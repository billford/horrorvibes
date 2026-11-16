"""Ties every module together. Owns retry/fallback policy, run-cadence
gating, and turning any unrecoverable failure into a durable log entry
plus a macOS notification (since nobody is watching an unattended run).
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess  # nosec B404 - no shell=True anywhere below
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from horrorvibes import (
    catalog, compositor, episode, imagegen, metadata, musicgen, publish, quotes, video, voiceover,
)
from horrorvibes.config import AutomationConfig, Config
from horrorvibes.exceptions import HorrorVibesError, PublishError, VideoAssemblyError
from horrorvibes.textutil import split_quote_and_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    """Summary of one completed pipeline run."""

    video_path: Path
    quote_count: int
    youtube_video_id: str | None


def is_run_due(
    automation_config: AutomationConfig,
    now: Callable[[], datetime] | None = None,
) -> bool:
    """Every-other-day guard: launchd fires daily, this decides whether to
    actually do anything. True if no prior run is recorded, or the last
    recorded run is older than min_hours_between_runs."""
    now = now or datetime.now
    state_file = automation_config.state_file
    if not state_file.exists():
        return True

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        last_run = datetime.fromisoformat(state["last_run"])
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Could not parse %s, treating run as due", state_file)
        return True

    elapsed_hours = (now() - last_run).total_seconds() / 3600
    return elapsed_hours >= automation_config.min_hours_between_runs


def record_run(state_file: Path, timestamp: datetime) -> None:
    """Atomically record that a run happened (write-temp + rename)."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(state_file.parent), prefix=".last_run_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump({"last_run": timestamp.isoformat()}, tmp_file)
        os.replace(tmp_name, state_file)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def notify_failure(message: str, runner: Callable[..., object] = subprocess.run) -> None:
    """Best-effort macOS local notification. Never raises -- a broken
    notification must not mask the original failure being reported."""
    script = f'display notification {json.dumps(message)} with title "horrorvibes run failed"'
    try:
        runner(["osascript", "-e", script], capture_output=True, text=True, check=False)
    except OSError:
        logger.warning("Failed to send macOS notification", exc_info=True)


def setup_directories(config: Config) -> None:
    """Create the run's working directories if they don't already exist."""
    directories = (config.run.quotes_dir, config.run.images_dir, config.run.frames_dir, config.run.output_dir)
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def _external_drive_available(path: Path, is_dir: Callable[[Path], bool] = Path.is_dir) -> bool:
    """On macOS, external volumes mount under /Volumes/<name>. If that
    mount-point directory doesn't already exist, the drive isn't plugged
    in -- don't create it ourselves, since mkdir(parents=True) would
    silently create a phantom folder on the boot disk under /Volumes
    instead of failing loudly when the real drive is absent."""
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return is_dir(Path(parts[0], parts[1], parts[2]))
    return True  # not a /Volumes path -- let the normal mkdir/copy handle it


def _archive_completed_video(config: Config, video_path: Path, timeout_sec: float = 300) -> None:
    """Best-effort copy of the finished video to run.completed_video_dir, if
    configured. Never raises -- an unavailable archive drive must not fail
    the run; the video is already safe in output_dir either way.

    The copy runs in a daemon thread with a timeout: under launchd, macOS can
    block open() on a removable volume indefinitely (a "would like to access
    files on a removable volume" prompt nobody is there to answer, or a
    wedged USB drive), which once hung a scheduled run for hours. A daemon
    thread still stuck in the kernel doesn't keep the process from exiting."""
    if config.run.completed_video_dir is None:
        return

    worker = threading.Thread(
        target=_copy_to_archive, args=(config.run.completed_video_dir, video_path), daemon=True
    )
    worker.start()
    worker.join(timeout_sec)
    if worker.is_alive():
        logger.warning(
            "Archiving to %s did not finish within %ss (drive unresponsive, or macOS waiting on a "
            "removable-volume permission prompt?); video stays in %s only",
            config.run.completed_video_dir,
            timeout_sec,
            video_path,
        )


def _copy_to_archive(archive_dir: Path, video_path: Path) -> None:
    if not _external_drive_available(archive_dir):
        logger.warning(
            "Archive directory %s not available (drive not mounted?); video stays in %s only",
            archive_dir,
            video_path,
        )
        return

    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / video_path.name
        shutil.copy2(video_path, archive_path)
        logger.info("Archived completed video to %s", archive_path)
    except OSError:
        logger.warning("Failed to archive video to %s", archive_dir, exc_info=True)


def _generate_narrations_if_enabled(
    config: Config,
    candidates: list[quotes.QuoteCandidate],
    elevenlabs_api_key: str | None,
    voice_id: str | None = None,
) -> list[voiceover.Narration | None]:
    if not config.voiceover.enabled:
        return [None] * len(candidates)
    narration_dir = config.run.output_dir / "narration"
    items = [voiceover.NarrationItem(text=c.display_text, delivery=c.delivery) for c in candidates]
    return voiceover.generate_narrations(
        config.voiceover, items, narration_dir, api_key=elevenlabs_api_key, voice_id=voice_id
    )


def _compute_quote_schedule(
    config: Config, narrations: list[voiceover.Narration | None]
) -> tuple[list[float], list[float], list[float | None]]:
    """Each quote's on-screen duration, its start offset in the final
    timeline, and its narration's own duration (or None). A quote whose
    narration runs longer than the nominal duration_per_quote_sec gets a
    longer on-screen duration (plus a pad) so the next quote's narration
    never starts before this one has actually finished."""
    narration_durations = [
        voiceover.narration_duration_sec(n.path) if n is not None else None for n in narrations
    ]

    quote_durations = [
        max(config.run.duration_per_quote_sec, duration + config.voiceover.narration_pad_sec)
        if duration is not None
        else float(config.run.duration_per_quote_sec)
        for duration in narration_durations
    ]

    start_offsets = []
    cursor = 0.0
    for duration in quote_durations:
        start_offsets.append(cursor)
        cursor += duration

    return quote_durations, start_offsets, narration_durations


def _build_final_audio_track(
    config: Config,
    music_path: Path,
    narrations: list[voiceover.Narration | None],
    narration_durations: list[float | None],
    start_offsets: list[float],
) -> Path:
    """Music alone, or music ducked under per-quote narration if
    voiceover.enabled and at least one quote's narration succeeded."""
    if not config.voiceover.enabled:
        return music_path

    narration_entries = [
        (start_offsets[i], narration.path, narration_durations[i])
        for i, narration in enumerate(narrations)
        if narration is not None
    ]
    if not narration_entries:
        logger.warning("Voiceover enabled but every quote's narration failed; using music track as-is")
        return music_path

    mixed_path = config.run.output_dir / "_music_with_narration.mp3"
    cmd = voiceover.build_ducked_mix_cmd(music_path, narration_entries, config.voiceover, mixed_path)
    # nosec B603 - no shell=True; argv is built by build_ducked_mix_cmd, not user input
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
    if result.returncode != 0:
        logger.warning("Narration/music mix failed, using music track as-is: %s", result.stderr)
        return music_path

    logger.info(
        "Mixed narration for %d/%d quotes into the music track",
        len(narration_entries),
        len(narrations),
    )
    return mixed_path


def caption_state_times(
    layers: compositor.TextLayers,
    narration: voiceover.Narration | None,
    narration_duration: float | None,
    quote_duration: float,
) -> list[float]:
    """When each caption state appears, in seconds into the quote's clip.
    Static captions fade in once; karaoke captions start fully dimmed and
    light each word at its narrated start (from API timings, else estimated
    across the narration -- or across 60% of the slot with no narration)."""
    if len(layers.word_states) == 1:
        return [0.4]
    words = ["w"] * layers.word_count
    if narration is not None and narration.word_starts and len(narration.word_starts) == layers.word_count:
        starts = list(narration.word_starts)
    elif narration_duration:
        starts = voiceover.estimate_word_starts(words, narration_duration)
    else:
        starts = voiceover.estimate_word_starts(words, quote_duration * 0.6, lead_sec=0.6)
    return [0.0] + starts


@dataclass(frozen=True)
class _QuoteMedia:
    background: Path
    layers: compositor.TextLayers
    frame: Path


def _render_quote_media(
    config: Config, plan: episode.EpisodePlan, candidates: list[quotes.QuoteCandidate]
) -> list[_QuoteMedia]:
    """Background image, text layers and a flattened still for every quote."""
    image_backends = imagegen.default_backends(config)
    karaoke = config.motion is None or config.motion.caption_mode == "karaoke"
    layers_dir = config.run.frames_dir / "layers"
    media = []
    for i, candidate in enumerate(candidates):
        line = candidate.to_line()
        image_result = imagegen.generate_image(
            line, i, config, config.run.images_dir, backends=image_backends,
            visual_scene=candidate.visual_scene, style=plan.pack.image_style,
        )
        _, movie_title = split_quote_and_title(line)
        layers = compositor.render_text_layers(
            candidate.display_text, movie_title, plan.pack, config.compositor,
            config.run.width, config.run.height, layers_dir, i, karaoke=karaoke,
        )
        frame = config.run.frames_dir / f"frame_{i + 1}.png"
        compositor.flatten_frame(image_result.path, layers, config.run.width, config.run.height, frame)
        media.append(_QuoteMedia(background=image_result.path, layers=layers, frame=frame))
    return media


def _assemble(  # pylint: disable=too-many-arguments
    config: Config,
    plan: episode.EpisodePlan,
    media: list[_QuoteMedia],
    narrations: list[voiceover.Narration | None],
    narration_durations: list[float | None],
    quote_durations: list[float],
    audio_path: Path,
    output_path: Path,
    rng: random.Random,
) -> Path:
    """Animated assembly when motion is enabled; the static frame-by-frame
    assembly otherwise, or if the animated render fails for any reason."""
    motion = config.motion
    if motion is not None and motion.enabled:
        clips = [
            video.ClipSpec(
                background=m.background,
                title=m.layers.title,
                word_states=m.layers.word_states,
                state_times=caption_state_times(
                    m.layers, narrations[i], narration_durations[i], quote_durations[i]
                ),
                duration=quote_durations[i],
                zoom_in=i % 2 == 0,
                drift_x=rng.uniform(-1, 1),
                drift_y=rng.uniform(-0.6, 0.6),
            )
            for i, m in enumerate(media)
        ]
        style = video.MotionStyle(
            grade=plan.pack.grade, overlay=plan.pack.overlay, vignette=plan.pack.vignette,
            zoom_amount=motion.zoom_amount, upscale_factor=motion.upscale_factor,
            drift=motion.drift, ripple_px=motion.ripple_px, flicker=motion.flicker,
            transition_sec=motion.transition_sec, crf=motion.crf, preset=motion.preset,
        )
        transitions = [rng.choice(plan.pack.transitions) for _ in range(max(len(clips) - 1, 0))]
        try:
            return video.assemble_motion_video(
                clips, output_path, audio_path, style, transitions,
                config.run.width, config.run.height, config.run.fps, runner=subprocess.run,
            )
        except (VideoAssemblyError, OSError):
            logger.warning("Animated assembly failed; falling back to static frames", exc_info=True)

    return video.assemble_video(
        [m.frame for m in media], output_path, audio_path, quote_durations, config.run.fps,
        runner=subprocess.run,
    )


def run_pipeline(  # pylint: disable=too-many-locals,too-many-arguments
    config: Config,
    chat_client,
    quote_count: int | None = None,
    now: Callable[[], datetime] | None = None,
    elevenlabs_api_key: str | None = None,
    rng: random.Random | None = None,
) -> RunResult:
    """Run the full plan -> quotes -> narration -> images -> music -> video -> publish pipeline once."""
    now = now or datetime.now
    rng = rng or random.Random()  # nosec B311 - creative variety, not security-sensitive
    setup_directories(config)

    plan = episode.plan_episode(config, rng, quote_count_override=quote_count)
    recent_movies = quotes.load_recent_movies(
        config.quotes.movie_history_path, now(), config.quotes.movie_cooldown_days
    )
    candidates = quotes.generate_quote_candidates(
        chat_client, plan.quotes_config, plan.quote_count, config.run.quotes_history_path,
        rng=rng, recent_movies=recent_movies, instruction=plan.instruction,
    )
    generated_quotes = [candidate.to_line() for candidate in candidates]
    quotes.write_quote_files(config.run.quotes_dir, generated_quotes)
    quotes.append_quote_history(config.run.quotes_history_path, generated_quotes)
    quotes.append_movie_history(
        config.quotes.movie_history_path,
        [split_quote_and_title(q)[1] for q in generated_quotes],
        now(),
    )
    logger.info("Generated %d quotes", len(generated_quotes))

    narrations = _generate_narrations_if_enabled(config, candidates, elevenlabs_api_key, plan.pack.voice_id)
    quote_durations, start_offsets, narration_durations = _compute_quote_schedule(config, narrations)

    media = _render_quote_media(config, plan, candidates)
    logger.info("Rendered media for %d quotes (pack=%s)", len(media), plan.pack.name)

    total_duration_sec = start_offsets[-1] + quote_durations[-1] if quote_durations else 0.0
    music_path = config.run.output_dir / "_music.mp3"
    music_result = musicgen.generate_music(
        config, music_path, api_key=elevenlabs_api_key, duration_sec=total_duration_sec,
        mood_anchor=plan.pack.music_mood_anchor, mood_pool=plan.pack.music_mood_pool,
    )
    logger.info("Music ready via %r backend", music_result.backend_used)

    final_audio_path = _build_final_audio_track(
        config, music_result.path, narrations, narration_durations, start_offsets
    )

    timestamp = now().strftime("%Y%m%d_%H%M%S")
    output_path = config.run.output_dir / f"horror_quotes_{timestamp}.mp4"
    video_path = _assemble(
        config, plan, media, narrations, narration_durations, quote_durations,
        final_audio_path, output_path, rng,
    )

    headline = plan.headline(len(generated_quotes))

    youtube_video_id: str | None = None
    curated_upload_blocked = (
        music_result.backend_used == "curated_file" and not config.music.allow_curated_upload
    )
    if config.publish.youtube_upload and curated_upload_blocked:
        message = (
            f"Music fell back to a curated library track, which YouTube Content ID will claim; "
            f"skipped upload. Video kept at {video_path}"
        )
        logger.error("%s (set music.allow_curated_upload to override)", message)
        notify_failure(message)
    elif config.publish.youtube_upload:
        try:
            youtube_video_id = publish.publish(
                video_path,
                metadata.build_video_title(generated_quotes, headline=headline),
                metadata.build_video_description(generated_quotes, headline=headline),
                metadata.build_video_hashtags(generated_quotes),
                config.publish,
            )
        except PublishError:
            logger.error(
                "YouTube upload failed; video remains available locally at %s", video_path, exc_info=True
            )

    catalog.append_entry(
        config.run.video_catalog_path,
        catalog.build_catalog_entry(
            video_path, generated_quotes, youtube_video_id, now(), music_backend=music_result.backend_used,
            style_pack=plan.pack.name, episode_format=plan.format_name, headline=headline,
        ),
    )
    if config.episodes is not None:
        episode.record_episode(config.episodes.history_path, plan, now())
    # Last, so a slow or unresponsive archive drive can never hold up the upload or the catalog entry.
    _archive_completed_video(config, video_path)

    return RunResult(
        video_path=video_path, quote_count=len(generated_quotes), youtube_video_id=youtube_video_id
    )


def main_unattended(
    config: Config,
    chat_client,
    force: bool = False,
    quote_count: int | None = None,
    elevenlabs_api_key: str | None = None,
) -> int:
    """Entry point for the launchd job: cadence guard + failure notification.

    Returns a process exit code (0 success/skip, 1 unrecoverable failure)
    instead of calling sys.exit() itself, so it stays testable.
    """
    if not force and not is_run_due(config.automation):
        logger.info(
            "Not due yet (min_hours_between_runs=%d); skipping.", config.automation.min_hours_between_runs
        )
        return 0

    try:
        result = run_pipeline(
            config, chat_client, quote_count=quote_count, elevenlabs_api_key=elevenlabs_api_key
        )
    except HorrorVibesError as exc:
        logger.error("Run failed: %s", exc, exc_info=True)
        notify_failure(f"horrorvibes run failed: {exc}")
        return 1

    record_run(config.automation.state_file, datetime.now())
    logger.info("Run complete: %s (%d quotes)", result.video_path, result.quote_count)
    return 0
