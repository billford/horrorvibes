"""Meditative horror music generation: local Stable Audio Open, the
ElevenLabs Music API, or a curated fallback file.

One generation per run, sized to the whole video's duration -- not one
call per quote -- per spec (simpler, cheaper, no audible seams between
quotes). Each backend falls back to the next in the chain on failure.
"""

from __future__ import annotations

import logging
import math
import random
import subprocess  # nosec B404 - no shell=True anywhere below
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from horrorvibes.config import Config, MusicConfig
from horrorvibes.exceptions import MusicGenerationError

logger = logging.getLogger(__name__)

_AUDIO_EXTENSIONS = ("*.mp3", "*.wav", "*.m4a")
_ELEVENLABS_MUSIC_URL = "https://api.elevenlabs.io/v1/music"

T = TypeVar("T")
Runner = Callable[..., Any]


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


def plan_segment_count(total_sec: float, segment_sec: float, crossfade_sec: float) -> int:
    """How many ``segment_sec``-long clips are needed to cover ``total_sec``
    once consecutive clips are crossfaded by ``crossfade_sec``.

    Each additional segment past the first only adds (segment_sec -
    crossfade_sec) of new material, since the crossfade region overlaps.
    """
    if total_sec <= segment_sec:
        return 1
    step = segment_sec - crossfade_sec
    return 1 + math.ceil((total_sec - segment_sec) / step)


def build_crossfade_filter(segment_count: int, crossfade_sec: float) -> tuple[str, str]:
    """ffmpeg filter_complex chaining ``acrossfade`` across N audio inputs.

    Returns (filter_complex_string, final_output_label). For a single
    segment there's nothing to crossfade -- callers should skip straight
    to a plain copy/trim in that case.
    """
    if segment_count < 2:
        raise ValueError("build_crossfade_filter requires at least 2 segments")

    parts = []
    previous_label = "0:a"
    for i in range(1, segment_count):
        output_label = f"cf{i}"
        parts.append(f"[{previous_label}][{i}:a]acrossfade=d={crossfade_sec}:c1=tri:c2=tri[{output_label}]")
        previous_label = output_label
    return ";".join(parts), previous_label


def build_music_assembly_cmd(
    segment_paths: list[Path], crossfade_sec: float, trim_to_sec: float, output_path: Path
) -> list[str]:
    """ffmpeg argv that crossfades ``segment_paths`` (if more than one) into
    a single track, trimmed to exactly ``trim_to_sec``, encoded to match
    ``output_path``'s extension."""
    inputs: list[str] = []
    for path in segment_paths:
        inputs.extend(["-i", str(path)])

    if len(segment_paths) == 1:
        return [
            "ffmpeg", "-y", *inputs,
            "-t", str(trim_to_sec),
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(output_path),
        ]

    filter_complex, final_label = build_crossfade_filter(len(segment_paths), crossfade_sec)
    return [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-t", str(trim_to_sec),
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ]


class LocalMusicBackend:
    """Local Stable Audio Open generation via diffusers on the Apple Silicon
    MPS backend. The model natively caps out well under a full run's
    length, so this generates several segments and crossfades them
    together with ffmpeg to reach the target duration.
    """

    def __init__(self, config: MusicConfig, runner: Runner = subprocess.run) -> None:
        self._config = config
        self._runner = runner
        self._pipeline = None

    def _pipeline_or_load(self):
        if self._pipeline is None:
            import torch  # pylint: disable=import-outside-toplevel
            from diffusers import StableAudioPipeline  # pylint: disable=import-outside-toplevel

            logger.info(
                "Loading local music model %s (device=%s)",
                self._config.local_model,
                self._config.local_device,
            )
            pipeline = StableAudioPipeline.from_pretrained(
                self._config.local_model, torch_dtype=torch.float16
            )
            self._pipeline = pipeline.to(self._config.local_device)
        return self._pipeline

    def _generate_segment(
        self, pipeline, prompt: str, length_sec: float, seed: int, output_path: Path
    ) -> None:
        import soundfile as sf  # pylint: disable=import-outside-toplevel
        import torch  # pylint: disable=import-outside-toplevel

        generator = torch.Generator(device=self._config.local_device).manual_seed(seed)
        result = pipeline(
            prompt=prompt,
            negative_prompt=self._config.local_negative_prompt,
            num_inference_steps=self._config.local_steps,
            audio_end_in_s=length_sec,
            num_waveforms_per_prompt=1,
            generator=generator,
        )
        audio = result.audios[0].T.float().cpu().numpy()
        sf.write(output_path, audio, pipeline.vae.sampling_rate)

    def __call__(self, prompt: str, duration_ms: int, output_path: Path) -> None:
        pipeline = self._pipeline_or_load()
        target_sec = duration_ms / 1000
        segment_sec = min(self._config.local_segment_sec, target_sec)
        count = plan_segment_count(target_sec, segment_sec, self._config.local_crossfade_sec)

        with tempfile.TemporaryDirectory() as tmp_dir:
            segment_paths = []
            for i in range(count):
                length = segment_sec if count > 1 else target_sec
                segment_path = Path(tmp_dir) / f"segment_{i}.wav"
                self._generate_segment(pipeline, prompt, length, seed=i, output_path=segment_path)
                segment_paths.append(segment_path)

            cmd = build_music_assembly_cmd(
                segment_paths, self._config.local_crossfade_sec, target_sec, output_path
            )
            result = self._runner(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise MusicGenerationError(f"ffmpeg failed to assemble local music segments: {result.stderr}")


_BACKEND_CHAIN: dict[str, tuple[str, ...]] = {
    "local": ("local", "elevenlabs", "curated_file"),
    "elevenlabs": ("elevenlabs", "local", "curated_file"),
    "curated_file": ("curated_file",),
}


def default_backends(config: Config, api_key: str | None) -> dict[str, Callable[[str, int, Path], None]]:
    """Build the real, config-driven backend instances for each chain link."""
    return {
        "local": LocalMusicBackend(config.music),
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
