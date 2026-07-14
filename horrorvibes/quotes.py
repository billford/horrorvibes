"""Horror movie quote generation via an OpenAI chat model, with dedup.

The chat client is injected by the caller (see ``generate_quotes``) so
tests never make a real network call -- they pass a fake object whose
``chat.completions.create`` returns a canned response.
"""

from __future__ import annotations

import logging
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from horrorvibes.config import QuotesConfig
from horrorvibes.exceptions import QuoteGenerationError

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    """Minimal shape of the OpenAI client surface this module needs."""

    chat: Any


@dataclass(frozen=True)
class QuoteRequest:
    """Pure description of one chat completion request, for easy testing."""

    model: str
    system_prompt: str
    user_prompt: str
    temperature: float = 1.0
    top_p: float = 0.9


def normalize_quote(quote: str) -> str:
    """Normalize a quote for duplicate comparison (case/quote-char insensitive)."""
    return quote.lower().replace('"', "").replace("'", "").strip()


def load_used_quotes(history_path: Path) -> set[str]:
    """Read previously-used, normalized quotes from the history file."""
    if not history_path.exists():
        return set()
    used: set[str] = set()
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            used.add(normalize_quote(line))
    return used


def build_quote_request(
    quotes_config: QuotesConfig,
    remaining: int,
    theme: str,
    seed: int,
    timestamp: str,
) -> QuoteRequest:
    """Build the chat request for one attempt. Pure function -- no I/O."""
    system_prompt = (
        "You are an expert on horror movies AND horror TV shows. "
        "Provide authentic, memorable quotes from horror films or horror television series -- "
        "both are equally welcome. "
        "Include only the quote and the title of the movie or show it's from. "
        "Format as: 'QUOTE' - TITLE (YEAR). "
        "Accuracy matters more than novelty: only provide a quote if you are highly "
        "confident it is word-for-word accurate and correctly attributed to that "
        "exact movie or show. Never paraphrase, invent, or guess a plausible-sounding "
        "quote, and never guess at a title/year you are not sure of. If you are not "
        "certain a quote is accurate and correctly attributed, choose a different, "
        "well-documented quote instead. Ensure each quote is unique and different "
        "from any you've provided before."
    )
    user_prompt = (
        f"Provide {remaining} different, authentic horror quotes (from movies or TV shows) "
        f"focusing on {theme}. Choose quotes that are impactful, memorable, and would look "
        f"good on a dramatic background. Random seed: {seed}, timestamp: {timestamp}. "
        "Prefer variety over repeating the most famous lines, but never at the cost "
        "of accuracy -- do not invent a quote or misattribute one just to seem less "
        "common."
    )
    return QuoteRequest(
        model=quotes_config.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=quotes_config.temperature,
    )


def _call_chat_client(client: ChatClient, request: QuoteRequest) -> str:
    response = client.chat.completions.create(
        model=request.model,
        messages=[
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
        temperature=request.temperature,
        top_p=request.top_p,
    )
    return response.choices[0].message.content or ""


def generate_quotes(  # pylint: disable=too-many-locals
    client: ChatClient,
    quotes_config: QuotesConfig,
    count: int,
    history_path: Path,
    rng: random.Random | None = None,
    now: Callable[[], datetime] | None = None,
) -> list[str]:
    """Generate up to ``count`` unique horror quotes not present in history.

    Raises QuoteGenerationError only if zero unique quotes could be produced
    after all attempts -- a partial result (fewer than requested) is
    returned with a warning logged, matching the original tool's behavior.
    """
    rng = rng or random.Random()  # nosec B311 - varies the chat prompt's theme/seed, not security-sensitive
    now = now or datetime.now

    used_quotes = load_used_quotes(history_path)
    logger.info("Found %d previously used quotes", len(used_quotes))

    new_quotes: list[str] = []
    for attempt in range(1, quotes_config.max_attempts + 1):
        if len(new_quotes) >= count:
            break

        theme = rng.choice(quotes_config.themes)
        seed = rng.randint(1, 100_000)
        timestamp = now().strftime("%Y%m%d%H%M%S%f")
        request = build_quote_request(quotes_config, count - len(new_quotes), theme, seed, timestamp)

        logger.info("Attempt %d/%d requesting quotes (theme=%s)", attempt, quotes_config.max_attempts, theme)
        try:
            content = _call_chat_client(client, request)
        except Exception:  # pylint: disable=broad-exception-caught
            # A single flaky attempt must not abort the whole run -- log and retry.
            logger.warning("Chat completion request failed on attempt %d", attempt, exc_info=True)
            continue

        for line in (line.strip() for line in content.split("\n") if line.strip()):
            if len(new_quotes) >= count:
                break
            normalized = normalize_quote(line)
            if normalized in used_quotes:
                logger.debug("Skipped duplicate quote: %.50s", line)
                continue
            new_quotes.append(line)
            used_quotes.add(normalized)

    if not new_quotes:
        raise QuoteGenerationError(
            f"Failed to generate any unique quotes after {quotes_config.max_attempts} attempts"
        )

    if len(new_quotes) < count:
        logger.warning("Only got %d unique quotes out of %d requested", len(new_quotes), count)

    return new_quotes


def write_quote_files(quotes_dir: Path, quotes: list[str]) -> list[Path]:
    """Clear stale quote_*.txt files and write the new set. Returns their paths."""
    quotes_dir.mkdir(parents=True, exist_ok=True)
    for stale in quotes_dir.glob("quote_*.txt"):
        stale.unlink()

    paths = []
    for i, quote in enumerate(quotes, start=1):
        path = quotes_dir / f"quote_{i}.txt"
        path.write_text(quote, encoding="utf-8")
        paths.append(path)
    return paths


def append_quote_history(history_path: Path, quotes: list[str]) -> None:
    """Atomically append new quotes to the history file (write-temp + rename)."""
    if not quotes:
        return

    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    new_content = existing + "".join(f"{quote}\n" for quote in quotes)

    fd, tmp_name = tempfile.mkstemp(dir=str(history_path.parent), prefix=".quotes_history_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(new_content)
        os.replace(tmp_name, history_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
