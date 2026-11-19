"""Story Shorts: pick a real legend, research it, write a short script, and check every claim.

See docs/specs/story-shorts.md. The flow per video is:

    propose topics -> filter repeats and off-limits subjects -> research (web search)
      -> write from the research only -> check each claim (web search) -> rewrite once if needed

Anything that can't be backed by at least ``min_sources`` sources, or still has a wrong or unsupported
claim after one rewrite, is dropped and the next topic is tried. Nothing here renders or uploads; the
result is a ``Story`` plus an audit record of its sources and verified claims.

Like quotes.py, the OpenAI client is injected so tests run against canned JSON.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from horrorvibes.config import StoriesConfig
from horrorvibes.exceptions import QuoteGenerationError
from horrorvibes.quotes import (
    DELIVERY_TAGS,
    QuoteRequest,
    ResponsesClient,
    _atomic_append,
    _call,
    has_strong_profanity,
)

logger = logging.getLogger(__name__)


class StoryGenerationError(QuoteGenerationError):
    """No topic survived research and fact-checking."""


# Backstop for subjects YouTube treats as harmful misinformation or that target people.
# The prompts already exclude them; this catches a slip.
_OFF_LIMITS = re.compile(
    r"\b(election|ballot|voter fraud|vaccin\w*|covid|pandemic|5g|cancer cure|chemtrail\w*|"
    r"qanon|pizzagate|adrenochrome|replacement theory|zionis\w*|protocols of|holocaust|"
    r"sandy hook|school shooting|9/11 victims?)\b",
    re.IGNORECASE,
)

_CONTENT_RULES = (
    "Content rules:\n"
    "- Real, documented legends, mysteries and historical incidents only.\n"
    "- Never accuse a named, living private person of a crime or misconduct.\n"
    "- Exclude elections, vaccines and medical claims, ongoing disasters, events less than about 25 "
    "years old with grieving families, and any theory that targets an ethnic, religious or other "
    "protected group.\n"
    "- No graphic violence or gore in the telling.\n"
)

# ---- schemas ----------------------------------------------------------------------------------

_STR = {"type": "string"}

TOPICS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": _STR, "place": _STR, "era": _STR, "pitch": _STR},
                "required": ["name", "place", "era", "pitch"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "legend": _STR,
        "record": _STR,
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"fact": _STR, "source_url": _STR},
                "required": ["fact", "source_url"],
                "additionalProperties": False,
            },
        },
        "names": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": _STR, "respelling": _STR, "ipa": _STR},
                "required": ["name", "respelling", "ipa"],
                "additionalProperties": False,
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": _STR, "url": _STR},
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["found", "legend", "record", "facts", "names", "sources"],
    "additionalProperties": False,
}

SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": _STR,
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": _STR,
                    "visual_scene": _STR,
                    "delivery": {"type": "string", "enum": list(DELIVERY_TAGS)},
                },
                "required": ["narration", "visual_scene", "delivery"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "beats"],
    "additionalProperties": False,
}

CLAIMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": _STR,
                    "status": {"type": "string", "enum": ["supported", "unsupported", "wrong"]},
                    "correction": _STR,
                    "source_url": _STR,
                },
                "required": ["claim", "status", "correction", "source_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

# ---- data -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Topic:
    """One proposed story subject."""

    name: str
    place: str
    era: str
    pitch: str
    category: str


@dataclass(frozen=True)
class Research:
    """What the web-search research step found."""

    legend: str
    record: str
    facts: list[dict]
    names: list[dict]
    sources: list[dict]


@dataclass(frozen=True)
class Beat:
    """One image's worth of narration."""

    narration: str
    visual_scene: str
    delivery: str


@dataclass(frozen=True)
class Script:
    """A written story, ready to check."""

    title: str
    beats: list[Beat]

    @property
    def narration(self) -> str:
        """The whole narration as one text."""
        return " ".join(beat.narration for beat in self.beats)

    @property
    def word_count(self) -> int:
        """Words in the narration."""
        return len(self.narration.split())


@dataclass(frozen=True)
class Story:
    """A topic, its research, a script that passed the claim check, and the verified claims."""

    topic: Topic
    research: Research
    script: Script
    claims: list[dict] = field(default_factory=list)

    def description(self) -> str:
        """YouTube description: a one-line summary, the sources, and an AI disclosure."""
        lines = [self.topic.pitch, "", "Sources:"]
        lines += [f"- {src['title']}: {src['url']}" for src in self.research.sources]
        lines += ["", "Narration and images are AI-generated; facts are checked against the sources above."]
        return "\n".join(lines)


# ---- pure helpers -----------------------------------------------------------------------------


def normalize_topic(name: str) -> str:
    """Lower-case words only, without leading articles, for repeat checks."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    while words and words[0] in {"the", "a", "an"}:
        words = words[1:]
    return " ".join(words)


def is_repeat(topic: Topic, used: list[str], threshold: float = 0.85) -> bool:
    """True if the topic matches a used topic name or past upload title.

    A past title counts when the topic's normalized name appears inside it ("ss baychimo" in "the
    ship that refused to sink ss baychimo") or the two are near-identical.
    """
    key = normalize_topic(topic.name)
    if not key:
        return True
    for past in used:
        other = normalize_topic(past)
        if not other:
            continue
        if key in other or other in key or SequenceMatcher(None, key, other).ratio() >= threshold:
            return True
    return False


def is_off_limits(*texts: str) -> bool:
    """True if any text touches an excluded subject or contains strong profanity."""
    return any(_OFF_LIMITS.search(text) or has_strong_profanity(text) for text in texts)


def source_domains(sources: list[dict]) -> set[str]:
    """Distinct websites among the sources (``www.`` ignored)."""
    domains = set()
    for src in sources:
        host = urlparse(str(src.get("url", ""))).netloc.lower()
        if host:
            domains.add(host.removeprefix("www."))
    return domains


def pick_category(categories: dict[str, float], rng: random.Random) -> str:
    """Weighted random category."""
    names = [name for name, weight in categories.items() if weight > 0]
    return rng.choices(names, weights=[categories[name] for name in names])[0]


def load_used_topics(history_path: Path, snapshot_path: Path | None = None) -> list[str]:
    """Topic names already made by this pipeline plus every past upload title on the channel."""
    used: list[str] = []
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            try:
                used.append(json.loads(line)["topic"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    if snapshot_path and snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            used += [video["title"] for video in snapshot.get("videos", []) if video.get("title")]
        except (json.JSONDecodeError, TypeError):
            logger.warning("Could not read channel snapshot %s; past titles not excluded", snapshot_path)
    return used


def failed_claims(claims: list[dict]) -> list[dict]:
    """Claims the checker marked wrong or unsupported."""
    return [claim for claim in claims if claim.get("status") != "supported"]


# ---- requests ---------------------------------------------------------------------------------


def _request(cfg: StoriesConfig, instructions: str, text: str, name: str, schema: dict, search: bool):
    return QuoteRequest(
        model=cfg.model,
        instructions=instructions,
        input=text,
        schema_name=name,
        schema=schema,
        reasoning_effort=cfg.reasoning_effort,
        tools=({"type": "web_search"},) if search else (),
    )


def build_topics_request(cfg: StoriesConfig, category: str, used: list[str]) -> QuoteRequest:
    """Ask for candidate topics in one category, excluding everything already covered."""
    text = "\n".join(
        [
            f"Category: {category}",
            f"Propose {cfg.topic_candidates} distinct topics.",
            "",
            "Already covered on the channel (do not propose these or the same story under another name):",
            *[f"- {title}" for title in used[-600:]],
        ]
    )
    instructions = (
        "You pick subjects for under-a-minute YouTube Shorts on a channel about real legends, "
        "mysteries and conspiracy stories. The channel's best videos each tell ONE specific, named "
        "story tied to a real place, such as a ghost ship, a set of mystery lights, a cursed doll or "
        "one documented incident. Broad theories (secret societies, faked moon landings, hidden "
        "civilisations) do badly. Propose lesser-known but well-documented stories with a name, a "
        "place and an era, that a short video can tell accurately.\n"
        + _CONTENT_RULES
        + "For each topic give: name (the searchable name of the story), place, era, and a one-"
        "sentence pitch."
    )
    return _request(cfg, instructions, text, "story_topics", TOPICS_SCHEMA, search=False)


def build_research_request(cfg: StoriesConfig, topic: Topic) -> QuoteRequest:
    """Web-search research brief for one topic."""
    instructions = (
        "You research a story for a short documentary narration. Use web search. Prefer primary "
        "records, newspapers, museums, government or academic sources, and well-sourced references "
        "over blogs and video sites. Separate what people claim (the legend) from what the records "
        "show. Every fact must name the URL it came from. For every unusual proper noun a narrator "
        "could mispronounce (people, places, ships, creatures; skip common words like Britain or "
        "BBC), give an easy respelling for pronunciation (e.g. "
        "'Chir-Batti: cheer BAH-tee') and IPA if a source gives it, else an empty string. "
        "Set found=false if you cannot find reliable sources for the story."
    )
    text = f"Story: {topic.name}\nPlace: {topic.place}\nEra: {topic.era}\nPitch: {topic.pitch}"
    return _request(cfg, instructions, text, "story_research", RESEARCH_SCHEMA, search=True)


def build_script_request(
    cfg: StoriesConfig, topic: Topic, research: Research, corrections: list[dict] | None = None
) -> QuoteRequest:
    """Write the narration from the research brief only, optionally fixing failed claims."""
    instructions = (
        "You write narration for a YouTube Short about a real legend or mystery. Use ONLY the facts "
        "in the research brief; add nothing that is not in it. Structure: a gripping first sentence "
        "that makes someone stop scrolling, then the legend as people tell it, then what the record "
        "actually shows, then a quiet, eerie closing line. Present claims as claims ('the story "
        "goes', 'witnesses said', 'believers point to'), never as fact, and never as mockery. "
        f"Total length {cfg.words_min}-{cfg.words_max} words, split into {cfg.beats_min}-{cfg.beats_max} "
        "beats. Each beat gets: narration; a visual_scene for one image (the real place, era, objects "
        "and weather; people only as silhouettes or from behind; no real faces, logos, text, borders "
        "or gore); and a delivery tag.\n"
        "Write it as a story told aloud at night, not a report: short sentences, concrete sensory "
        "detail from the facts, at most two numbers or dates in the whole script, and never mention "
        "articles, sources, researchers' caveats or 'records' in a bureaucratic way -- the "
        "description lists sources. Keep the uncertainty, but say it like a storyteller ('no one "
        "has ever explained it').\n"
        "Title: the story's searchable name, a colon, then a short evocative hook naming the place "
        "if it fits, e.g. 'The SS Baychimo: The Ghost Ship That Wouldn't Sink' or 'Chir-Batti: The "
        "Mysterious Desert Lights of Gujarat'. No question, no hashtags, under 70 characters.\n"
        + _CONTENT_RULES
    )
    lines = [
        f"Story: {topic.name} ({topic.place}, {topic.era})",
        "",
        f"Legend: {research.legend}",
        f"Record: {research.record}",
        "Facts:",
        *[f"- {fact['fact']}" for fact in research.facts],
    ]
    if corrections:
        lines += ["", "A fact-check failed these claims in your last draft. Fix or remove each one:"]
        lines += [f"- {c['claim']} -> {c['status']}: {c.get('correction', '')}" for c in corrections]
    return _request(cfg, instructions, "\n".join(lines), "story_script", SCRIPT_SCHEMA, search=False)


def build_claims_request(cfg: StoriesConfig, script: Script, research: Research) -> QuoteRequest:
    """Extract and check every factual claim in the script, by web search."""
    instructions = (
        "You fact-check narration for a documentary Short. List every factual claim in it: names, "
        "dates, places, numbers, events, and who said or saw what. Statements clearly framed as a "
        "legend or belief ('the story goes...') are claims about what people say -- check that "
        "people do say it. For each claim, use web search and the listed sources, and mark it "
        "supported, unsupported (no reliable source found) or wrong (a source contradicts it), with "
        "a correction and the source URL. Be strict about dates, numbers and names. Statements of "
        "mood or open uncertainty ('it has never been explained', 'no one knows') are not claims "
        "unless a source shows the question was in fact settled."
    )
    text = "\n".join(
        ["Narration:", script.narration, "", "Sources:"]
        + [f"- {src['title']}: {src['url']}" for src in research.sources]
    )
    return _request(cfg, instructions, text, "story_claims", CLAIMS_SCHEMA, search=True)


# ---- parsing ----------------------------------------------------------------------------------


def parse_topics(payload: dict, category: str) -> list[Topic]:
    """Topics from a schema payload; malformed items are dropped."""
    topics = []
    for item in payload.get("topics", []):
        try:
            topics.append(
                Topic(
                    name=str(item["name"]).strip(),
                    place=str(item["place"]).strip(),
                    era=str(item["era"]).strip(),
                    pitch=str(item["pitch"]).strip(),
                    category=category,
                )
            )
        except (KeyError, TypeError):
            continue
    return [topic for topic in topics if topic.name]


def parse_research(payload: dict) -> Research | None:
    """The research brief, or None if the model found nothing reliable."""
    if not payload.get("found"):
        return None
    return Research(
        legend=str(payload.get("legend", "")),
        record=str(payload.get("record", "")),
        facts=list(payload.get("facts") or []),
        names=list(payload.get("names") or []),
        sources=list(payload.get("sources") or []),
    )


def parse_script(payload: dict) -> Script:
    """The written script."""
    beats = [
        Beat(
            narration=str(beat.get("narration", "")).strip(),
            visual_scene=str(beat.get("visual_scene", "")).strip(),
            delivery=str(beat.get("delivery", "")).strip(),
        )
        for beat in payload.get("beats", [])
    ]
    return Script(title=str(payload.get("title", "")).strip(), beats=[b for b in beats if b.narration])


def script_problems(cfg: StoriesConfig, script: Script) -> list[str]:
    """Shape problems that make a script unusable, before any fact-check is spent on it."""
    problems = []
    if not script.title:
        problems.append("no title")
    if not cfg.beats_min <= len(script.beats) <= cfg.beats_max:
        problems.append(f"{len(script.beats)} beats")
    # 15% slack either way: the model counts words loosely, and a few words don't change the length much.
    if not cfg.words_min * 0.85 <= script.word_count <= cfg.words_max * 1.15:
        problems.append(f"{script.word_count} words")
    if "?" in script.title or "#" in script.title:
        problems.append("question or hashtag in title")
    if is_off_limits(script.title, script.narration):
        problems.append("off-limits subject or profanity")
    return problems


# ---- main entry -------------------------------------------------------------------------------


def _write_and_check(client: ResponsesClient, cfg: StoriesConfig, topic: Topic, research: Research):
    """Write, check, and rewrite once. Returns (script, claims) or None if it never passed."""
    corrections: list[dict] | None = None
    for attempt in (1, 2):
        script = parse_script(_call(client, build_script_request(cfg, topic, research, corrections)))
        problems = script_problems(cfg, script)
        if problems:
            logger.info("Script for %s rejected (attempt %d): %s", topic.name, attempt, ", ".join(problems))
            corrections = None
            continue
        claims = list(_call(client, build_claims_request(cfg, script, research)).get("claims") or [])
        failed = failed_claims(claims)
        if not claims:
            logger.info("Claim check for %s returned no claims (attempt %d)", topic.name, attempt)
            continue
        if not failed:
            return script, claims
        logger.info(
            "Claim check failed %d/%d claims for %s (attempt %d): %s",
            len(failed), len(claims), topic.name, attempt,
            "; ".join(f"{c['claim'][:60]} [{c['status']}]" for c in failed),
        )
        corrections = failed
    return None


def generate_story(
    client: ResponsesClient,
    cfg: StoriesConfig,
    rng: random.Random | None = None,
    used: list[str] | None = None,
) -> Story:
    """Pick, research, write and fact-check one story, or raise StoryGenerationError."""
    rng = rng or random.Random()  # nosec B311 - creative variety, not security-sensitive
    used = list(used) if used is not None else load_used_topics(cfg.history_path, cfg.channel_snapshot_path)

    for round_number in range(1, cfg.max_topic_attempts + 1):
        category = pick_category(cfg.categories, rng)
        topics = parse_topics(_call(client, build_topics_request(cfg, category, used)), category)
        fresh = [t for t in topics if not is_repeat(t, used) and not is_off_limits(t.name, t.pitch)]
        logger.info(
            "Story round %d: %s -> %d topics, %d fresh", round_number, category, len(topics), len(fresh)
        )
        rng.shuffle(fresh)
        for topic in fresh[:2]:  # research is the expensive step; try at most two per round
            used.append(topic.name)
            research = parse_research(_call(client, build_research_request(cfg, topic)))
            if research is None:
                logger.info("No reliable research for %s", topic.name)
                continue
            domains = source_domains(research.sources)
            if len(domains) < cfg.min_sources:
                logger.info("Only %d source site(s) for %s; skipping", len(domains), topic.name)
                continue
            result = _write_and_check(client, cfg, topic, research)
            if result is None:
                continue
            script, claims = result
            logger.info(
                "Story ready: %s (%d words, %d claims checked)", script.title, script.word_count, len(claims)
            )
            return Story(topic=topic, research=research, script=script, claims=claims)

    raise StoryGenerationError(
        f"No story passed research and fact-checking in {cfg.max_topic_attempts} rounds"
    )


def save_audit(audit_dir: Path, story: Story, timestamp: datetime) -> Path:
    """Write the full story (sources, verified claims, script) for later review."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    slug = normalize_topic(story.topic.name).replace(" ", "_")[:60] or "story"
    path = audit_dir / f"{timestamp:%Y%m%d_%H%M%S}_{slug}.json"
    path.write_text(json.dumps(asdict(story), indent=1), encoding="utf-8")
    return path


def record_story(history_path: Path, story: Story, timestamp: datetime, video_id: str | None) -> None:
    """Append the topic so it is never picked again."""
    record = {
        "timestamp": timestamp.isoformat(),
        "topic": story.topic.name,
        "category": story.topic.category,
        "title": story.script.title,
        "youtube_video_id": video_id,
    }
    _atomic_append(history_path, json.dumps(record) + "\n", ".story_history_")
