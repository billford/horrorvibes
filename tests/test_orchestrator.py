# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import dataclasses
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from horrorvibes import imagegen, musicgen, publish, video, voiceover
from horrorvibes.config import load_config
from horrorvibes.exceptions import PublishError, QuoteGenerationError
from horrorvibes.orchestrator import (
    _archive_completed_video,
    _compute_quote_schedule,
    _external_drive_available,
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
        video_catalog_path=tmp_path / "video_catalog.jsonl",
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


# ---- _compute_quote_schedule ---------------------------------------------------


def test_compute_quote_schedule_uses_nominal_duration_when_voiceover_disabled(config):
    narrations = [None, None, None]
    config = dataclasses.replace(config, run=dataclasses.replace(config.run, duration_per_quote_sec=10))

    quote_durations, start_offsets, narration_durations = _compute_quote_schedule(config, narrations)

    assert quote_durations == [10.0, 10.0, 10.0]
    assert start_offsets == [0.0, 10.0, 20.0]
    assert narration_durations == [None, None, None]


def test_compute_quote_schedule_keeps_nominal_duration_for_short_narration(config, monkeypatch, tmp_path):
    config = dataclasses.replace(config, run=dataclasses.replace(config.run, duration_per_quote_sec=10))
    monkeypatch.setattr(voiceover, "narration_duration_sec", lambda path: 3.0)  # well under 10s

    quote_durations, start_offsets, _ = _compute_quote_schedule(config, [tmp_path / "n1.mp3", None])

    assert quote_durations == [10.0, 10.0]
    assert start_offsets == [0.0, 10.0]


def test_compute_quote_schedule_stretches_duration_for_long_narration(config, monkeypatch, tmp_path):
    """Regression test: this is the actual overlap bug -- a quote whose
    narration runs longer than duration_per_quote_sec must get a longer
    on-screen slot (plus padding), so the *next* quote's narration doesn't
    start until this one has actually finished."""
    config = dataclasses.replace(
        config,
        run=dataclasses.replace(config.run, duration_per_quote_sec=10),
        voiceover=dataclasses.replace(config.voiceover, narration_pad_sec=1.0),
    )
    durations_by_path = {"n1.mp3": 15.0, "n2.mp3": 4.0}
    monkeypatch.setattr(voiceover, "narration_duration_sec", lambda path: durations_by_path[path.name])

    quote_durations, start_offsets, narration_durations = _compute_quote_schedule(
        config, [tmp_path / "n1.mp3", tmp_path / "n2.mp3"]
    )

    assert quote_durations == [16.0, 10.0]  # 15.0 + 1.0 pad, then back to nominal
    assert start_offsets == [0.0, 16.0]  # second quote starts after the first's stretched slot
    assert narration_durations == [15.0, 4.0]


def test_compute_quote_schedule_treats_failed_narration_as_nominal_duration(config, monkeypatch, tmp_path):
    config = dataclasses.replace(config, run=dataclasses.replace(config.run, duration_per_quote_sec=10))
    monkeypatch.setattr(voiceover, "narration_duration_sec", lambda path: 15.0)

    quote_durations, start_offsets, narration_durations = _compute_quote_schedule(
        config, [None, tmp_path / "n2.mp3"]
    )

    assert quote_durations == [10.0, 16.0]
    assert start_offsets == [0.0, 10.0]
    assert narration_durations == [None, 15.0]


# ---- run_pipeline --------------------------------------------------------------


def test_run_pipeline_generates_expected_number_of_frames_and_video(config, monkeypatch):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])

    def fake_generate_image(quote, index, cfg, images_dir, backends=None):
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

    catalog_lines = config.run.video_catalog_path.read_text(encoding="utf-8").splitlines()
    assert len(catalog_lines) == 1
    entry = json.loads(catalog_lines[0])
    assert entry["youtube_video_id"] is None
    assert entry["quotes"] == [
        {"quote": "'A'", "movie": "Movie1"},
        {"quote": "'B'", "movie": "Movie2"},
    ]


def test_run_pipeline_uses_content_specific_youtube_metadata(config, monkeypatch):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    _mock_non_voiceover_stages(monkeypatch)

    published = {}

    def fake_publish(video_path, title, description, tags, publish_config):  # pylint: disable=unused-argument
        published["title"] = title
        published["description"] = description
        published["tags"] = tags
        return "yt-video-id"

    monkeypatch.setattr(publish, "publish", fake_publish)

    config = dataclasses.replace(config, publish=dataclasses.replace(config.publish, youtube_upload=True))
    result = run_pipeline(config, chat_client)

    assert result.youtube_video_id == "yt-video-id"
    assert "Movie1" in published["title"]
    assert "Movie1" in published["description"]
    assert "'A'" in published["description"]
    assert "horror" in published["tags"]

    entry = json.loads(config.run.video_catalog_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["youtube_video_id"] == "yt-video-id"


def test_run_pipeline_reuses_one_image_backend_set_across_all_quotes(config, monkeypatch):
    """Regression test: run_pipeline must build the image backend chain ONCE
    per run, not once per quote -- a prior version rebuilt it per-image,
    which threw away LocalSDBackend's cached, lazily-loaded pipeline and
    reloaded the model from disk before every single image."""
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2\n'C' - Movie3"])
    default_backends_calls = []

    def fake_default_backends(cfg):
        default_backends_calls.append(cfg)
        return {"local_sd": lambda prompt, index, output_path: _write(output_path)}

    monkeypatch.setattr(imagegen, "default_backends", fake_default_backends)
    monkeypatch.setattr(
        "horrorvibes.orchestrator.compositor.compose_frame",
        lambda image_path, quote, comp_cfg, width, height, output_path: _write(output_path),
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

    config = dataclasses.replace(
        config,
        run=dataclasses.replace(config.run, quote_count=3),
        publish=dataclasses.replace(config.publish, youtube_upload=False),
    )
    run_pipeline(config, chat_client)

    assert len(default_backends_calls) == 1


def test_run_pipeline_forwards_elevenlabs_api_key_to_generate_music(config, monkeypatch):
    """Regression test: run_pipeline must thread elevenlabs_api_key through
    to musicgen.generate_music -- a prior version silently dropped it,
    sending every ElevenLabs request with no API key (a 401, not a config
    error)."""
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    received_kwargs = {}

    monkeypatch.setattr(
        imagegen,
        "generate_image",
        lambda quote, index, cfg, images_dir, backends=None: imagegen.ImageResult(
            path=_write(images_dir / f"background_{index + 1}.png"), backend_used="gradient"
        ),
    )
    monkeypatch.setattr(
        "horrorvibes.orchestrator.compositor.compose_frame",
        lambda image_path, quote, comp_cfg, width, height, output_path: _write(output_path),
    )
    monkeypatch.setattr(
        video,
        "assemble_video",
        lambda frame_paths, output_path, audio_path, duration_per_frame, fps, runner: _write(output_path),
    )

    def fake_generate_music(cfg, output_path, **kwargs):
        received_kwargs.update(kwargs)
        return musicgen.MusicResult(path=_write(output_path), backend_used="elevenlabs")

    monkeypatch.setattr(musicgen, "generate_music", fake_generate_music)

    config = dataclasses.replace(config, publish=dataclasses.replace(config.publish, youtube_upload=False))
    run_pipeline(config, chat_client, elevenlabs_api_key="the-real-key")

    assert received_kwargs.get("api_key") == "the-real-key"


def _mock_non_voiceover_stages(monkeypatch):
    monkeypatch.setattr(
        imagegen,
        "generate_image",
        lambda quote, index, cfg, images_dir, backends=None: imagegen.ImageResult(
            path=_write(images_dir / f"background_{index + 1}.png"), backend_used="gradient"
        ),
    )
    monkeypatch.setattr(
        "horrorvibes.orchestrator.compositor.compose_frame",
        lambda image_path, quote, comp_cfg, width, height, output_path: _write(output_path),
    )
    monkeypatch.setattr(
        musicgen,
        "generate_music",
        lambda cfg, output_path, **kwargs: musicgen.MusicResult(
            path=_write(output_path), backend_used="curated_file"
        ),
    )
    recorded_video_calls = []
    recorded_durations = []

    def fake_assemble_video(frame_paths, output_path, audio_path, durations_sec, fps, runner):
        recorded_video_calls.append(audio_path)
        recorded_durations.append(durations_sec)
        return _write(output_path)

    monkeypatch.setattr(video, "assemble_video", fake_assemble_video)
    return recorded_video_calls, recorded_durations


def test_run_pipeline_uses_plain_music_when_voiceover_disabled(config, monkeypatch):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    video_calls, _video_durations = _mock_non_voiceover_stages(monkeypatch)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("generate_narrations should not run when voiceover is disabled")

    monkeypatch.setattr(voiceover, "generate_narrations", fail_if_called)

    config = dataclasses.replace(config, publish=dataclasses.replace(config.publish, youtube_upload=False))
    run_pipeline(config, chat_client)

    assert video_calls[0].name == "_music.mp3"


def test_run_pipeline_mixes_narration_when_voiceover_enabled(config, monkeypatch):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    video_calls, _video_durations = _mock_non_voiceover_stages(monkeypatch)

    monkeypatch.setattr(
        voiceover,
        "generate_narrations",
        lambda voiceover_cfg, quotes, output_dir, api_key=None, backend=None: [
            _write(output_dir / "narration_1.mp3"),
            _write(output_dir / "narration_2.mp3"),
        ],
    )
    monkeypatch.setattr(voiceover, "narration_duration_sec", lambda path: 2.0)

    def fake_ffmpeg_run(cmd, capture_output=True, text=True, check=False):
        Path(cmd[-1]).write_bytes(b"mixed")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("horrorvibes.orchestrator.subprocess.run", fake_ffmpeg_run)

    config = dataclasses.replace(
        config,
        publish=dataclasses.replace(config.publish, youtube_upload=False),
        voiceover=dataclasses.replace(config.voiceover, enabled=True),
    )
    run_pipeline(config, chat_client)

    assert video_calls[0].name == "_music_with_narration.mp3"


def test_run_pipeline_stretches_video_and_music_for_long_narration(config, monkeypatch):
    """Regression test for the actual overlap bug end-to-end: a long
    narration must stretch both the per-frame video durations passed to
    video.assemble_video and the total duration_sec passed to
    musicgen.generate_music -- not just the internal offset math."""
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    _video_calls, video_durations = _mock_non_voiceover_stages(monkeypatch)

    monkeypatch.setattr(
        voiceover,
        "generate_narrations",
        lambda voiceover_cfg, quotes, output_dir, api_key=None, backend=None: [
            _write(output_dir / "narration_1.mp3"),
            _write(output_dir / "narration_2.mp3"),
        ],
    )
    durations_by_name = {"narration_1.mp3": 15.0, "narration_2.mp3": 2.0}
    monkeypatch.setattr(voiceover, "narration_duration_sec", lambda path: durations_by_name[path.name])

    music_kwargs = {}

    def fake_generate_music(cfg, output_path, **kwargs):
        music_kwargs.update(kwargs)
        return musicgen.MusicResult(path=_write(output_path), backend_used="curated_file")

    monkeypatch.setattr(musicgen, "generate_music", fake_generate_music)

    def fake_ffmpeg_run(cmd, capture_output=True, text=True, check=False):
        Path(cmd[-1]).write_bytes(b"mixed")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("horrorvibes.orchestrator.subprocess.run", fake_ffmpeg_run)

    config = dataclasses.replace(
        config,
        run=dataclasses.replace(config.run, duration_per_quote_sec=10),
        publish=dataclasses.replace(config.publish, youtube_upload=False),
        voiceover=dataclasses.replace(config.voiceover, enabled=True, narration_pad_sec=1.0),
    )
    run_pipeline(config, chat_client)

    assert video_durations[0] == [16.0, 10.0]  # 15.0 + 1.0 pad, then the second quote back to nominal
    assert music_kwargs["duration_sec"] == 26.0  # 16.0 + 10.0 total, not the nominal 2 * 10 = 20


def test_run_pipeline_falls_back_to_music_when_all_narrations_fail(config, monkeypatch, caplog):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    video_calls, _video_durations = _mock_non_voiceover_stages(monkeypatch)

    monkeypatch.setattr(
        voiceover,
        "generate_narrations",
        lambda voiceover_cfg, quotes, output_dir, api_key=None, backend=None: [None, None],
    )

    config = dataclasses.replace(
        config,
        publish=dataclasses.replace(config.publish, youtube_upload=False),
        voiceover=dataclasses.replace(config.voiceover, enabled=True),
    )
    with caplog.at_level("WARNING"):
        run_pipeline(config, chat_client)

    assert video_calls[0].name == "_music.mp3"
    assert any("every quote's narration failed" in r.message for r in caplog.records)


def test_run_pipeline_falls_back_to_music_when_mix_ffmpeg_fails(config, monkeypatch, caplog):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    video_calls, _video_durations = _mock_non_voiceover_stages(monkeypatch)

    monkeypatch.setattr(
        voiceover,
        "generate_narrations",
        lambda voiceover_cfg, quotes, output_dir, api_key=None, backend=None: [
            _write(output_dir / "narration_1.mp3"),
            None,
        ],
    )
    monkeypatch.setattr(voiceover, "narration_duration_sec", lambda path: 2.0)
    monkeypatch.setattr(
        "horrorvibes.orchestrator.subprocess.run",
        lambda cmd, capture_output=True, text=True, check=False: SimpleNamespace(
            returncode=1, stderr="ffmpeg exploded"
        ),
    )

    config = dataclasses.replace(
        config,
        publish=dataclasses.replace(config.publish, youtube_upload=False),
        voiceover=dataclasses.replace(config.voiceover, enabled=True),
    )
    with caplog.at_level("WARNING"):
        run_pipeline(config, chat_client)

    assert video_calls[0].name == "_music.mp3"
    assert any("mix failed" in r.message.lower() for r in caplog.records)


# ---- video archiving to an external drive ---------------------------------------


def test_external_drive_available_true_for_non_volumes_path(tmp_path):
    assert _external_drive_available(tmp_path / "some" / "nested" / "path") is True


def test_external_drive_available_true_when_mount_point_exists():
    assert _external_drive_available(Path("/Volumes/My Book/out"), is_dir=lambda p: True) is True


def test_external_drive_available_false_when_mount_point_missing():
    assert _external_drive_available(Path("/Volumes/Definitely Not Plugged In/out")) is False


def test_archive_completed_video_does_nothing_when_unconfigured(config, tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    config = dataclasses.replace(config, run=dataclasses.replace(config.run, completed_video_dir=None))

    _archive_completed_video(config, video_path)  # should not raise, nothing to assert on disk


def test_archive_completed_video_copies_to_configured_dir(config, tmp_path):
    video_path = tmp_path / "output" / "video.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video-bytes")
    archive_dir = tmp_path / "archive"
    config = dataclasses.replace(
        config, run=dataclasses.replace(config.run, completed_video_dir=archive_dir)
    )

    _archive_completed_video(config, video_path)

    assert (archive_dir / "video.mp4").read_bytes() == b"video-bytes"
    assert video_path.exists()  # original stays in place too


def test_archive_completed_video_warns_without_raising_when_drive_unavailable(config, tmp_path, caplog):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    unavailable = Path("/Volumes/Definitely Not Plugged In/horrorvibes_output")
    config = dataclasses.replace(
        config, run=dataclasses.replace(config.run, completed_video_dir=unavailable)
    )

    with caplog.at_level("WARNING"):
        _archive_completed_video(config, video_path)  # should not raise

    assert any("not available" in r.message for r in caplog.records)


def test_run_pipeline_archives_video_when_configured(config, monkeypatch, tmp_path):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    _mock_non_voiceover_stages(monkeypatch)

    archive_dir = tmp_path / "archive"
    config = dataclasses.replace(
        config,
        publish=dataclasses.replace(config.publish, youtube_upload=False),
        run=dataclasses.replace(config.run, completed_video_dir=archive_dir),
    )
    result = run_pipeline(config, chat_client)

    assert (archive_dir / result.video_path.name).exists()


def test_run_pipeline_skips_upload_and_logs_when_publish_fails(config, monkeypatch, caplog):
    chat_client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])

    monkeypatch.setattr(
        imagegen,
        "generate_image",
        lambda quote, index, cfg, images_dir, backends=None: imagegen.ImageResult(
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
