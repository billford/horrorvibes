"""Ties every module together. Owns retry/fallback policy, run-cadence
gating, and turning any unrecoverable failure into a durable log entry
plus a macOS notification (since nobody is watching an unattended run).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404 - no shell=True anywhere below
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from horrorvibes import compositor, imagegen, musicgen, publish, quotes, video
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


def run_pipeline(  # pylint: disable=too-many-locals
    config: Config,
    chat_client,
    quote_count: int | None = None,
    now: Callable[[], datetime] | None = None,
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

    frame_paths = []
    for i, quote_text in enumerate(generated_quotes):
        image_result = imagegen.generate_image(quote_text, i, config, config.run.images_dir)
        frame_path = config.run.frames_dir / f"frame_{i + 1}.png"
        compositor.compose_frame(
            image_result.path, quote_text, config.compositor, config.run.width, config.run.height, frame_path
        )
        frame_paths.append(frame_path)
    logger.info("Composed %d frames", len(frame_paths))

    music_path = config.run.output_dir / "_music.mp3"
    music_result = musicgen.generate_music(config, music_path)
    logger.info("Music ready via %r backend", music_result.backend_used)

    timestamp = now().strftime("%Y%m%d_%H%M%S")
    output_path = config.run.output_dir / f"horror_quotes_{timestamp}.mp4"

    video_path = video.assemble_video(
        frame_paths,
        output_path,
        music_result.path,
        config.run.duration_per_quote_sec,
        config.run.fps,
        runner=subprocess.run,
    )

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
        result = run_pipeline(config, chat_client, quote_count=quote_count)
    except HorrorVibesError as exc:
        logger.error("Run failed: %s", exc, exc_info=True)
        notify_failure(f"horrorvibes run failed: {exc}")
        return 1

    record_run(config.automation.state_file, datetime.now())
    logger.info("Run complete: %s (%d quotes)", result.video_path, result.quote_count)
    return 0
