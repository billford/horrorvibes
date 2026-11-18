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

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from horrorvibes.config import VoiceoverConfig
from horrorvibes.musicgen import with_retries

logger = logging.getLogger(__name__)

_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# eleven_v3 only accepts these stability values (Creative / Natural / Robust).
_V3_STABILITY_STEPS = (0.0, 0.5, 1.0)


@dataclass(frozen=True)
class NarrationItem:
    """What to say for one quote, and how."""

    text: str
    delivery: str = ""


@dataclass(frozen=True)
class Narration:
    """One generated clip, plus each caption word's start time (seconds into
    the clip) when the API returned character timings."""

    path: Path
    word_starts: list[float] | None = None


# backend(text, output_path, model_id, voice_id) -> alignment dict or None
NarrationBackend = Callable[[str, Path, str, str], "dict | None"]


def _is_v3(model_id: str) -> bool:
    return model_id.startswith("eleven_v3")


class ElevenLabsVoiceoverBackend:
    """Calls the ElevenLabs text-to-speech API. With ``use_timestamps`` it
    uses the /with-timestamps variant (JSON: base64 audio + character
    alignment) so captions can be synced to the actual speech."""

    def __init__(self, config: VoiceoverConfig, api_key: str | None, session=None) -> None:
        self._config = config
        self._api_key = api_key
        self._session = session

    def _session_or_default(self):
        if self._session is None:
            import requests  # pylint: disable=import-outside-toplevel

            self._session = requests.Session()
        return self._session

    def voice_settings(self, model_id: str) -> dict:
        """v3 takes a stepped stability and none of the v2 style/speed knobs."""
        if _is_v3(model_id):
            stability = min(_V3_STABILITY_STEPS, key=lambda step: abs(step - self._config.stability))
            return {
                "stability": stability,
                "similarity_boost": self._config.similarity_boost,
                "use_speaker_boost": self._config.use_speaker_boost,
            }
        return {
            "stability": self._config.stability,
            "similarity_boost": self._config.similarity_boost,
            "style": self._config.style,
            "use_speaker_boost": self._config.use_speaker_boost,
            "speed": self._config.speed,
        }

    def _request_once(self, text: str, output_path: Path, model_id: str, voice_id: str) -> dict | None:
        session = self._session_or_default()
        url = _ELEVENLABS_TTS_URL.format(voice_id=voice_id)
        if self._config.use_timestamps:
            url += "/with-timestamps"
        response = session.post(
            url,
            headers={"xi-api-key": self._api_key or ""},
            json={"text": text, "model_id": model_id, "voice_settings": self.voice_settings(model_id)},
            timeout=self._config.api_timeout_sec,
        )
        response.raise_for_status()
        if not self._config.use_timestamps:
            output_path.write_bytes(response.content)
            return None
        payload = response.json()
        output_path.write_bytes(base64.b64decode(payload["audio_base64"]))
        return payload.get("alignment") or payload.get("normalized_alignment")

    def __call__(self, text: str, output_path: Path, model_id: str, voice_id: str) -> dict | None:
        return with_retries(
            lambda: self._request_once(text, output_path, model_id, voice_id),
            self._config.max_retries,
            self._config.retry_backoff_sec,
        )


def default_backend(config: VoiceoverConfig, api_key: str | None) -> NarrationBackend:
    """Build the real, config-driven narration backend."""
    return ElevenLabsVoiceoverBackend(config, api_key)


def spoken_text(item: NarrationItem, tagged: bool) -> str:
    """The text sent to TTS: the quote, prefixed with a v3 audio tag if asked."""
    if tagged and item.delivery:
        return f"[{item.delivery}] {item.text}"
    return item.text


def word_starts_from_alignment(alignment: dict | None) -> list[float] | None:
    """Start time of each whitespace-separated word, skipping [audio tags]."""
    if not alignment:
        return None
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    if not chars or len(chars) != len(starts):
        return None

    words: list[float] = []
    current: float | None = None
    in_tag = False
    for char, start in zip(chars, starts):
        if in_tag:
            in_tag = char != "]"
            continue
        if char == "[":
            in_tag = True
            continue
        if char.isspace():
            if current is not None:
                words.append(float(current))
                current = None
        elif current is None:
            current = start
    if current is not None:
        words.append(float(current))
    return words


def estimate_word_starts(words: list[str], duration_sec: float, lead_sec: float = 0.1) -> list[float]:
    """Spread word starts across a clip in proportion to word length -- the
    fallback when the API gives no timings."""
    if not words:
        return []
    weights = [len(word) + 1 for word in words]
    span = max(duration_sec - lead_sec, 0.1)
    total = sum(weights)
    starts, cursor = [], lead_sec
    for weight in weights:
        starts.append(round(cursor, 3))
        cursor += span * weight / total
    return starts


def _attempt_plan(config: VoiceoverConfig, voice_id: str) -> list[tuple[str, str, bool]]:
    """(model, voice, tagged) attempts in order: primary model with delivery
    tags, then the fallback model untagged, then the default voice."""
    tagged = config.use_delivery_tags and _is_v3(config.model_id)
    plan = [(config.model_id, voice_id, tagged)]
    if config.fallback_model_id and config.fallback_model_id != config.model_id:
        plan.append((config.fallback_model_id, voice_id, False))
    if voice_id != config.voice_id:
        last_model = config.fallback_model_id or config.model_id
        plan.append((last_model, config.voice_id, tagged and last_model == config.model_id))
    return plan


def generate_narrations(  # pylint: disable=too-many-arguments,too-many-locals
    config: VoiceoverConfig,
    items: list[NarrationItem],
    output_dir: Path,
    api_key: str | None = None,
    backend: NarrationBackend | None = None,
    voice_id: str | None = None,
) -> list[Narration | None]:
    """One clip per item, aligned with ``items`` -- ``None`` where every
    attempt failed, so the caller can skip that quote's narration rather
    than fail the run. A (model, voice) pair that failed once (after its
    own retries) is not tried again for later items in this run."""
    backend = backend if backend is not None else default_backend(config, api_key)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = _attempt_plan(config, voice_id or config.voice_id)
    dead: set[tuple[str, str]] = set()

    results: list[Narration | None] = []
    for i, item in enumerate(items):
        output_path = output_dir / f"narration_{i + 1}.mp3"
        result: Narration | None = None
        for model_id, voice, tagged in plan:
            if (model_id, voice) in dead:
                continue
            text = spoken_text(item, tagged)
            try:
                alignment = backend(text, output_path, model_id, voice)
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Narration failed for quote %d (model=%s voice=%s)", i + 1, model_id, voice, exc_info=True
                )
                dead.add((model_id, voice))
                continue
            starts = word_starts_from_alignment(alignment)
            if starts is not None and len(starts) != len(item.text.split()):
                logger.debug("Alignment word count mismatch for quote %d; will estimate", i + 1)
                starts = None
            result = Narration(path=output_path, word_starts=starts)
            logger.info(
                "Narration generated for quote %d via %s (word timings: %s)",
                i + 1, model_id, "yes" if starts is not None else "no -- will estimate",
            )
            break
        if result is None:
            logger.warning("Narration failed for quote %d on every model/voice, skipping", i + 1)
        results.append(result)
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
    # loudnorm upsamples to 192 kHz internally, so resample straight back.
    music_norm = f"loudnorm=I={voiceover_config.music_lufs}:TP=-2:LRA=11,aresample=44100"
    voice_norm = f"loudnorm=I={voiceover_config.narration_lufs}:TP=-1.5:LRA=11,aresample=44100"
    filter_parts = [
        f"[0:a]{music_norm},"
        f"volume={voiceover_config.duck_volume}:enable='{duck_condition}':eval=frame[duckedmusic]"
    ]
    filter_parts += [
        f"[{position}:a]{voice_norm},volume={voiceover_config.narration_gain}[g{position}]"
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
