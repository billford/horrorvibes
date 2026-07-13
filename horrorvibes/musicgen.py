"""Meditative horror music generation via the ElevenLabs Music API.

One call per run, sized to the whole video's duration -- not one call per
quote -- per spec (simpler, cheaper, no audible seams between quotes).
Falls back to picking a random curated file from ``fallback_dir`` if the
API call fails after retries.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from horrorvibes.config import Config, MusicConfig
from horrorvibes.exceptions import MusicGenerationError

logger = logging.getLogger(__name__)

_AUDIO_EXTENSIONS = ("*.mp3", "*.wav", "*.m4a")
_ELEVENLABS_MUSIC_URL = "https://api.elevenlabs.io/v1/music"

T = TypeVar("T")


@dataclass(frozen=True)
class MusicResult:
    """Which file was produced and which backend in the chain produced it."""

    path: Path
    backend_used: str


def compute_duration_ms(quote_count: int, duration_per_quote_sec: int) -> int:
    """Total video duration in milliseconds, clamped to the API's allowed range."""
    duration_ms = quote_count * duration_per_quote_sec * 1000
    return max(3_000, min(duration_ms, 600_000))


def build_music_prompt(
    mood_anchor: str,
    mood_pool: list[str],
    sample_size: int,
    rng: random.Random | None = None,
) -> str:
    """Combine a random subset of mood descriptors with the fixed anchor phrase.

    Keeps the mood consistent run-to-run (the anchor) while varying texture
    (the sampled descriptors), per spec 5.2.
    """
    rng = rng or random.Random()  # nosec B311 - creative prompt variety, not security-sensitive
    sample_size = min(sample_size, len(mood_pool))
    descriptors = rng.sample(mood_pool, sample_size)
    return f"{mood_anchor}, {', '.join(descriptors)}"


def with_retries(
    fn: Callable[[], T],
    max_retries: int,
    backoff_sec: int,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` up to ``max_retries`` times with exponential backoff between attempts."""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:  # pylint: disable=broad-except
            last_exc = exc
            logger.warning("Attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                sleep(backoff_sec * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


class ElevenLabsMusicBackend:
    """Calls the ElevenLabs Music API (POST /v1/music, audio bytes response)."""

    def __init__(self, config: MusicConfig, api_key: str | None, session=None) -> None:
        self._config = config
        self._api_key = api_key
        self._session = session

    def _session_or_default(self):
        if self._session is None:
            import requests  # pylint: disable=import-outside-toplevel

            self._session = requests.Session()
        return self._session

    def _request_once(self, prompt: str, duration_ms: int, output_path: Path) -> None:
        session = self._session_or_default()
        response = session.post(
            _ELEVENLABS_MUSIC_URL,
            headers={"xi-api-key": self._api_key or ""},
            json={
                "prompt": prompt,
                "music_length_ms": duration_ms,
                "model_id": self._config.model_id,
                "force_instrumental": True,
            },
            timeout=self._config.api_timeout_sec,
        )
        response.raise_for_status()
        output_path.write_bytes(response.content)

    def __call__(self, prompt: str, duration_ms: int, output_path: Path) -> None:
        with_retries(
            lambda: self._request_once(prompt, duration_ms, output_path),
            self._config.max_retries,
            self._config.retry_backoff_sec,
        )


class CuratedFileBackend:
    """Fallback: pick a random pre-supplied track from fallback_dir."""

    def __init__(self, fallback_dir: Path, rng: random.Random | None = None) -> None:
        self._fallback_dir = fallback_dir
        self._rng = rng or random.Random()  # nosec B311 - picking a fallback track, not security-sensitive

    def _candidates(self) -> list[Path]:
        if not self._fallback_dir.exists():
            return []
        found: list[Path] = []
        for pattern in _AUDIO_EXTENSIONS:
            found.extend(self._fallback_dir.glob(pattern))
        return found

    def __call__(self, prompt: str, duration_ms: int, output_path: Path) -> None:
        candidates = self._candidates()
        if not candidates:
            raise MusicGenerationError(f"No curated audio files found in {self._fallback_dir}")
        chosen = self._rng.choice(candidates)
        output_path.write_bytes(chosen.read_bytes())


_BACKEND_CHAIN: dict[str, tuple[str, ...]] = {
    "elevenlabs": ("elevenlabs", "curated_file"),
    "curated_file": ("curated_file",),
}


def default_backends(config: Config, api_key: str | None) -> dict[str, Callable[[str, int, Path], None]]:
    """Build the real, config-driven backend instances for each chain link."""
    return {
        "elevenlabs": ElevenLabsMusicBackend(config.music, api_key),
        "curated_file": CuratedFileBackend(config.music.fallback_dir),
    }


def generate_music(
    config: Config,
    output_path: Path,
    api_key: str | None = None,
    backends: dict[str, Callable[[str, int, Path], None]] | None = None,
    rng: random.Random | None = None,
) -> MusicResult:
    """Generate (or fetch a fallback for) the run's background music track."""
    backends = backends if backends is not None else default_backends(config, api_key)
    chain = _BACKEND_CHAIN[config.music.backend]

    prompt = build_music_prompt(
        config.music.mood_anchor, config.music.mood_pool, config.music.mood_pool_sample_size, rng
    )
    duration_ms = compute_duration_ms(config.run.quote_count, config.run.duration_per_quote_sec)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    for name in chain:
        try:
            backends[name](prompt, duration_ms, output_path)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Music backend %r failed, falling back", name, exc_info=True)
            continue
        if name != chain[0]:
            logger.warning("Music generated via fallback backend %r (not %r)", name, chain[0])
        else:
            logger.info("Music generated via %r backend", name)
        return MusicResult(path=output_path, backend_used=name)

    raise MusicGenerationError(f"All music backends in chain {chain} failed")
