# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import dataclasses
import random

import pytest

from horrorvibes.config import load_config
from horrorvibes.exceptions import MusicGenerationError
from horrorvibes.musicgen import (
    CuratedFileBackend,
    ElevenLabsMusicBackend,
    build_music_prompt,
    compute_duration_ms,
    generate_music,
    with_retries,
)


@pytest.fixture
def config(tmp_path):
    return load_config(tmp_path / "missing.yaml")


def with_backend(config, backend):
    return dataclasses.replace(config, music=dataclasses.replace(config.music, backend=backend))


@pytest.mark.parametrize(
    "quote_count,duration_per_quote,expected_ms",
    [
        (12, 10, 120_000),
        (1, 1, 3_000),  # clamped up to the API's 3s minimum
        (100, 60, 600_000),  # clamped down to the API's 10min maximum
    ],
)
def test_compute_duration_ms_clamps_to_api_bounds(quote_count, duration_per_quote, expected_ms):
    assert compute_duration_ms(quote_count, duration_per_quote) == expected_ms


def test_build_music_prompt_includes_anchor_and_sampled_descriptors():
    prompt = build_music_prompt(
        "meditative horror ambient", ["slow", "sparse", "drone"], 2, rng=random.Random(1)
    )
    assert prompt.startswith("meditative horror ambient, ")
    descriptors = prompt.split(", ", 1)[1].split(", ")
    assert len(descriptors) == 2
    assert all(d in ["slow", "sparse", "drone"] for d in descriptors)


def test_build_music_prompt_clamps_sample_size_to_pool_length():
    prompt = build_music_prompt("anchor", ["only-one"], sample_size=5, rng=random.Random(1))
    assert prompt == "anchor, only-one"


def test_with_retries_succeeds_on_first_try_without_sleeping():
    sleeps = []
    result = with_retries(lambda: 42, max_retries=3, backoff_sec=1, sleep=sleeps.append)
    assert result == 42
    assert sleeps == []


def test_with_retries_recovers_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    sleeps = []
    result = with_retries(flaky, max_retries=5, backoff_sec=2, sleep=sleeps.append)

    assert result == "ok"
    assert sleeps == [2, 4]  # exponential backoff: backoff_sec * 2**(attempt-1)


def test_with_retries_raises_last_exception_after_exhausting_attempts():
    def always_fails():
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        with_retries(always_fails, max_retries=2, backoff_sec=0, sleep=lambda _s: None)


class FakeResponse:
    def __init__(self, content=b"audio-bytes", status_ok=True):
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


def test_elevenlabs_backend_writes_response_bytes_and_sends_api_key(config, tmp_path):
    session = FakeSession([FakeResponse(content=b"song-bytes")])
    backend = ElevenLabsMusicBackend(config.music, api_key="secret-key", session=session)
    output_path = tmp_path / "music.mp3"

    backend("a prompt", 12_000, output_path)

    assert output_path.read_bytes() == b"song-bytes"
    assert session.requests[0]["headers"]["xi-api-key"] == "secret-key"
    assert session.requests[0]["json"]["music_length_ms"] == 12_000


def test_elevenlabs_backend_retries_then_succeeds(config, tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    session = FakeSession([RuntimeError("network blip"), FakeResponse(content=b"song-bytes")])
    backend = ElevenLabsMusicBackend(config.music, api_key="k", session=session)
    output_path = tmp_path / "music.mp3"

    backend("a prompt", 12_000, output_path)

    assert output_path.read_bytes() == b"song-bytes"
    assert len(session.requests) == 2


def test_curated_file_backend_picks_one_of_the_candidates(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "track1.mp3").write_bytes(b"one")
    (audio_dir / "track2.mp3").write_bytes(b"two")

    backend = CuratedFileBackend(audio_dir, rng=random.Random(1))
    output_path = tmp_path / "out.mp3"
    backend("prompt", 1000, output_path)

    assert output_path.read_bytes() in (b"one", b"two")


def test_curated_file_backend_raises_when_directory_empty(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    backend = CuratedFileBackend(audio_dir)

    with pytest.raises(MusicGenerationError):
        backend("prompt", 1000, tmp_path / "out.mp3")


def test_curated_file_backend_raises_when_directory_missing(tmp_path):
    backend = CuratedFileBackend(tmp_path / "does_not_exist")

    with pytest.raises(MusicGenerationError):
        backend("prompt", 1000, tmp_path / "out.mp3")


def test_generate_music_uses_elevenlabs_when_it_succeeds(config, tmp_path):
    calls = []

    def elevenlabs(prompt, duration_ms, output_path):
        calls.append("elevenlabs")
        output_path.write_bytes(b"generated")

    def curated(prompt, duration_ms, output_path):
        calls.append("curated_file")

    result = generate_music(
        with_backend(config, "elevenlabs"),
        tmp_path / "music.mp3",
        backends={"elevenlabs": elevenlabs, "curated_file": curated},
    )

    assert calls == ["elevenlabs"]
    assert result.backend_used == "elevenlabs"


def test_generate_music_falls_back_to_curated_file_and_logs_warning(config, tmp_path, caplog):
    def elevenlabs(prompt, duration_ms, output_path):
        raise RuntimeError("api down")

    def curated(prompt, duration_ms, output_path):
        output_path.write_bytes(b"fallback-track")

    with caplog.at_level("WARNING"):
        result = generate_music(
            with_backend(config, "elevenlabs"),
            tmp_path / "music.mp3",
            backends={"elevenlabs": elevenlabs, "curated_file": curated},
        )

    assert result.backend_used == "curated_file"
    assert any("fallback" in record.message.lower() for record in caplog.records)


def test_generate_music_raises_when_entire_chain_fails(config, tmp_path):
    def failing(prompt, duration_ms, output_path):
        raise RuntimeError("nope")

    with pytest.raises(MusicGenerationError):
        generate_music(
            with_backend(config, "curated_file"),
            tmp_path / "music.mp3",
            backends={"curated_file": failing},
        )
