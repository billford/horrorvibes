# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import random
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from horrorvibes.config import QuotesConfig
from horrorvibes.exceptions import QuoteGenerationError
from horrorvibes.quotes import (
    append_movie_history,
    append_quote_history,
    build_quote_request,
    generate_quotes,
    load_recent_movies,
    load_used_quotes,
    normalize_movie,
    normalize_quote,
    write_quote_files,
)


def _response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeChatClient:
    """Duck-typed stand-in for openai.OpenAI() -- no network involved."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _response(item)


@pytest.fixture
def quotes_config(tmp_path):
    return QuotesConfig(
        model="gpt-4",
        max_attempts=3,
        temperature=0.7,
        themes=["classic horror"],
        movie_history_path=tmp_path / "movie_history.jsonl",
        movie_cooldown_days=14,
    )


def test_normalize_quote_strips_case_and_quote_chars():
    assert normalize_quote(" \"Redrum.\" - The Shining ") == "redrum. - the shining"
    assert normalize_quote("'Redrum.' - The Shining") == normalize_quote('"Redrum." - The Shining')


def test_load_used_quotes_from_history_file(tmp_path):
    history = tmp_path / "quotes_history.txt"
    history.write_text("'Redrum.' - The Shining\n\n'They're here.' - Poltergeist\n", encoding="utf-8")

    used = load_used_quotes(history)

    assert normalize_quote("'Redrum.' - The Shining") in used
    assert len(used) == 2


def test_load_used_quotes_missing_file_returns_empty_set(tmp_path):
    assert load_used_quotes(tmp_path / "missing.txt") == set()


def test_build_quote_request_includes_theme_seed_and_count(quotes_config):
    request = build_quote_request(quotes_config, remaining=5, theme="ghost stories", seed=42, timestamp="ts")

    assert request.model == "gpt-4"
    assert "5 different" in request.user_prompt
    assert "ghost stories" in request.user_prompt
    assert "42" in request.user_prompt
    assert "ts" in request.user_prompt


def test_generate_quotes_returns_requested_count_on_first_attempt(tmp_path, quotes_config):
    client = FakeChatClient(["'A' - Movie1\n'B' - Movie2\n'C' - Movie3"])
    history_path = tmp_path / "quotes_history.txt"

    result = generate_quotes(client, quotes_config, count=3, history_path=history_path, rng=random.Random(1))

    assert result == ["'A' - Movie1", "'B' - Movie2", "'C' - Movie3"]
    assert len(client.calls) == 1


def test_generate_quotes_skips_quotes_already_in_history(tmp_path, quotes_config):
    history_path = tmp_path / "quotes_history.txt"
    history_path.write_text("'A' - Movie1\n", encoding="utf-8")
    client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])

    result = generate_quotes(client, quotes_config, count=1, history_path=history_path, rng=random.Random(1))

    assert result == ["'B' - Movie2"]


def test_generate_quotes_retries_after_a_failed_attempt(tmp_path, quotes_config):
    client = FakeChatClient([RuntimeError("boom"), "'A' - Movie1"])
    history_path = tmp_path / "quotes_history.txt"

    result = generate_quotes(client, quotes_config, count=1, history_path=history_path, rng=random.Random(1))

    assert result == ["'A' - Movie1"]
    assert len(client.calls) == 2


def test_generate_quotes_raises_when_every_attempt_fails(tmp_path, quotes_config):
    client = FakeChatClient([RuntimeError("boom")] * quotes_config.max_attempts)
    history_path = tmp_path / "quotes_history.txt"

    with pytest.raises(QuoteGenerationError):
        generate_quotes(client, quotes_config, count=1, history_path=history_path, rng=random.Random(1))


def test_generate_quotes_logs_warning_on_partial_result(tmp_path, quotes_config, caplog):
    client = FakeChatClient(["'A' - Movie1", "'A' - Movie1", "'A' - Movie1"])
    history_path = tmp_path / "quotes_history.txt"

    with caplog.at_level("WARNING"):
        result = generate_quotes(
            client, quotes_config, count=3, history_path=history_path, rng=random.Random(1)
        )

    assert result == ["'A' - Movie1"]
    assert any("Only got" in record.message for record in caplog.records)


def test_write_quote_files_cleans_stale_files_first(tmp_path):
    quotes_dir = tmp_path / "quotes"
    quotes_dir.mkdir()
    (quotes_dir / "quote_1.txt").write_text("stale", encoding="utf-8")

    paths = write_quote_files(quotes_dir, ["'A' - Movie1", "'B' - Movie2"])

    assert [p.name for p in paths] == ["quote_1.txt", "quote_2.txt"]
    assert (quotes_dir / "quote_1.txt").read_text(encoding="utf-8") == "'A' - Movie1"


def test_append_quote_history_is_additive_and_atomic(tmp_path):
    history_path = tmp_path / "sub" / "quotes_history.txt"
    append_quote_history(history_path, ["'A' - Movie1"])
    append_quote_history(history_path, ["'B' - Movie2"])

    content = history_path.read_text(encoding="utf-8")
    assert content == "'A' - Movie1\n'B' - Movie2\n"
    # no leftover temp files
    assert list(history_path.parent.glob(".quotes_history_*.tmp")) == []


def test_append_quote_history_noop_for_empty_list(tmp_path):
    history_path = tmp_path / "quotes_history.txt"
    append_quote_history(history_path, [])
    assert not history_path.exists()


# ---- movie recency tracking --------------------------------------------------


def test_normalize_movie_strips_case_and_whitespace():
    assert normalize_movie("  The Shining  ") == "the shining"
    assert normalize_movie("THE SHINING") == normalize_movie("the shining")


def test_load_recent_movies_missing_file_returns_empty_set(tmp_path):
    assert load_recent_movies(tmp_path / "missing.jsonl", datetime(2026, 1, 1), 14) == set()


def test_load_recent_movies_filters_by_cooldown_window(tmp_path):
    history_path = tmp_path / "movie_history.jsonl"
    now = datetime(2026, 1, 20)
    append_movie_history(history_path, ["The Shining"], now - timedelta(days=5))
    append_movie_history(history_path, ["Poltergeist"], now - timedelta(days=20))

    recent = load_recent_movies(history_path, now, cooldown_days=14)

    assert recent == {"the shining"}


def test_append_movie_history_is_additive_and_atomic(tmp_path):
    history_path = tmp_path / "sub" / "movie_history.jsonl"
    append_movie_history(history_path, ["The Shining"], datetime(2026, 1, 1))
    append_movie_history(history_path, ["Poltergeist"], datetime(2026, 1, 2))

    lines = history_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert list(history_path.parent.glob(".movie_history_*.tmp")) == []


def test_append_movie_history_skips_unknown_and_empty_list(tmp_path):
    history_path = tmp_path / "movie_history.jsonl"
    append_movie_history(history_path, [], datetime(2026, 1, 1))
    assert not history_path.exists()

    append_movie_history(history_path, ["Unknown"], datetime(2026, 1, 1))
    assert not history_path.exists()


def test_generate_quotes_avoids_recent_movie_within_batch(tmp_path, quotes_config):
    client = FakeChatClient(
        [
            "'A' - Movie1\n'B' - Movie1\n'C' - Movie2",
        ]
    )
    history_path = tmp_path / "quotes_history.txt"

    result = generate_quotes(client, quotes_config, count=2, history_path=history_path, rng=random.Random(1))

    assert result == ["'A' - Movie1", "'C' - Movie2"]


def test_generate_quotes_avoids_movie_from_recent_movies_param(tmp_path, quotes_config):
    client = FakeChatClient(["'A' - Movie1\n'B' - Movie2"])
    history_path = tmp_path / "quotes_history.txt"

    result = generate_quotes(
        client,
        quotes_config,
        count=1,
        history_path=history_path,
        rng=random.Random(1),
        recent_movies={"movie1"},
    )

    assert result == ["'B' - Movie2"]
