# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import base64
import dataclasses

import pytest

from horrorvibes.config import load_config
from horrorvibes.voiceover import (
    ElevenLabsVoiceoverBackend,
    NarrationItem,
    build_ducked_mix_cmd,
    estimate_word_starts,
    generate_narrations,
    narration_duration_sec,
    word_starts_from_alignment,
)


@pytest.fixture
def voiceover_config(tmp_path):
    return load_config(tmp_path / "missing.yaml").voiceover


class FakeResponse:
    def __init__(self, content=b"speech-bytes", payload=None, status_ok=True):
        self.content = content
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._payload


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


def _alignment(text, step=0.1):
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(i * step, 3) for i in range(len(text))],
        "character_end_times_seconds": [round((i + 1) * step, 3) for i in range(len(text))],
    }


def test_backend_with_timestamps_decodes_audio_and_returns_alignment(voiceover_config, tmp_path):
    payload = {"audio_base64": base64.b64encode(b"narration-bytes").decode(), "alignment": _alignment("Hi")}
    session = FakeSession([FakeResponse(payload=payload)])
    backend = ElevenLabsVoiceoverBackend(voiceover_config, api_key="secret", session=session)
    output_path = tmp_path / "narration_1.mp3"

    alignment = backend("[whispers] Redrum.", output_path, "eleven_v3", "voice-x")

    assert output_path.read_bytes() == b"narration-bytes"
    assert alignment["characters"] == ["H", "i"]
    request = session.requests[0]
    assert request["url"].endswith("/voice-x/with-timestamps")
    assert request["headers"]["xi-api-key"] == "secret"
    assert request["json"]["text"] == "[whispers] Redrum."
    assert request["json"]["model_id"] == "eleven_v3"


def test_backend_without_timestamps_writes_raw_bytes(voiceover_config, tmp_path):
    config = dataclasses.replace(voiceover_config, use_timestamps=False)
    session = FakeSession([FakeResponse(content=b"raw")])
    backend = ElevenLabsVoiceoverBackend(config, api_key="k", session=session)

    assert backend("Hi", tmp_path / "n.mp3", "eleven_multilingual_v2", "v") is None
    assert (tmp_path / "n.mp3").read_bytes() == b"raw"
    assert not session.requests[0]["url"].endswith("with-timestamps")


def test_voice_settings_v3_snaps_stability_and_drops_v2_knobs(voiceover_config):
    backend = ElevenLabsVoiceoverBackend(dataclasses.replace(voiceover_config, stability=0.4), api_key="k")
    v3 = backend.voice_settings("eleven_v3")
    v2 = backend.voice_settings("eleven_multilingual_v2")
    assert v3["stability"] == 0.5 and "speed" not in v3 and "style" not in v3
    assert v2["speed"] == voiceover_config.speed


def test_word_starts_skip_audio_tags():
    starts = word_starts_from_alignment(_alignment("[whispers] Run now"))
    assert starts == [1.1, 1.5]


def test_word_starts_none_for_missing_alignment():
    assert word_starts_from_alignment(None) is None
    assert word_starts_from_alignment({"characters": ["a"], "character_start_times_seconds": []}) is None


def test_estimate_word_starts_is_monotonic_within_duration():
    starts = estimate_word_starts(["one", "two", "three"], 3.0)
    assert starts == sorted(starts) and starts[0] == 0.1 and starts[-1] < 3.0


def test_generate_narrations_tags_v3_and_returns_word_starts(tmp_path, voiceover_config):
    calls = []

    def backend(text, output_path, model_id, voice_id):
        calls.append((text, model_id, voice_id))
        output_path.write_bytes(b"audio")
        return _alignment(text)

    results = generate_narrations(
        voiceover_config,
        [NarrationItem("Redrum.", "whispers"), NarrationItem("They're here.", "calm")],
        tmp_path / "narration",
        backend=backend,
        voice_id="pack-voice",
    )

    assert calls[0] == ("[whispers] Redrum.", "eleven_v3", "pack-voice")
    assert results[0].path.name == "narration_1.mp3"
    assert results[1].word_starts == [0.7, 1.5]  # "[calm] " is 7 chars at 0.1s each


def test_generate_narrations_falls_back_to_v2_then_default_voice(tmp_path, voiceover_config):
    calls = []

    def backend(text, output_path, model_id, voice_id):
        calls.append((text, model_id, voice_id))
        if voice_id == "bad-voice":
            raise RuntimeError("voice not found")
        output_path.write_bytes(b"audio")

    results = generate_narrations(
        voiceover_config,
        [NarrationItem("A.", "cold"), NarrationItem("B.", "cold")],
        tmp_path / "narration",
        backend=backend,
        voice_id="bad-voice",
    )

    assert calls[:3] == [
        ("[cold] A.", "eleven_v3", "bad-voice"),
        ("A.", "eleven_multilingual_v2", "bad-voice"),
        ("A.", "eleven_multilingual_v2", voiceover_config.voice_id),
    ]
    # dead (model, voice) pairs aren't retried for the second quote
    assert calls[3] == ("B.", "eleven_multilingual_v2", voiceover_config.voice_id)
    assert all(r is not None and r.word_starts is None for r in results)


def test_generate_narrations_skips_failed_quotes_without_raising(tmp_path, voiceover_config, caplog):
    def backend(text, output_path, model_id, voice_id):
        if "here" in text:
            raise RuntimeError("tts down")
        output_path.write_bytes(b"audio")

    with caplog.at_level("WARNING"):
        results = generate_narrations(
            dataclasses.replace(voiceover_config, fallback_model_id=None),
            [NarrationItem("Redrum."), NarrationItem("They're here.")],
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
    # both sides are levelled first, so a whispered take can't be buried
    assert f"[0:a]loudnorm=I={voiceover_config.music_lufs}" in filter_complex
    assert f"[1:a]loudnorm=I={voiceover_config.narration_lufs}" in filter_complex
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


# ---- local (Chatterbox) engine ---------------------------------------------------------------


def _local_config(voiceover_config, tmp_path):
    python = tmp_path / "python"
    reference = tmp_path / "voice.wav"
    python.write_text("")
    reference.write_bytes(b"wav")
    return dataclasses.replace(
        voiceover_config, engine="chatterbox", local_python=python, local_reference=reference
    )


def _fake_worker(fail_indexes=()):
    """Stands in for scripts/tts_chatterbox.py: writes each clip and the results file."""
    import json  # pylint: disable=import-outside-toplevel
    from pathlib import Path  # pylint: disable=import-outside-toplevel
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        job = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        results = []
        for i, item in enumerate(job["items"]):
            if i in fail_indexes:
                results.append({"ok": False, "error": "boom"})
            else:
                Path(item["out"]).write_bytes(b"wav")
                results.append({"ok": True, "word_starts": [0.1, 0.5]})
        Path(job["results"]).write_text(json.dumps(results), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    return runner, calls


def test_local_engine_makes_every_clip_in_one_worker_run(voiceover_config, tmp_path):
    from horrorvibes import voiceover  # pylint: disable=import-outside-toplevel

    config = _local_config(voiceover_config, tmp_path)
    runner, calls = _fake_worker()
    items = [
        voiceover.NarrationItem("one two", "whispers"),
        voiceover.NarrationItem("three four", "menacing"),
    ]

    result = voiceover.generate_narrations(config, items, tmp_path / "out", runner=runner)

    assert len(calls) == 1
    assert [n.path.name for n in result] == ["narration_1.wav", "narration_2.wav"]
    assert result[0].word_starts == [0.1, 0.5]


def test_local_engine_falls_back_to_elevenlabs_for_failed_clips(voiceover_config, tmp_path):
    from horrorvibes import voiceover  # pylint: disable=import-outside-toplevel

    config = _local_config(voiceover_config, tmp_path)
    runner, _ = _fake_worker(fail_indexes={1})
    spoken = []

    def eleven(text, output_path, model_id, voice_id):  # pylint: disable=unused-argument
        spoken.append(text)
        output_path.write_bytes(b"mp3")

    items = [voiceover.NarrationItem("one two"), voiceover.NarrationItem("three four")]
    result = voiceover.generate_narrations(config, items, tmp_path / "out", backend=eleven, runner=runner)

    assert result[0].path.suffix == ".wav"
    assert "elevenlabs_fallback" in str(result[1].path)
    assert spoken == ["three four"]


def test_local_engine_without_a_voice_sample_uses_elevenlabs(voiceover_config, tmp_path):
    from horrorvibes import voiceover  # pylint: disable=import-outside-toplevel

    config = dataclasses.replace(
        _local_config(voiceover_config, tmp_path), local_reference=tmp_path / "missing.wav"
    )
    assert not voiceover.local_engine_ready(config)


def test_exaggeration_follows_delivery(voiceover_config, tmp_path):
    from horrorvibes import voiceover  # pylint: disable=import-outside-toplevel

    config = _local_config(voiceover_config, tmp_path)
    items = [voiceover.NarrationItem("a", "whispers"), voiceover.NarrationItem("b", "terrified"),
             voiceover.NarrationItem("c")]
    job = voiceover.build_local_job(config, items, tmp_path, tmp_path / "r.json")
    levels = [item["exaggeration"] for item in job["items"]]
    assert levels[0] < levels[2] < levels[1]


def test_unknown_voice_engine_is_rejected(tmp_path):
    from horrorvibes.config import ConfigError  # pylint: disable=import-outside-toplevel

    path = tmp_path / "config.yaml"
    path.write_text("voiceover:\n  engine: festival\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="voiceover.engine"):
        load_config(path)
