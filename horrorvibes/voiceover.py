"""Per-quote narration via the ElevenLabs text-to-speech API, plus ffmpeg
command builders to mix that narration into the background music track
with sidechain ducking (music quiets automatically while narration plays).

Narration is best-effort per quote -- a failed quote's narration is
skipped (logged as a warning) rather than failing the whole run, matching
the per-item resilience pattern used elsewhere (imagegen, compositor).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from horrorvibes.config import VoiceoverConfig
from horrorvibes.musicgen import with_retries
from horrorvibes.textutil import split_quote_and_title

logger = logging.getLogger(__name__)

_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

NarrationBackend = Callable[[str, Path], None]


class ElevenLabsVoiceoverBackend:
    """Calls the ElevenLabs text-to-speech API (audio bytes response)."""

    def __init__(self, config: VoiceoverConfig, api_key: str | None, session=None) -> None:
        self._config = config
        self._api_key = api_key
        self._session = session

    def _session_or_default(self):
        if self._session is None:
            import requests  # pylint: disable=import-outside-toplevel

            self._session = requests.Session()
        return self._session

    def _request_once(self, text: str, output_path: Path) -> None:
        session = self._session_or_default()
        url = _ELEVENLABS_TTS_URL.format(voice_id=self._config.voice_id)
        response = session.post(
            url,
            headers={"xi-api-key": self._api_key or ""},
            json={
                "text": text,
                "model_id": self._config.model_id,
                "voice_settings": {
                    "stability": self._config.stability,
                    "similarity_boost": self._config.similarity_boost,
                    "style": self._config.style,
                    "use_speaker_boost": self._config.use_speaker_boost,
                    "speed": self._config.speed,
                },
            },
            timeout=self._config.api_timeout_sec,
        )
        response.raise_for_status()
        output_path.write_bytes(response.content)

    def __call__(self, text: str, output_path: Path) -> None:
        with_retries(
            lambda: self._request_once(text, output_path),
            self._config.max_retries,
            self._config.retry_backoff_sec,
        )


def default_backend(config: VoiceoverConfig, api_key: str | None) -> NarrationBackend:
    """Build the real, config-driven narration backend."""
    return ElevenLabsVoiceoverBackend(config, api_key)


def generate_narrations(
    config: VoiceoverConfig,
    quotes: list[str],
    output_dir: Path,
    api_key: str | None = None,
    backend: NarrationBackend | None = None,
) -> list[Path | None]:
    """Generate one narration clip per quote (just the quote text, not the
    movie title). Returns a list aligned with ``quotes`` -- ``None`` for any
    quote whose narration failed, so the caller can skip it rather than
    fail the whole run."""
    backend = backend if backend is not None else default_backend(config, api_key)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[Path | None] = []
    for i, quote in enumerate(quotes):
        quote_text, _ = split_quote_and_title(quote)
        output_path = output_dir / f"narration_{i + 1}.mp3"
        try:
            backend(quote_text, output_path)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Narration failed for quote %d, skipping", i + 1, exc_info=True)
            results.append(None)
            continue
        logger.info("Narration generated for quote %d", i + 1)
        results.append(output_path)
    return results


def build_ducked_mix_cmd(
    music_path: Path,
    narration_entries: list[tuple[int, Path]],
    duration_per_quote_sec: float,
    voiceover_config: VoiceoverConfig,
    output_path: Path,
) -> list[str]:
    """ffmpeg argv: delay each narration clip to its quote's start time,
    mix them together, then sidechain-duck the music under that mix so it
    quiets automatically while narration plays. ``narration_entries`` is
    (quote_index, path) pairs for quotes whose narration succeeded --
    quote_index (not list position) drives the delay, so gaps from failed
    quotes don't shift later narration out of place."""
    if not narration_entries:
        raise ValueError("build_ducked_mix_cmd requires at least one narration entry")

    inputs = ["-i", str(music_path)]
    delay_labels = []
    for position, (quote_index, narration_path) in enumerate(narration_entries, start=1):
        inputs.extend(["-i", str(narration_path)])
        delay_ms = int(quote_index * duration_per_quote_sec * 1000)
        delay_labels.append((position, f"d{position}", delay_ms))

    filter_parts = [
        f"[{position}:a]adelay={delay_ms}|{delay_ms}[{label}]" for position, label, delay_ms in delay_labels
    ]

    if len(delay_labels) == 1:
        narration_mix_label = delay_labels[0][1]
    else:
        joined_labels = "".join(f"[{label}]" for _, label, _ in delay_labels)
        filter_parts.append(f"{joined_labels}amix=inputs={len(delay_labels)}:normalize=0[narrmix]")
        narration_mix_label = "narrmix"

    filter_parts.append(
        f"[0:a][{narration_mix_label}]sidechaincompress="
        f"threshold={voiceover_config.duck_threshold}:ratio={voiceover_config.duck_ratio}:"
        f"attack={voiceover_config.duck_attack_ms}:release={voiceover_config.duck_release_ms}[ducked]"
    )
    filter_parts.append(f"[ducked][{narration_mix_label}]amix=inputs=2:weights=1 1:normalize=0[final]")

    return [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[final]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ]
