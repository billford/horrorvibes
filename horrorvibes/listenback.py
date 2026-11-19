"""Listen back to generated narration and catch mispronounced names.

Each narration clip is transcribed with Whisper. Every unusual name that the script uses (from the
research step's pronunciation list) must turn up in the transcript as something that *sounds* like the
name or its respelling. Whisper misspells rare names even when they are said correctly, so names are
compared by a rough sound-alike key, not exact spelling: "Nyree" passes for "Ngaire" (respelled
NYE-ree), but "Okanagan" does not pass for "Ogopogo".

This catches garbled, swapped or skipped names. It cannot hear stress or vowel-quality mistakes
("Hashimoto" still passes for "Hashima"); the respellings sent to the voice are the guard for those.

When a name fails, the clip is regenerated with the name swapped for its respelling (``respell``). The
caller decides what to do if it still fails.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

Transcriber = Callable[[Path], str]

_SOUND_RULES = (
    ("ph", "f"), ("gh", "g"), ("ck", "k"), ("qu", "kw"), ("q", "k"), ("x", "ks"), ("z", "s"),
    ("c", "k"), ("y", "i"), ("w", "u"), ("v", "f"),
)


def fold(text: str) -> str:
    """Lower case, accents removed (Kaikōura -> kaikoura), letters, digits and spaces only."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9 ]+", " ", plain.lower()).strip()


def sound_key(text: str) -> str:
    """A rough sound-alike key: spelling variants merged, vowels after the first letter collapsed."""
    word = re.sub(r"[^a-z]", "", fold(text))
    for old, new in _SOUND_RULES:
        word = word.replace(old, new)
    if not word:
        return ""
    head, tail = word[0], re.sub(r"h(?![aeiou])", "", word[1:])
    tail = re.sub(r"[aeiou]+", "a", tail)
    return re.sub(r"(.)\1+", r"\1", head + tail)


def _close(a: str, b: str, threshold: float) -> bool:
    return bool(a and b) and SequenceMatcher(None, a, b).ratio() >= threshold


def name_heard(name: str, respelling: str, transcript: str, threshold: float = 0.72) -> bool:
    """True if some run of transcript words sounds like ``name`` or its ``respelling``."""
    targets = [sound_key(name), sound_key(respelling.replace("-", ""))]
    words = fold(transcript).split()
    span = max(1, len(fold(name).split()))
    for size in sorted({span, span + 1, max(1, span - 1)}):
        for start in range(0, max(1, len(words) - size + 1)):
            heard = sound_key("".join(words[start : start + size]))
            if any(_close(heard, target, threshold) for target in targets):
                return True
    return False


def names_in(text: str, names: list[dict]) -> list[dict]:
    """The pronunciation entries whose name appears in ``text`` (accent- and case-insensitive)."""
    folded = f" {fold(text)} "
    return [entry for entry in names if fold(entry["name"]) and f" {fold(entry['name'])} " in folded]


def missing_names(transcript: str, text: str, names: list[dict]) -> list[dict]:
    """Names the script says in ``text`` that the transcript doesn't seem to contain."""
    return [
        entry
        for entry in names_in(text, names)
        if not name_heard(entry["name"], entry.get("respelling", ""), transcript)
    ]


def respell(text: str, entries: list[dict]) -> str:
    """Swap each name for its respelling, for the TTS text only (captions keep the real spelling)."""
    for entry in entries:
        if entry.get("respelling"):
            pattern = re.compile(re.escape(entry["name"]), re.IGNORECASE)
            text = pattern.sub(entry["respelling"], text)
    return text


def openai_transcriber(client) -> Transcriber:
    """Whisper via the OpenAI API. No prompt is given: hinting the names would hide mispronunciations."""

    def transcribe(path: Path) -> str:
        with path.open("rb") as audio:
            return client.audio.transcriptions.create(model="whisper-1", file=audio).text

    return transcribe
