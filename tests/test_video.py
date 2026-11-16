# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from horrorvibes.exceptions import VideoAssemblyError
from horrorvibes.video import (
    ClipSpec,
    MotionStyle,
    assemble_motion_video,
    assemble_video,
    build_clip_cmd,
    build_concat_file_content,
    build_mux_audio_cmd,
    build_silent_video_cmd,
    build_xfade_cmd,
    effective_transition_sec,
)


def test_build_concat_file_content_repeats_last_frame_without_duration():
    frames = [Path("/tmp/a.png"), Path("/tmp/b.png")]
    content = build_concat_file_content(frames, [10, 10])
    lines = content.splitlines()

    abs_a = os.path.abspath("/tmp/a.png")
    abs_b = os.path.abspath("/tmp/b.png")
    assert lines == [
        f"file '{abs_a}'",
        "duration 10",
        f"file '{abs_b}'",
        "duration 10",
        f"file '{abs_b}'",
    ]


def test_build_concat_file_content_supports_per_frame_durations():
    frames = [Path("/tmp/a.png"), Path("/tmp/b.png")]
    content = build_concat_file_content(frames, [10, 15.5])
    lines = content.splitlines()

    assert "duration 10" in lines
    assert "duration 15.5" in lines


def test_build_concat_file_content_empty_frames():
    assert build_concat_file_content([], []) == "\n"


def test_build_concat_file_content_raises_on_length_mismatch():
    with pytest.raises(ValueError):
        build_concat_file_content([Path("/tmp/a.png")], [10, 20])


def test_build_silent_video_cmd_has_no_shell_and_uses_concat_demuxer():
    cmd = build_silent_video_cmd(Path("/tmp/concat.txt"), Path("/tmp/out.mp4"), fps=30)
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat"
    assert str(Path("/tmp/out.mp4")) == cmd[-1]


def test_build_mux_audio_cmd_uses_shortest_and_copies_video_codec():
    cmd = build_mux_audio_cmd(Path("/tmp/v.mp4"), Path("/tmp/a.mp3"), Path("/tmp/out.mp4"))
    assert "-shortest" in cmd
    assert "copy" in cmd


class FakeRunner:
    """Records calls and simulates ffmpeg writing bytes to its output path."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, cmd, capture_output=True, text=True, check=False):
        self.calls.append(cmd)
        returncode, output_bytes = self._results.pop(0)
        if returncode == 0 and output_bytes is not None:
            Path(cmd[-1]).write_bytes(output_bytes)
        return SimpleNamespace(returncode=returncode, stdout="", stderr="simulated ffmpeg error")


def test_assemble_video_success_with_audio(tmp_path):
    frame = tmp_path / "frame1.png"
    frame.write_bytes(b"frame")
    audio = tmp_path / "music.mp3"
    audio.write_bytes(b"music")
    output_path = tmp_path / "out.mp4"

    runner = FakeRunner([(0, b"silent-video"), (0, b"muxed-video")])
    result = assemble_video([frame], output_path, audio, [10], fps=30, runner=runner)

    assert result == output_path
    assert output_path.read_bytes() == b"muxed-video"
    assert len(runner.calls) == 2
    assert not output_path.with_suffix(output_path.suffix + ".tmp").exists()


def test_assemble_video_without_audio_only_builds_silent_video(tmp_path):
    frame = tmp_path / "frame1.png"
    frame.write_bytes(b"frame")
    output_path = tmp_path / "out.mp4"

    runner = FakeRunner([(0, b"silent-video")])
    result = assemble_video([frame], output_path, None, [10], fps=30, runner=runner)

    assert result.read_bytes() == b"silent-video"
    assert len(runner.calls) == 1


def test_assemble_video_raises_when_silent_video_step_fails(tmp_path):
    frame = tmp_path / "frame1.png"
    frame.write_bytes(b"frame")
    output_path = tmp_path / "out.mp4"

    runner = FakeRunner([(1, None)])
    with pytest.raises(VideoAssemblyError):
        assemble_video([frame], output_path, None, [10], fps=30, runner=runner)


def test_assemble_video_falls_back_to_silent_video_when_mux_fails(tmp_path, caplog):
    frame = tmp_path / "frame1.png"
    frame.write_bytes(b"frame")
    audio = tmp_path / "music.mp3"
    audio.write_bytes(b"music")
    output_path = tmp_path / "out.mp4"

    runner = FakeRunner([(0, b"silent-video"), (1, None)])
    with caplog.at_level("WARNING"):
        result = assemble_video([frame], output_path, audio, [10], fps=30, runner=runner)

    assert result.read_bytes() == b"silent-video"
    assert any("mux" in record.message.lower() for record in caplog.records)


def test_assemble_video_raises_on_empty_frame_list(tmp_path):
    runner = FakeRunner([])
    with pytest.raises(VideoAssemblyError):
        assemble_video([], tmp_path / "out.mp4", None, [], fps=30, runner=runner)
    assert runner.calls == []


# ---- animated assembly -------------------------------------------------------------



def _clip(tmp_path, n_states=3, duration=5.0):
    states = [tmp_path / f"s{k}.png" for k in range(n_states)]
    return ClipSpec(
        background=tmp_path / "bg.png", title=tmp_path / "t.png", word_states=states,
        state_times=[0.0, 1.0, 2.0][:n_states], duration=duration,
    )


def test_effective_transition_is_clamped_to_half_shortest_clip():
    assert effective_transition_sec([10, 1.0, 8], 0.8) == 0.5
    assert effective_transition_sec([10, 10], 0.8) == 0.8
    assert effective_transition_sec([10], 0.8) == 0.0


def test_clip_cmd_feeds_background_once_and_loops_layers(tmp_path):
    style = MotionStyle(grade="eq=saturation=0.5")
    cmd = build_clip_cmd(_clip(tmp_path), 5.8, style, 1080, 1920, 30, tmp_path / "c.mp4")
    bg_index = cmd.index(str(tmp_path / "bg.png"))
    # not looped: zoompan emits d frames per input frame
    assert cmd[bg_index - 1] == "-i" and cmd[bg_index - 2] != "1"
    assert cmd.count("-loop") == 4  # title + 3 caption states
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "zoompan=z='1+0.1*on/174'" in graph and "d=174" in graph
    assert "eq=saturation=0.5" in graph
    assert "enable='between(t,1.000,1.999)'" in graph
    assert "noise=" in graph and "vignette" in graph


def test_clip_filter_static_caption_fades_in(tmp_path):
    spec = dataclasses.replace(_clip(tmp_path, n_states=1), state_times=[0.4])
    style = MotionStyle(overlay="none", vignette=False)
    cmd = build_clip_cmd(spec, 5, style, 1080, 1920, 30, tmp_path / "c.mp4")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "fade=t=in:st=0.400" in graph and "noise=" not in graph and "vignette" not in graph


def test_xfade_offsets_start_each_transition_at_the_next_quote(tmp_path):
    clips = [tmp_path / f"c{i}.mp4" for i in range(3)]
    cmd = build_xfade_cmd(
        clips, [4.0, 6.5, 3.0], ["fade", "pixelize"], 0.8,
        tmp_path / "a.mp3", tmp_path / "o.mp4", MotionStyle(),
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.8:offset=4.000" in graph
    assert "xfade=transition=pixelize:duration=0.8:offset=10.500" in graph
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "3:a"


def test_assemble_motion_video_renders_each_clip_then_joins(tmp_path):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"x")
        return SimpleNamespace(returncode=0, stderr="")

    out = assemble_motion_video(
        [_clip(tmp_path), _clip(tmp_path)], tmp_path / "out.mp4", None, MotionStyle(), ["fade"],
        1080, 1920, 30, runner=runner,
    )
    assert out.read_bytes() == b"x"
    assert len(calls) == 3 and "xfade" in " ".join(calls[-1])
    first_clip_len = calls[0][calls[0].index("-t", calls[0].index("-map")) + 1]
    assert first_clip_len == "5.800"  # duration + transition, so the xfade starts on time


def test_assemble_motion_video_raises_on_ffmpeg_failure(tmp_path):
    def runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stderr="boom")

    with pytest.raises(VideoAssemblyError):
        assemble_motion_video(
            [_clip(tmp_path)], tmp_path / "o.mp4", None, MotionStyle(), [], 1080, 1920, 30, runner
        )
