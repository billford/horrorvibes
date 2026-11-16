"""Horror quote generation via the OpenAI Responses API, with diversity
steering, fuzzy dedup, and optional web-search verification.

How it avoids repetition (see docs/specs/v3-diversity.md for the why):

- **Slot briefs**: every quote is requested against its own randomly
  sampled (decade, subgenre, region, medium) brief, so a batch spans eras
  and corners of the genre instead of collapsing onto the same 30 films.
- **Exclusions up front**: recently used titles and quotes are listed in
  the prompt, rather than only being filtered out after the fact.
- **Robust keys**: titles are compared without year/article/punctuation,
  quotes by fuzzy similarity of their text alone.

The client is injected by the caller so tests never make a network call --
they pass a fake whose ``responses.create`` returns canned JSON.

Output is still a list of ``"QUOTE" - TITLE (YEAR)`` strings, so every
downstream module (imagegen, compositor, voiceover, metadata, catalog) is
unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Protocol

from horrorvibes.config import QuotesConfig
from horrorvibes.exceptions import QuoteGenerationError
from horrorvibes.textutil import split_quote_and_title

logger = logging.getLogger(__name__)

# Earliest decade each medium can plausibly be drawn from.
_EARLIEST_TV_DECADE = 1950

_CONFIDENCE_LEVELS = ("high", "medium", "low")

QUOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "integer"},
                    "quote": {"type": "string"},
                    "title": {"type": "string"},
                    "year": {"type": "integer"},
                    "medium": {"type": "string", "enum": ["film", "tv"]},
                    "speaker": {"type": "string"},
                    "confidence": {"type": "string", "enum": list(_CONFIDENCE_LEVELS)},
                },
                "required": ["slot", "quote", "title", "year", "medium", "speaker", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["quotes"],
    "additionalProperties": False,
}

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "accurate": {"type": "boolean"},
                    "correct_year": {"type": ["integer", "null"]},
                    "note": {"type": "string"},
                },
                "required": ["index", "accurate", "correct_year", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You are a meticulous horror film and television historian. You supply real, word-for-word "
    "lines of dialogue from horror movies and horror TV series, correctly attributed.\n"
    "Rules, in priority order:\n"
    "1. Accuracy beats everything. Only give a line you are confident is spoken, essentially "
    "verbatim, in the named title. Never paraphrase, invent, merge two lines, or guess a title "
    "or year. Rate your honest confidence for each line.\n"
    "2. Each slot gives a decade, subgenre, region and medium. Match it as closely as you can. "
    "You may relax region first, then decade, if you have no confident line that fits -- never "
    "relax rule 1 to fit a slot.\n"
    "3. Every line must come from a different title, and none from the excluded titles or "
    "excluded lines.\n"
    "4. Prefer lines that stand alone out of context, land in under 25 words, and would read "
    "well over a dark image. When a film has one overwhelmingly famous line, prefer another "
    "memorable line from it if you know one accurately.\n"
    "Give the quote text without surrounding quotation marks."
)

_VERIFY_PROMPT = (
    "You fact-check horror movie and TV quotes. For each numbered item, use web search to "
    "confirm the line is actually spoken, essentially word for word, in the named title. "
    "Set accurate=false if you cannot find support, if the wording differs materially, or if it "
    "comes from a different work. If the line is right but the year is wrong, set correct_year "
    "to the right year; otherwise null. Keep each note to one short sentence."
)


class ResponsesClient(Protocol):
    """Minimal shape of the OpenAI client surface this module needs."""

    responses: Any


@dataclass(frozen=True)
class SlotBrief:
    """One quote's diversity target."""

    decade: int
    subgenre: str
    region: str
    medium: str

    def describe(self) -> str:
        """Human-readable brief for the prompt."""
        kind = "TV series" if self.medium == "tv" else "film"
        return f"{self.decade}s {self.region} {kind}, {self.subgenre}"


@dataclass(frozen=True)
class QuoteCandidate:
    """One structured quote as returned by the model."""

    quote: str
    title: str
    year: int
    medium: str
    speaker: str
    confidence: str

    def to_line(self) -> str:
        """Legacy ``"QUOTE" - TITLE (YEAR)`` form used throughout the pipeline.

        A literal " - " inside the quote would be mistaken for the title
        separator downstream, so it becomes an em dash."""
        text = self.quote.strip().strip("\"'“”‘’").replace(" - ", " — ")
        title = self.title.strip()
        return f'"{text}" - {title} ({self.year})' if self.year else f'"{text}" - {title}'


# ---- normalization & history ------------------------------------------------


def quote_key(quote_text: str) -> str:
    """Comparison key for a quote's *text only*: lowercase, punctuation
    stripped, whitespace collapsed, and stretched letters squashed (so
    "Heeeere's Johnny" and "Here's Johnny!" collide)."""
    text = quote_text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text).replace("'", "")
    text = re.sub(r"(.)\1{2,}", r"\1", text)
    return " ".join(text.split())


def normalize_quote(quote: str) -> str:
    """Comparison key for a full ``QUOTE - TITLE`` line (title is ignored)."""
    return quote_key(split_quote_and_title(quote)[0])


def normalize_movie(movie: str) -> str:
    """Comparison key for a title: ``"The Shining (1980)"``, ``"Shining"``
    and ``"the shining"`` all map to ``"shining"``. Idempotent, so keys
    written by older versions re-normalize to the same value."""
    text = re.sub(r"\(.*?\)", " ", movie.lower())
    text = re.sub(r",\s*(the|a|an)\s*$", "", text.strip())
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = " ".join(text.split())
    return re.sub(r"^(the|a|an) ", "", text)


def is_near_duplicate(key: str, used_keys: list[str] | set[str], threshold: float) -> bool:
    """True if ``key`` matches any used key exactly or with a similarity
    ratio at or above ``threshold``."""
    if not key:
        return True
    if key in used_keys:
        return True
    for used in used_keys:
        matcher = SequenceMatcher(None, key, used)
        if matcher.real_quick_ratio() >= threshold and matcher.quick_ratio() >= threshold:
            if matcher.ratio() >= threshold:
                return True
    return False


def load_history_lines(history_path: Path) -> list[str]:
    """Raw previously-used quote lines, oldest first."""
    if not history_path.exists():
        return []
    return [line.strip() for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_used_quotes(history_path: Path) -> set[str]:
    """Previously-used quote keys (see ``quote_key``)."""
    return {normalize_quote(line) for line in load_history_lines(history_path)}


def load_recent_movies(movie_history_path: Path, now: datetime, cooldown_days: float) -> set[str]:
    """Normalized titles used within the last ``cooldown_days``. Older
    entries are fair game again. Entries written by older versions are
    re-normalized so they still match."""
    if not movie_history_path.exists():
        return set()

    cutoff = now - timedelta(days=cooldown_days)
    recent: set[str] = set()
    for line in movie_history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        if datetime.fromisoformat(entry["timestamp"]) >= cutoff:
            recent.add(normalize_movie(entry["movie"]))
    return recent


def _atomic_append(path: Path, new_text: str, prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(existing + new_text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def append_movie_history(movie_history_path: Path, movies: list[str], timestamp: datetime) -> None:
    """Atomically append one (normalized title, timestamp) record per title.
    "Unknown" (an unparseable title) is never recorded."""
    entries = [
        {"movie": normalize_movie(movie), "timestamp": timestamp.isoformat()}
        for movie in movies
        if movie != "Unknown" and normalize_movie(movie)
    ]
    if entries:
        _atomic_append(
            movie_history_path, "".join(json.dumps(entry) + "\n" for entry in entries), ".movie_history_"
        )


def append_quote_history(history_path: Path, quotes: list[str]) -> None:
    """Atomically append new quote lines to the history file."""
    if quotes:
        _atomic_append(history_path, "".join(f"{quote}\n" for quote in quotes), ".quotes_history_")


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


# ---- diversity briefs ---------------------------------------------------------


def build_slot_briefs(quotes_config: QuotesConfig, count: int, rng: random.Random) -> list[SlotBrief]:
    """Sample ``count`` briefs. Subgenres are drawn without replacement
    (cycling only once the pool is exhausted), so no two slots in a batch
    share a decade+subgenre pair."""
    regions = list(quotes_config.regions)
    region_weights = [float(quotes_config.regions[r]) for r in regions]

    subgenre_order: list[str] = []
    briefs: list[SlotBrief] = []
    seen_pairs: set[tuple[int, str]] = set()
    for _ in range(count):
        if not subgenre_order:
            subgenre_order = rng.sample(quotes_config.themes, len(quotes_config.themes))
        subgenre = subgenre_order.pop()

        medium = "tv" if rng.random() < quotes_config.tv_share else "film"
        decades = quotes_config.decades
        if medium == "tv":
            decades = [d for d in decades if d >= _EARLIEST_TV_DECADE] or decades
        open_decades = [d for d in decades if (d, subgenre) not in seen_pairs] or decades
        decade = rng.choice(open_decades)
        seen_pairs.add((decade, subgenre))

        region = rng.choices(regions, weights=region_weights, k=1)[0]
        briefs.append(SlotBrief(decade=decade, subgenre=subgenre, region=region, medium=medium))
    return briefs


# ---- requests -------------------------------------------------------------------


@dataclass(frozen=True)
class QuoteRequest:
    """Pure description of one Responses API request, for easy testing."""

    model: str
    instructions: str
    input: str
    schema_name: str
    schema: dict[str, Any]
    temperature: float | None = None
    reasoning_effort: str | None = None
    tools: tuple[dict[str, Any], ...] = ()

    def to_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``client.responses.create``."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": self.instructions,
            "input": self.input,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": self.schema_name,
                    "schema": self.schema,
                    "strict": True,
                }
            },
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if self.tools:
            kwargs["tools"] = list(self.tools)
        return kwargs


def build_quote_request(
    quotes_config: QuotesConfig,
    briefs: list[SlotBrief],
    excluded_titles: list[str],
    excluded_quotes: list[str],
) -> QuoteRequest:
    """Build the generation request for one attempt. Pure function -- no I/O."""
    lines = [f"Give exactly {len(briefs)} quotes, one per slot:"]
    lines += [f"{i}. {brief.describe()}" for i, brief in enumerate(briefs, start=1)]
    if excluded_titles:
        lines += ["", "Excluded titles (used recently -- do not quote these):", "; ".join(excluded_titles)]
    if excluded_quotes:
        lines += ["", "Excluded lines (already used -- do not repeat or reword these):"]
        lines += [f"- {quote}" for quote in excluded_quotes]
    return QuoteRequest(
        model=quotes_config.model,
        instructions=_SYSTEM_PROMPT,
        input="\n".join(lines),
        schema_name="horror_quotes",
        schema=QUOTES_SCHEMA,
        temperature=quotes_config.temperature,
        reasoning_effort=quotes_config.reasoning_effort,
    )


def build_verify_request(quotes_config: QuotesConfig, candidates: list[QuoteCandidate]) -> QuoteRequest:
    """Build the web-search verification request. Pure function -- no I/O."""
    items = [
        f'{i}. "{c.quote}" -- {c.speaker or "unknown speaker"}, {c.title} ({c.year}, {c.medium})'
        for i, c in enumerate(candidates, start=1)
    ]
    return QuoteRequest(
        model=quotes_config.verify_model or quotes_config.model,
        instructions=_VERIFY_PROMPT,
        input="\n".join(items),
        schema_name="quote_verdicts",
        schema=VERIFY_SCHEMA,
        reasoning_effort=quotes_config.reasoning_effort,
        tools=({"type": "web_search"},),
    )


def _call(client: ResponsesClient, request: QuoteRequest) -> dict[str, Any]:
    response = client.responses.create(**request.to_kwargs())
    return json.loads(response.output_text or "{}")


def parse_candidates(payload: dict[str, Any]) -> list[QuoteCandidate]:
    """Turn a schema-conforming payload into candidates, dropping malformed items."""
    candidates = []
    for item in payload.get("quotes", []):
        try:
            candidates.append(
                QuoteCandidate(
                    quote=str(item["quote"]).strip(),
                    title=str(item["title"]).strip(),
                    year=int(item.get("year") or 0),
                    medium=str(item.get("medium", "film")),
                    speaker=str(item.get("speaker", "")).strip(),
                    confidence=str(item.get("confidence", "low")),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.debug("Dropped malformed quote item: %r", item)
    return candidates


def verify_candidates(
    client: ResponsesClient, quotes_config: QuotesConfig, candidates: list[QuoteCandidate]
) -> list[QuoteCandidate]:
    """Keep only candidates the web-search check confirms, applying any
    corrected year. If the verification call itself fails, the candidates
    are returned unchanged (logged) so an unattended run never dies here.
    A candidate with no verdict at all is dropped."""
    if not candidates:
        return []
    try:
        payload = _call(client, build_verify_request(quotes_config, candidates))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Quote verification failed; keeping %d unverified quotes", len(candidates), exc_info=True
        )
        return candidates

    verdicts = {int(v["index"]): v for v in payload.get("verdicts", []) if "index" in v}
    kept = []
    for i, candidate in enumerate(candidates, start=1):
        verdict = verdicts.get(i)
        if verdict is None or not verdict.get("accurate"):
            note = verdict.get("note", "") if verdict else "no verdict returned"
            logger.info("Dropped unverified quote %r (%s): %s", candidate.quote[:50], candidate.title, note)
            continue
        correct_year = verdict.get("correct_year")
        if correct_year and int(correct_year) != candidate.year:
            logger.info("Corrected year for %s: %s -> %s", candidate.title, candidate.year, correct_year)
            candidate = QuoteCandidate(**{**candidate.__dict__, "year": int(correct_year)})
        kept.append(candidate)
    return kept


def _filter_candidates(
    candidates: list[QuoteCandidate],
    accepted_confidence: set[str],
    avoided_movies: set[str],
    used_keys: set[str],
    threshold: float,
) -> list[QuoteCandidate]:
    """Drop low-confidence, untitled, recently-used-title, same-title-in-batch
    and near-duplicate candidates."""
    batch: list[QuoteCandidate] = []
    batch_movies: set[str] = set()
    for candidate in candidates:
        movie = normalize_movie(candidate.title)
        if candidate.confidence not in accepted_confidence:
            logger.debug("Skipped %s-confidence quote from %s", candidate.confidence, candidate.title)
        elif not movie:
            logger.debug("Skipped quote with no title: %.50s", candidate.quote)
        elif movie in avoided_movies or movie in batch_movies:
            logger.debug("Skipped quote from a recently-used title: %s", candidate.title)
        elif is_near_duplicate(quote_key(candidate.quote), used_keys, threshold):
            logger.debug("Skipped near-duplicate quote: %.50s", candidate.quote)
        else:
            batch.append(candidate)
            batch_movies.add(movie)
    return batch


# ---- main entry -----------------------------------------------------------------


def generate_quotes(  # pylint: disable=too-many-arguments,too-many-locals
    client: ResponsesClient,
    quotes_config: QuotesConfig,
    count: int,
    history_path: Path,
    rng: random.Random | None = None,
    now: Callable[[], datetime] | None = None,  # pylint: disable=unused-argument
    recent_movies: set[str] | None = None,
) -> list[str]:
    """Generate up to ``count`` fresh horror quotes, each from a distinct
    title not used recently, none a near-duplicate of a past quote.

    Raises QuoteGenerationError only if zero quotes survive every attempt;
    a partial result is returned with a warning.
    """
    rng = rng or random.Random()  # nosec B311 - steers prompt variety, not security-sensitive

    history_lines = load_history_lines(history_path)
    used_keys = {normalize_quote(line) for line in history_lines}
    logger.info("Found %d previously used quotes", len(used_keys))

    avoided_movies = {normalize_movie(m) for m in recent_movies} if recent_movies else set()
    recent_quote_texts = [split_quote_and_title(line)[0] for line in history_lines]
    accepted_confidence = {"high", "medium"} if quotes_config.verify else {"high"}

    accepted: list[QuoteCandidate] = []
    for attempt in range(1, quotes_config.max_attempts + 1):
        remaining = count - len(accepted)
        if remaining <= 0:
            break

        limit = quotes_config.exclusion_prompt_limit
        request = build_quote_request(
            quotes_config,
            build_slot_briefs(quotes_config, remaining, rng),
            excluded_titles=sorted(avoided_movies)[:limit],
            excluded_quotes=(recent_quote_texts + [c.quote for c in accepted])[-limit:] if limit else [],
        )
        logger.info("Attempt %d/%d requesting %d quotes", attempt, quotes_config.max_attempts, remaining)
        try:
            candidates = parse_candidates(_call(client, request))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Quote request failed on attempt %d", attempt, exc_info=True)
            continue

        batch = _filter_candidates(
            candidates, accepted_confidence, avoided_movies, used_keys, quotes_config.fuzzy_dup_threshold
        )
        if quotes_config.verify:
            batch = verify_candidates(client, quotes_config, batch)

        for candidate in batch[:remaining]:
            accepted.append(candidate)
            used_keys.add(quote_key(candidate.quote))
            avoided_movies.add(normalize_movie(candidate.title))

    if not accepted:
        raise QuoteGenerationError(
            f"Failed to generate any unique quotes after {quotes_config.max_attempts} attempts"
        )
    if len(accepted) < count:
        logger.warning("Only got %d unique quotes out of %d requested", len(accepted), count)

    return [candidate.to_line() for candidate in accepted]
