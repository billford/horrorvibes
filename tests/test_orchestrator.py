# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import dataclasses
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from horrorvibes import imagegen, musicgen, publish, video
from horrorvibes.config import load_config
from horrorvibes.exceptions import PublishError, QuoteGenerationError
from horrorvibes.orchestrator import (
    is_run_due,
    main_unattended,
    notify_failure,
    record_run,
    run_pipeline,
)


def _response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeChatClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):  # pylint: disable=unused-argument
        return _response(self._responses.pop(0))


@pytest.fixture
def config(tmp_path):
    base = load_config(tmp_path / "missing.yaml")
    rooted_run = dataclasses.replace(
        base.run,
        quote_count=2,
        quotes_dir=tmp_path / "quotes",
        images_dir=tmp_path / "images",
        frames_dir=tmp_path / "frames",
        output_dir=tmp_path / "output",
        quotes_history_path=tmp_path / "quotes_history.txt",
    )
    rooted_music = dataclasses.replace(base.music, fallback_dir=tmp_path / "audio")
    rooted_publish = dataclasses.replace(
        base.publish, token_path=tmp_path / "token.json", client_secrets_path=tmp_path / "client_secret.json"
    )
    rooted_automation = dataclasses.replace(
        base.automation, state_file=tmp_path / "logs" / "last_run.json"
    )
    return dataclasses.replace(
        base, run=rooted_run, music=rooted_music, publish=rooted_publish, automation=rooted_automation
    )


# ---- is_run_due / record_run -------------------------------------------------


def test_is_run_due_true_when_no_state_file(config):
    assert is_run_due(config.automation) is True


def test_is_run_due_false_when_run_was_recent(config):
    record_run(config.automation.state_file, datetime(2026, 1, 1, 12, 0, 0))
    assert is_run_due(config.automation, now=lambda: datetime(2026, 1, 1, 13, 0, 0)) is False


def test_is_run_due_true_when_run_was_long_ago(config):
    record_run(config.automation.state_file, datetime(2026, 1, 1, 0, 0, 0))
    later = datetime(2026, 1, 1, 0, 0, 0) + timedelta(hours=config.automation.min_hours_between_runs + 1)
    assert is_run_due(config.automation, now=lambda: later) is True


def test_is_run_due_true_when_state_file_is_corrupt(config, caplog):
    config.automation.state_file.parent.mkdir(parents=True, exist_ok=True)
    config.automation.state_file.write_text("not json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert is_run_due(config.automation) is True
    assert any("Could not parse" in r.message for r in caplog.records)


def test_record_run_is_atomic_and_leaves_no_tmp_files(config):
    record_run(config.automation.state_file, datetime(2026, 1, 1))
    assert config.automation.state_file.exists()
    assert list(config.automation.state_file.parent.glob(".last_run_*.tmp")) == []


# ---- notify_failure -----------------------------------------------------------


def test_notify_failure_invokes_osascript_with_escaped_message():
    calls = []

    def fake_runner(cmd, **kwargs):  # pylint: disable=unused-argument
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    notify_failure('bad "quotes" here', runner=fake_runner)

    assert calls[0][0] == "osascript"
    assert "quotes" in calls[0][2]
    assert "display notification" in calls[0][2]


def test_notify_failure_never_raises_on_runner_error():
    def fake_runner(cmd, **kwargs):  # pylint: disable=unused-argument
        raise OSError("osascript not found")

    notify_failure("message", runner=fake_runner)  # should not raise


# ---- run_pipeline --------------------------------------------------------------


def test_run_pipeline_generates_expected_number_of_frames_and_video(config, monkeypatch):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])

    def fake_generate_image(quote, index, cfg, images_dir):  # pylint: disable=unused-argument
        path = images_dir / f"background_{index + 1}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"img")
        return imagegen.ImageResult(path=path, backend_used="gradient")

    def fake_generate_music(cfg, output_path, **kwargs):  # pylint: disable=unused-argument
        output_path.write_bytes(b"music")
        return musicgen.MusicResult(path=output_path, backend_used="curated_file")

    def fake_assemble_video(frame_paths, output_path, audio_path, duration_per_frame, fps, runner):
        output_path.write_bytes(b"video")
        return output_path

    def fake_compose_frame(image_path, quote, comp_cfg, width, height, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"frame")
        return output_path

    monkeypatch.setattr(imagegen, "generate_image", fake_generate_image)
    monkeypatch.setattr(musicgen, "generate_music", fake_generate_music)
    monkeypatch.setattr(video, "assemble_video", fake_assemble_video)
    monkeypatch.setattr("horrorvibes.orchestrator.compositor.compose_frame", fake_compose_frame)

    config = dataclasses.replace(config, publish=dataclasses.replace(config.publish, youtube_upload=False))

    result = run_pipeline(config, chat_client, now=lambda: datetime(2026, 1, 1, 9, 0, 0))

    assert result.quote_count == 2
    assert result.youtube_video_id is None
    assert result.video_path.exists()
    assert len(list(config.run.frames_dir.glob("frame_*.png"))) == 2


def test_run_pipeline_skips_upload_and_logs_when_publish_fails(config, monkeypatch, caplog):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])

    monkeypatch.setattr(
        imagegen,
        "generate_image",
        lambda quote, index, cfg, images_dir: imagegen.ImageResult(
            path=_write(images_dir / f"background_{index + 1}.png"), backend_used="gradient"
        ),
    )
    monkeypatch.setattr(
        musicgen,
        "generate_music",
        lambda cfg, output_path, **kwargs: musicgen.MusicResult(
            path=_write(output_path), backend_used="curated_file"
        ),
    )
    monkeypatch.setattr(
        video,
        "assemble_video",
        lambda frame_paths, output_path, audio_path, duration_per_frame, fps, runner: _write(output_path),
    )
    monkeypatch.setattr(
        "horrorvibes.orchestrator.compositor.compose_frame",
        lambda image_path, quote, comp_cfg, width, height, output_path: _write(output_path),
    )

    def failing_publish(*args, **kwargs):  # pylint: disable=unused-argument
        raise PublishError("no refresh token")

    monkeypatch.setattr(publish, "publish", failing_publish)

    config = dataclasses.replace(config, publish=dataclasses.replace(config.publish, youtube_upload=True))

    with caplog.at_level("ERROR"):
        result = run_pipeline(config, chat_client, now=lambda: datetime(2026, 1, 1, 9, 0, 0))

    assert result.youtube_video_id is None
    assert result.video_path.exists()
    assert any("upload failed" in r.message.lower() for r in caplog.records)


def _write(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


# ---- main_unattended ------------------------------------------------------------


def test_main_unattended_skips_when_not_due(config, monkeypatch):
    record_run(config.automation.state_file, datetime.now())

    def fail_if_called(*args, **kwargs):
        raise AssertionError("run_pipeline should not have been called")

    monkeypatch.setattr("horrorvibes.orchestrator.run_pipeline", fail_if_called)

    assert main_unattended(config, chat_client=None) == 0


def test_main_unattended_returns_1_and_notifies_on_failure(config, monkeypatch):
    def raising_run_pipeline(*args, **kwargs):
        raise QuoteGenerationError("no quotes available")

    notified = []
    monkeypatch.setattr("horrorvibes.orchestrator.run_pipeline", raising_run_pipeline)
    monkeypatch.setattr("horrorvibes.orchestrator.notify_failure", lambda msg: notified.append(msg))

    exit_code = main_unattended(config, chat_client=None, force=True)

    assert exit_code == 1
    assert notified and "no quotes available" in notified[0]


def test_main_unattended_returns_0_and_records_run_on_success(config, monkeypatch):
    fake_result = SimpleNamespace(video_path="out.mp4", quote_count=2, youtube_video_id=None)
    monkeypatch.setattr("horrorvibes.orchestrator.run_pipeline", lambda *a, **k: fake_result)

    exit_code = main_unattended(config, chat_client=None, force=True)

    assert exit_code == 0
    assert config.automation.state_file.exists()
