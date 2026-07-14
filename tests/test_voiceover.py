# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import pytest

from horrorvibes.config import load_config
from horrorvibes.voiceover import (
    ElevenLabsVoiceoverBackend,
    build_ducked_mix_cmd,
    generate_narrations,
    narration_duration_sec,
)


@pytest.fixture
def voiceover_config(tmp_path):
    return load_config(tmp_path / "missing.yaml").voiceover


class FakeResponse:
    def __init__(self, content=b"speech-bytes", status_ok=True):
        self.content = content
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_elevenlabs_backend_writes_response_bytes_and_uses_voice_id(voiceover_config, tmp_path):
    session = FakeSession([FakeResponse(content=b"narration-bytes")])
    backend = ElevenLabsVoiceoverBackend(voiceover_config, api_key="secret", session=session)
    output_path = tmp_path / "narration_1.mp3"

    backend("Redrum.", output_path)

    assert output_path.read_bytes() == b"narration-bytes"
    assert voiceover_config.voice_id in session.requests[0]["url"]
    assert session.requests[0]["headers"]["xi-api-key"] == "secret"
    assert session.requests[0]["json"]["text"] == "Redrum."


def test_generate_narrations_succeeds_for_every_quote(tmp_path, voiceover_config):
    calls = []

    def backend(text, output_path):
        calls.append(text)
        output_path.write_bytes(b"audio")

    results = generate_narrations(
        voiceover_config,
        ["'Redrum.' - The Shining", "'They're here.' - Poltergeist"],
        tmp_path / "narration",
        backend=backend,
    )

    assert calls == ["'Redrum.'", "'They're here.'"]
    assert all(r is not None for r in results)
    assert results[0].name == "narration_1.mp3"
    assert results[1].name == "narration_2.mp3"


def test_generate_narrations_skips_failed_quotes_without_raising(tmp_path, voiceover_config, caplog):
    def backend(text, output_path):
        if "Poltergeist" in text or "here" in text:
            raise RuntimeError("tts down")
        output_path.write_bytes(b"audio")

    with caplog.at_level("WARNING"):
        results = generate_narrations(
            voiceover_config,
            ["'Redrum.' - The Shining", "'They're here.' - Poltergeist"],
            tmp_path / "narration",
            backend=backend,
        )

    assert results[0] is not None
    assert results[1] is None
    assert any("Narration failed" in r.message for r in caplog.records)


def test_build_ducked_mix_cmd_single_entry_uses_explicit_start_offset(tmp_path, voiceover_config):
    narration_path = tmp_path / "narration_3.mp3"

    cmd = build_ducked_mix_cmd(
        tmp_path / "music.mp3",
        [(20.0, narration_path, 3.5)],  # explicit start offset (e.g. a stretched earlier quote), 3.5s long
        voiceover_config=voiceover_config,
        output_path=tmp_path / "out.mp3",
    )

    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=20000|20000" in filter_complex
    assert "between(t,20.0,23.5)" in filter_complex
    assert f"volume={voiceover_config.duck_volume}" in filter_complex
    assert f"volume={voiceover_config.narration_gain}" in filter_complex
    assert "sidechaincompress" not in filter_complex
    assert "-map" in cmd and cmd[cmd.index("-map") + 1] == "[final]"


def test_build_ducked_mix_cmd_multi_entry_mixes_narrations_before_ducking(tmp_path, voiceover_config):
    cmd = build_ducked_mix_cmd(
        tmp_path / "music.mp3",
        [(0.0, tmp_path / "n1.mp3", 2.0), (50.0, tmp_path / "n2.mp3", 4.0)],
        voiceover_config=voiceover_config,
        output_path=tmp_path / "out.mp3",
    )

    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=0|0" in filter_complex
    assert "adelay=50000|50000" in filter_complex
    assert "between(t,0.0,2.0)" in filter_complex
    assert "between(t,50.0,54.0)" in filter_complex
    assert "amix=inputs=2:normalize=0[narrmix]" in filter_complex
    assert "[narrmix]" in filter_complex


def test_build_ducked_mix_cmd_uses_actual_offset_not_a_fixed_slot(tmp_path, voiceover_config):
    """Regression test: a quote stretched by a prior long narration must
    use its real cumulative start time, not quote_index * a fixed slot
    duration -- that's exactly what caused narration to overlap."""
    cmd = build_ducked_mix_cmd(
        tmp_path / "music.mp3",
        [(0.0, tmp_path / "n1.mp3", 15.0), (16.0, tmp_path / "n2.mp3", 2.0)],
        voiceover_config=voiceover_config,
        output_path=tmp_path / "out.mp3",
    )

    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=16000|16000" in filter_complex
    assert "between(t,16.0,18.0)" in filter_complex


def test_build_ducked_mix_cmd_raises_on_empty_entries(tmp_path, voiceover_config):
    with pytest.raises(ValueError):
        build_ducked_mix_cmd(
            tmp_path / "music.mp3", [], voiceover_config=voiceover_config, output_path=tmp_path / "out.mp3"
        )


def test_narration_duration_sec_reads_real_audio_length(tmp_path):
    import soundfile as sf
    import numpy as np

    path = tmp_path / "narration.wav"
    sf.write(path, np.zeros(44100 * 2), 44100)  # 2 seconds of silence

    assert narration_duration_sec(path) == pytest.approx(2.0, abs=0.01)
