"""Per-run creative plan: which style pack, which episode format, how many
quotes, and what the video is called.

Packs rotate least-recently-used (ties broken at random) using an
append-only history, so consecutive videos never share a look and every
pack comes round before any repeats. Themed formats are likewise picked
least-recently-used among themselves.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from horrorvibes.config import Config, EpisodeFormat, QuotesConfig, StylePack
from horrorvibes.quotes import _atomic_append

logger = logging.getLogger(__name__)

MIXED = "mixed"


@dataclass(frozen=True)
class EpisodePlan:
    """Everything a run needs to know about its creative direction."""

    pack: StylePack
    format_name: str
    headline_template: str
    quote_count: int
    quotes_config: QuotesConfig
    instruction: str | None = None

    def headline(self, actual_count: int) -> str:
        """The video's headline, using the number of quotes actually produced."""
        return self.headline_template.format(n=actual_count)


def load_history(history_path: Path) -> list[dict]:
    """Past episode records, oldest first. Unparseable lines are skipped."""
    if not history_path.exists():
        return []
    records = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping unparseable episode history line: %.80s", line)
    return records


def _last_used_index(history: list[dict], key: str) -> dict[str, int]:
    return {record[key]: i for i, record in enumerate(history) if key in record}


def choose_least_recent(names: list[str], history: list[dict], key: str, rng: random.Random) -> str:
    """Pick a name never used, else the one used longest ago. Ties at random."""
    last_used = _last_used_index(history, key)
    oldest = min(last_used.get(name, -1) for name in names)
    candidates = [name for name in names if last_used.get(name, -1) == oldest]
    return rng.choice(candidates)


def apply_format(quotes_config: QuotesConfig, fmt: EpisodeFormat) -> QuotesConfig:
    """Narrow the quote briefs to a themed format's constraints."""
    overrides: dict = {}
    if fmt.themes:
        overrides["themes"] = list(fmt.themes)
    if fmt.decades:
        overrides["decades"] = list(fmt.decades)
    if fmt.regions:
        overrides["regions"] = dict(fmt.regions)
    if fmt.tv_share is not None:
        overrides["tv_share"] = fmt.tv_share
    return dataclasses.replace(quotes_config, **overrides) if overrides else quotes_config


def plan_episode(
    config: Config,
    rng: random.Random | None = None,
    quote_count_override: int | None = None,
) -> EpisodePlan:
    """Decide this run's pack, format, length and headline."""
    rng = rng or random.Random()  # nosec B311 - creative variety, not security-sensitive
    episodes = config.episodes
    history = load_history(episodes.history_path) if episodes else []

    packs = {pack.name: pack for pack in config.style_packs}
    pack = packs[choose_least_recent(list(packs), history, "pack", rng)]

    if episodes is None or not episodes.enabled:
        count = quote_count_override or config.run.quote_count
        return EpisodePlan(pack, MIXED, "{n} Bone-Chilling Horror Quotes", count, config.quotes)

    count = quote_count_override or rng.randint(episodes.quote_count_min, episodes.quote_count_max)

    if episodes.formats and rng.random() < episodes.themed_share:
        formats = {fmt.name: fmt for fmt in episodes.formats}
        fmt = formats[choose_least_recent(list(formats), history, "format", rng)]
        plan = EpisodePlan(
            pack=pack,
            format_name=fmt.name,
            headline_template=fmt.headline,
            quote_count=count,
            quotes_config=apply_format(config.quotes, fmt),
            instruction=fmt.instruction,
        )
    else:
        plan = EpisodePlan(pack, MIXED, rng.choice(episodes.mixed_headlines), count, config.quotes)

    logger.info("Episode plan: pack=%s format=%s quotes=%d", plan.pack.name, plan.format_name, count)
    return plan


def record_episode(history_path: Path, plan: EpisodePlan, timestamp: datetime) -> None:
    """Append this run's pack + format so the next run rotates away from them."""
    record = {"timestamp": timestamp.isoformat(), "pack": plan.pack.name, "format": plan.format_name}
    _atomic_append(history_path, json.dumps(record) + "\n", ".episode_history_")
