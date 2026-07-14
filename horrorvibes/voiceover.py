"""Per-quote narration via the ElevenLabs text-to-speech API, plus ffmpeg
command builders to mix that narration into the background music track,
ducking the music at each narration's known start/end time and boosting
narration gain so it's clearly audible over the music bed.

An earlier version used ffmpeg's sidechaincompress, keyed off the
narration's own loudness crossing a threshold -- but narration's RMS
(~0.02-0.05) sat right at the chosen threshold while the music's RMS
(~0.18-0.2) was well above it, so the compressor barely engaged and
narration ended up inaudible under full-volume music. Since we already
know each narration's exact timing, ducking the music at those known
windows directly is both simpler and actually reliable.

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


def narration_duration_sec(narration_path: Path) -> float:
    """Probe a narration clip's duration without loading its audio into memory."""
    import soundfile as sf  # pylint: disable=import-outside-toplevel

    return sf.info(narration_path).duration


def build_ducked_mix_cmd(  # pylint: disable=too-many-locals
    music_path: Path,
    narration_entries: list[tuple[float, Path, float]],
    voiceover_config: VoiceoverConfig,
    output_path: Path,
) -> list[str]:
    """ffmpeg argv: boost and delay each narration clip to its quote's actual
    start time, duck the music at those exact known windows, then mix.
    Ducking is a plain ``volume`` filter enabled only during each
    narration's actual [start, start+duration] window -- deterministic,
    unlike sidechain compression, which depends on calibrating a threshold
    against signal levels that vary per voice/track.

    ``narration_entries`` is (start_sec, path, duration_sec) for quotes
    whose narration succeeded. The caller computes ``start_sec`` from each
    quote's actual on-screen duration (which may run longer than the
    nominal per-quote duration to fit a long narration) -- not simply
    ``quote_index * duration_per_quote_sec``, which would let one quote's
    narration bleed into the next's.
    """
    if not narration_entries:
        raise ValueError("build_ducked_mix_cmd requires at least one narration entry")

    inputs = ["-i", str(music_path)]
    delay_labels = []
    duck_windows = []
    for position, (start_sec, narration_path, duration_sec) in enumerate(narration_entries, start=1):
        inputs.extend(["-i", str(narration_path)])
        delay_ms = int(start_sec * 1000)
        delay_labels.append((position, f"d{position}", delay_ms))
        duck_windows.append((start_sec, start_sec + duration_sec))

    duck_condition = "+".join(f"between(t,{start},{end})" for start, end in duck_windows)
    filter_parts = [
        f"[0:a]volume={voiceover_config.duck_volume}:enable='{duck_condition}':eval=frame[duckedmusic]"
    ]
    filter_parts += [
        f"[{position}:a]volume={voiceover_config.narration_gain}[g{position}]"
        for position, _, _ in delay_labels
    ]
    filter_parts += [
        f"[g{position}]adelay={delay_ms}|{delay_ms}[{label}]" for position, label, delay_ms in delay_labels
    ]

    if len(delay_labels) == 1:
        narration_mix_label = delay_labels[0][1]
    else:
        joined_labels = "".join(f"[{label}]" for _, label, _ in delay_labels)
        filter_parts.append(f"{joined_labels}amix=inputs={len(delay_labels)}:normalize=0[narrmix]")
        narration_mix_label = "narrmix"

    filter_parts.append(f"[duckedmusic][{narration_mix_label}]amix=inputs=2:normalize=0[final]")

    return [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[final]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ]
