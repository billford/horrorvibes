"""Assembles frames + audio into an MP4 via ffmpeg (concat demuxer + mux).

Command construction is split into pure functions so tests can assert on
the exact argv without ffmpeg being installed or invoked; ``assemble_video``
takes an injectable ``runner`` (defaults to subprocess.run) for the same
reason. No command ever uses shell=True.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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


# ---- animated assembly ------------------------------------------------------------
#
# Each quote becomes its own clip (background zoom/pan + grade + text layers
# + film texture), rendered in parallel, then the clips are chained with
# xfade transitions and the audio is muxed in. Clip i (except the last) is
# rendered transition_sec longer than its on-screen slot so that each xfade
# *starts* exactly at the next quote's start offset -- keeping every quote
# aligned with its narration and the total length equal to sum(durations).

_TEXTURES = {
    "none": "",
    "grain": "noise=alls=12:allf=t+u",
    "vhs": "noise=alls=20:allf=t+u,rgbashift=rh=3:bh=-3,eq=saturation=0.85",
}


@dataclass(frozen=True)
class ClipSpec:
    """Inputs for one quote's clip. ``state_times[k]`` is when (seconds into
    the clip) ``word_states[k]`` becomes the visible caption."""

    background: Path
    title: Path
    word_states: list[Path]
    state_times: list[float]
    duration: float
    zoom_in: bool = True


@dataclass(frozen=True)
class MotionStyle:  # pylint: disable=too-many-instance-attributes
    """The bits of the style pack + motion config the video step needs."""

    grade: str = ""
    overlay: str = "grain"
    vignette: bool = True
    zoom_amount: float = 0.10
    upscale_factor: float = 1.5
    transition_sec: float = 0.8
    crf: int = 20
    preset: str = "medium"


def _even(value: float) -> int:
    return int(value) // 2 * 2


def effective_transition_sec(durations: list[float], requested: float) -> float:
    """A transition can't be longer than half the shortest clip."""
    if len(durations) < 2:
        return 0.0
    return round(max(0.0, min(requested, min(durations) / 2)), 3)


def build_clip_filter(  # pylint: disable=too-many-arguments,too-many-locals
    spec: ClipSpec, clip_len: float, style: MotionStyle, width: int, height: int, fps: int
) -> str:
    """filter_complex for one clip. Input 0 is the background (single
    frame -- zoompan emits ``d`` frames per input frame), input 1 the title
    layer, inputs 2.. the caption states."""
    frames = max(1, round(clip_len * fps))
    up_w, up_h = _even(width * style.upscale_factor), _even(height * style.upscale_factor)
    zoom = style.zoom_amount
    zexpr = f"1+{zoom}*on/{frames}" if spec.zoom_in else f"1+{zoom}-{zoom}*on/{frames}"
    background = (
        f"[0:v]scale={up_w}:{up_h}:force_original_aspect_ratio=increase,crop={up_w}:{up_h},setsar=1,"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={width}x{height}:fps={fps}"
    )
    if style.grade:
        background += f",{style.grade}"
    parts = [
        background + "[bg]",
        "[1:v]format=rgba,fade=t=in:st=0.2:d=0.8:alpha=1[title]",
        "[bg][title]overlay=0:0[v0]",
    ]

    current = "v0"
    for k, start in enumerate(spec.state_times):
        end = spec.state_times[k + 1] if k + 1 < len(spec.state_times) else clip_len + 1
        label = f"v{k + 1}"
        if len(spec.word_states) == 1:
            parts.append(f"[{k + 2}:v]format=rgba,fade=t=in:st={start:.3f}:d=0.6:alpha=1[s{k}]")
            parts.append(f"[{current}][s{k}]overlay=0:0[{label}]")
        else:
            parts.append(
                f"[{current}][{k + 2}:v]overlay=0:0:"
                f"enable='between(t,{start:.3f},{end - 0.001:.3f})'[{label}]"
            )
        current = label

    finish = [f for f in (_TEXTURES.get(style.overlay, ""), "vignette=PI/5" if style.vignette else "") if f]
    finish.append("format=yuv420p")
    parts.append(f"[{current}]{','.join(finish)}[out]")
    return ";".join(parts)


def build_clip_cmd(  # pylint: disable=too-many-arguments
    spec: ClipSpec, clip_len: float, style: MotionStyle, width: int, height: int, fps: int, output_path: Path
) -> list[str]:
    """ffmpeg argv to render one silent clip."""
    looped = ["-loop", "1", "-framerate", str(fps), "-t", f"{clip_len:.3f}", "-i"]
    inputs = ["-i", str(spec.background), *looped, str(spec.title)]
    for state in spec.word_states:
        inputs += [*looped, str(state)]
    return [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", build_clip_filter(spec, clip_len, style, width, height, fps),
        "-map", "[out]",
        "-t", f"{clip_len:.3f}",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", style.preset, "-crf", str(style.crf), "-pix_fmt", "yuv420p",
        str(output_path),
    ]


def build_xfade_cmd(  # pylint: disable=too-many-arguments,too-many-locals
    clip_paths: list[Path],
    durations: list[float],
    transitions: list[str],
    transition_sec: float,
    audio_path: Path | None,
    output_path: Path,
    style: MotionStyle,
) -> list[str]:
    """ffmpeg argv chaining clips with xfade and muxing the audio track.
    ``transitions[k]`` is used for the join into clip k+1."""
    inputs: list[str] = []
    for path in clip_paths:
        inputs += ["-i", str(path)]

    parts = []
    current = "0:v"
    offset = 0.0
    for k in range(1, len(clip_paths)):
        offset += durations[k - 1]
        label = f"x{k}"
        parts.append(
            f"[{current}][{k}:v]xfade=transition={transitions[k - 1]}:duration={transition_sec}:"
            f"offset={offset:.3f}[{label}]"
        )
        current = label
    parts.append(f"[{current}]format=yuv420p[vout]")

    cmd = ["ffmpeg", "-y", *inputs]
    audio_map: list[str] = []
    if audio_path is not None:
        cmd += ["-i", str(audio_path)]
        audio_map = ["-map", f"{len(clip_paths)}:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    return [
        *cmd,
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", *audio_map,
        "-c:v", "libx264", "-preset", style.preset, "-crf", str(style.crf), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]


def assemble_motion_video(  # pylint: disable=too-many-arguments,too-many-locals
    clips: list[ClipSpec],
    output_path: Path,
    audio_path: Path | None,
    style: MotionStyle,
    transitions: list[str],
    width: int,
    height: int,
    fps: int,
    runner: Runner,
    max_workers: int = 4,
) -> Path:
    """Render every clip (in parallel), chain them with transitions, mux
    audio, and atomically publish to ``output_path``. Raises
    VideoAssemblyError on any ffmpeg failure so the caller can fall back to
    the static assembly."""
    if not clips:
        raise VideoAssemblyError("No clips provided to assemble")
    durations = [clip.duration for clip in clips]
    transition_sec = effective_transition_sec(durations, style.transition_sec)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        jobs = []
        for i, clip in enumerate(clips):
            clip_len = clip.duration + (transition_sec if i < len(clips) - 1 else 0.0)
            clip_path = temp / f"clip_{i:02d}.mp4"
            jobs.append((clip_path, build_clip_cmd(clip, clip_len, style, width, height, fps, clip_path)))

        def render(job: tuple[Path, list[str]]) -> None:
            path, cmd = job
            result = runner(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise VideoAssemblyError(f"ffmpeg failed rendering {path.name}: {result.stderr[-800:]}")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(render, jobs))
        logger.info("Rendered %d animated clips", len(jobs))

        joined = temp / "joined.mp4"
        cmd = build_xfade_cmd(
            [path for path, _ in jobs], durations, transitions, transition_sec, audio_path, joined, style
        )
        result = runner(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise VideoAssemblyError(f"ffmpeg failed joining clips: {result.stderr[-800:]}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_output = output_path.with_suffix(output_path.suffix + ".tmp")
        shutil.copyfile(joined, tmp_output)
        os.replace(tmp_output, output_path)

    logger.info("Animated video assembled at %s", output_path)
    return output_path
