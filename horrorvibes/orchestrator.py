"""Ties every module together. Owns retry/fallback policy, run-cadence
gating, and turning any unrecoverable failure into a durable log entry
plus a macOS notification (since nobody is watching an unattended run).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess  # nosec B404 - no shell=True anywhere below
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from horrorvibes import compositor, imagegen, musicgen, publish, quotes, video, voiceover
from horrorvibes.config import AutomationConfig, Config
from horrorvibes.exceptions import HorrorVibesError, PublishError

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


def _archive_completed_video(config: Config, video_path: Path) -> None:
    """Best-effort copy of the finished video to run.completed_video_dir, if
    configured. Never raises -- an unavailable archive drive must not fail
    the run; the video is already safe in output_dir either way."""
    archive_dir = config.run.completed_video_dir
    if archive_dir is None:
        return

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
    config: Config, generated_quotes: list[str], elevenlabs_api_key: str | None
) -> list[Path | None]:
    if not config.voiceover.enabled:
        return [None] * len(generated_quotes)
    narration_dir = config.run.output_dir / "narration"
    return voiceover.generate_narrations(
        config.voiceover, generated_quotes, narration_dir, api_key=elevenlabs_api_key
    )


def _compute_quote_schedule(
    config: Config, narrations: list[Path | None]
) -> tuple[list[float], list[float], list[float | None]]:
    """Each quote's on-screen duration, its start offset in the final
    timeline, and its narration's own duration (or None). A quote whose
    narration runs longer than the nominal duration_per_quote_sec gets a
    longer on-screen duration (plus a pad) so the next quote's narration
    never starts before this one has actually finished."""
    narration_durations = [voiceover.narration_duration_sec(p) if p is not None else None for p in narrations]

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
    narrations: list[Path | None],
    narration_durations: list[float | None],
    start_offsets: list[float],
) -> Path:
    """Music alone, or music ducked under per-quote narration if
    voiceover.enabled and at least one quote's narration succeeded."""
    if not config.voiceover.enabled:
        return music_path

    narration_entries = [
        (start_offsets[i], narrations[i], narration_durations[i])
        for i in range(len(narrations))
        if narrations[i] is not None
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


def run_pipeline(  # pylint: disable=too-many-locals
    config: Config,
    chat_client,
    quote_count: int | None = None,
    now: Callable[[], datetime] | None = None,
    elevenlabs_api_key: str | None = None,
) -> RunResult:
    """Run the full quote -> image -> music -> video -> publish pipeline once."""
    now = now or datetime.now
    setup_directories(config)

    count = quote_count if quote_count is not None else config.run.quote_count
    generated_quotes = quotes.generate_quotes(
        chat_client, config.quotes, count, config.run.quotes_history_path
    )
    quotes.write_quote_files(config.run.quotes_dir, generated_quotes)
    quotes.append_quote_history(config.run.quotes_history_path, generated_quotes)
    logger.info("Generated %d quotes", len(generated_quotes))

    narrations = _generate_narrations_if_enabled(config, generated_quotes, elevenlabs_api_key)
    quote_durations, start_offsets, narration_durations = _compute_quote_schedule(config, narrations)

    image_backends = imagegen.default_backends(config)
    frame_paths = []
    for i, quote_text in enumerate(generated_quotes):
        image_result = imagegen.generate_image(
            quote_text, i, config, config.run.images_dir, backends=image_backends
        )
        frame_path = config.run.frames_dir / f"frame_{i + 1}.png"
        compositor.compose_frame(
            image_result.path, quote_text, config.compositor, config.run.width, config.run.height, frame_path
        )
        frame_paths.append(frame_path)
    logger.info("Composed %d frames", len(frame_paths))

    total_duration_sec = start_offsets[-1] + quote_durations[-1] if quote_durations else 0.0
    music_path = config.run.output_dir / "_music.mp3"
    music_result = musicgen.generate_music(
        config, music_path, api_key=elevenlabs_api_key, duration_sec=total_duration_sec
    )
    logger.info("Music ready via %r backend", music_result.backend_used)

    final_audio_path = _build_final_audio_track(
        config, music_result.path, narrations, narration_durations, start_offsets
    )

    timestamp = now().strftime("%Y%m%d_%H%M%S")
    output_path = config.run.output_dir / f"horror_quotes_{timestamp}.mp4"

    video_path = video.assemble_video(
        frame_paths,
        output_path,
        final_audio_path,
        quote_durations,
        config.run.fps,
        runner=subprocess.run,
    )
    _archive_completed_video(config, video_path)

    youtube_video_id: str | None = None
    if config.publish.youtube_upload:
        try:
            youtube_video_id = publish.publish(
                video_path,
                "Haunting Horror Movie Quotes",
                "A collection of the most spine-chilling quotes from classic horror films",
                ["horror", "movie quotes", "scary", "horror films", "shorts"],
                config.publish,
            )
        except PublishError:
            logger.error(
                "YouTube upload failed; video remains available locally at %s", video_path, exc_info=True
            )

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
