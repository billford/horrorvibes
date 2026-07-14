# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from horrorvibes.exceptions import VideoAssemblyError
from horrorvibes.video import (
    assemble_video,
    build_concat_file_content,
    build_mux_audio_cmd,
    build_silent_video_cmd,
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
