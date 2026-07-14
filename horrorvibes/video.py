"""Assembles frames + audio into an MP4 via ffmpeg (concat demuxer + mux).

Command construction is split into pure functions so tests can assert on
the exact argv without ffmpeg being installed or invoked; ``assemble_video``
takes an injectable ``runner`` (defaults to subprocess.run) for the same
reason. No command ever uses shell=True.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from horrorvibes.exceptions import VideoAssemblyError

logger = logging.getLogger(__name__)

Runner = Callable[..., Any]


def build_concat_file_content(frame_paths: list[Path], durations_sec: list[float]) -> str:
    """ffmpeg concat-demuxer file listing each frame with its own display
    duration -- frames aren't necessarily uniform length, e.g. a quote whose
    narration runs long needs its frame held on screen longer so the next
    quote's narration doesn't start before this one finishes.

    The last frame is repeated without a duration line, which the concat
    demuxer requires to know how long to hold the final frame.
    """
    if len(frame_paths) != len(durations_sec):
        raise ValueError("frame_paths and durations_sec must be the same length")

    lines = []
    for path, duration_sec in zip(frame_paths, durations_sec):
        lines.append(f"file '{os.path.abspath(str(path))}'")
        lines.append(f"duration {duration_sec}")
    if frame_paths:
        lines.append(f"file '{os.path.abspath(str(frame_paths[-1]))}'")
    return "\n".join(lines) + "\n"


def build_silent_video_cmd(concat_file: Path, output_path: Path, fps: int) -> list[str]:
    """ffmpeg argv to build a silent video from the concat-demuxer file list."""
    return [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "23",
        "-r", str(fps),
        str(output_path),
    ]


def build_mux_audio_cmd(video_path: Path, audio_path: Path, output_path: Path) -> list[str]:
    """ffmpeg argv to mux ``audio_path`` onto ``video_path`` without re-encoding video."""
    return [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]


def assemble_video(  # pylint: disable=too-many-locals
    frame_paths: list[Path],
    output_path: Path,
    audio_path: Path | None,
    durations_sec: list[float],
    fps: int,
    runner: Runner,
) -> Path:
    """Build the silent video from frames, mux in audio if provided, and
    atomically publish the result to ``output_path`` (write-temp + rename)."""
    if not frame_paths:
        raise VideoAssemblyError("No frames provided to assemble")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        concat_file = temp_dir_path / "concat.txt"
        concat_file.write_text(build_concat_file_content(frame_paths, durations_sec), encoding="utf-8")

        silent_path = temp_dir_path / "silent.mp4"
        silent_cmd = build_silent_video_cmd(concat_file, silent_path, fps)
        silent_result = runner(silent_cmd, capture_output=True, text=True, check=False)
        if silent_result.returncode != 0:
            raise VideoAssemblyError(f"ffmpeg failed to build silent video: {silent_result.stderr}")

        final_source = silent_path
        if audio_path is not None:
            muxed_path = temp_dir_path / "muxed.mp4"
            mux_cmd = build_mux_audio_cmd(silent_path, audio_path, muxed_path)
            mux_result = runner(mux_cmd, capture_output=True, text=True, check=False)
            if mux_result.returncode != 0:
                logger.warning("Audio mux failed, publishing silent video instead: %s", mux_result.stderr)
            else:
                final_source = muxed_path

        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_output = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp_output.write_bytes(final_source.read_bytes())
        os.replace(tmp_output, output_path)

    logger.info("Video assembled at %s", output_path)
    return output_path
