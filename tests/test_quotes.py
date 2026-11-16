# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import dataclasses
import json
import random
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from horrorvibes.config import QuotesConfig
from horrorvibes.exceptions import QuoteGenerationError
from horrorvibes.quotes import (
    QuoteCandidate,
    append_movie_history,
    append_quote_history,
    build_quote_request,
    build_slot_briefs,
    build_verify_request,
    generate_quotes,
    has_strong_profanity,
    is_near_duplicate,
    load_recent_movies,
    load_used_quotes,
    normalize_movie,
    normalize_quote,
    quote_key,
    verify_candidates,
    write_quote_files,
)


def _item(quote, title, year=1980, confidence="high", medium="film", slot=1):
    return {
        "slot": slot, "quote": quote, "title": title, "year": year,
        "medium": medium, "speaker": "Someone", "confidence": confidence,
    }


def _quotes_payload(*items):
    return {"quotes": list(items)}


class FakeResponsesClient:
    """Duck-typed stand-in for openai.OpenAI() -- no network involved.

    Each queued item is a dict (returned as JSON output_text) or an
    Exception (raised)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(output_text=json.dumps(item))


@pytest.fixture
def quotes_config(tmp_path):
    return QuotesConfig(
        model="gpt-test",
        max_attempts=3,
        temperature=None,
        themes=["slasher", "folk horror", "giallo"],
        movie_history_path=tmp_path / "movie_history.jsonl",
        movie_cooldown_days=14,
        verify=False,
        regions={"American": 1.0, "Japanese": 1.0},
    )


# ---- normalization -------------------------------------------------------------


def test_quote_key_ignores_case_punctuation_and_stretched_letters():
    assert quote_key("Heeeere's Johnny!") == quote_key("here's johnny")
    assert quote_key("“They're here.”") == quote_key("They’re here")


def test_normalize_quote_ignores_title_and_year_formatting():
    assert normalize_quote('"Redrum." - The Shining (1980)') == normalize_quote("'Redrum.' - The Shining")


def test_normalize_movie_strips_year_article_and_punctuation():
    assert normalize_movie("The Shining (1980)") == "shining"
    assert normalize_movie("  THE SHINING ") == "shining"
    assert normalize_movie("Shining, The") == "shining"
    assert normalize_movie("A Nightmare on Elm Street") == "nightmare on elm street"


def test_normalize_movie_is_idempotent_on_legacy_keys():
    assert normalize_movie("the shining (1980)") == normalize_movie(normalize_movie("the shining (1980)"))


def test_is_near_duplicate_exact_fuzzy_and_distinct():
    used = {quote_key("We all go a little mad sometimes.")}
    assert is_near_duplicate(quote_key("We all go a little mad sometimes"), used, 0.88)
    assert is_near_duplicate(quote_key("We all go a little bit mad sometimes."), used, 0.88)
    assert not is_near_duplicate(quote_key("They're coming to get you, Barbara."), used, 0.88)


def test_load_used_quotes_from_history_file(tmp_path):
    history = tmp_path / "quotes_history.txt"
    history.write_text("'Redrum.' - The Shining\n\n'They're here.' - Poltergeist\n", encoding="utf-8")

    used = load_used_quotes(history)

    assert quote_key("Redrum") in used
    assert len(used) == 2


def test_load_used_quotes_missing_file_returns_empty_set(tmp_path):
    assert load_used_quotes(tmp_path / "missing.txt") == set()


def test_candidate_to_line_strips_quote_marks_and_protects_separator():
    candidate = QuoteCandidate(
        quote='"Run - now."', title="Some Film", year=1999, medium="film", speaker="", confidence="high"
    )
    assert candidate.to_line() == '"Run — now." - Some Film (1999)'


# ---- slot briefs & requests ------------------------------------------------------


def test_slot_briefs_spread_subgenres_without_repeats(quotes_config):
    briefs = build_slot_briefs(quotes_config, 3, random.Random(1))
    assert sorted(b.subgenre for b in briefs) == sorted(quotes_config.themes)


def test_slot_briefs_never_put_tv_before_the_1950s(quotes_config):
    config = dataclasses.replace(quotes_config, tv_share=1.0)
    briefs = build_slot_briefs(config, 30, random.Random(2))
    assert all(b.medium == "tv" and b.decade >= 1950 for b in briefs)


def test_slot_briefs_avoid_repeating_decade_subgenre_pairs(quotes_config):
    briefs = build_slot_briefs(quotes_config, 9, random.Random(3))
    pairs = [(b.decade, b.subgenre) for b in briefs]
    assert len(pairs) == len(set(pairs))


def test_build_quote_request_lists_slots_and_exclusions(quotes_config):
    briefs = build_slot_briefs(quotes_config, 2, random.Random(0))
    request = build_quote_request(quotes_config, briefs, ["shining"], ["Redrum."])
    kwargs = request.to_kwargs()

    assert kwargs["model"] == "gpt-test"
    assert "exactly 2 quotes" in kwargs["input"]
    assert briefs[0].describe() in kwargs["input"]
    assert "shining" in kwargs["input"] and "Redrum." in kwargs["input"]
    assert kwargs["text"]["format"]["type"] == "json_schema"
    assert kwargs["text"]["format"]["strict"] is True
    assert "temperature" not in kwargs  # reasoning models reject it
    assert kwargs["reasoning"] == {"effort": "low"}


def test_build_quote_request_sends_temperature_only_when_set(quotes_config):
    config = dataclasses.replace(quotes_config, temperature=0.9, reasoning_effort=None)
    kwargs = build_quote_request(config, build_slot_briefs(config, 1, random.Random(0)), [], []).to_kwargs()
    assert kwargs["temperature"] == 0.9
    assert "reasoning" not in kwargs


def test_build_verify_request_uses_web_search(quotes_config):
    candidate = QuoteCandidate("Redrum.", "The Shining", 1980, "film", "Danny", "high")
    kwargs = build_verify_request(quotes_config, [candidate]).to_kwargs()
    assert kwargs["tools"] == [{"type": "web_search"}]
    assert "Redrum." in kwargs["input"]


# ---- generate_quotes -------------------------------------------------------------


def test_generate_quotes_returns_requested_count_on_first_attempt(tmp_path, quotes_config):
    client = FakeResponsesClient([_quotes_payload(_item("Q1", "Movie One"), _item("Q2", "Movie Two"))])

    result = generate_quotes(client, quotes_config, 2, tmp_path / "h.txt", rng=random.Random(0))

    assert result == ['"Q1" - Movie One (1980)', '"Q2" - Movie Two (1980)']
    assert len(client.calls) == 1


def test_generate_quotes_includes_recent_quotes_and_titles_as_exclusions(tmp_path, quotes_config):
    history = tmp_path / "h.txt"
    history.write_text('"Old line." - Old Film (1970)\n', encoding="utf-8")
    client = FakeResponsesClient([_quotes_payload(_item("New line", "New Film"))])

    generate_quotes(client, quotes_config, 1, history, rng=random.Random(0), recent_movies={"avoid me"})

    prompt = client.calls[0]["input"]
    assert '"Old line."' in prompt
    assert "avoid me" in prompt


def test_generate_quotes_skips_near_duplicates_of_history(tmp_path, quotes_config):
    history = tmp_path / "h.txt"
    history.write_text("'Heeere's Johnny!' - The Shining (1980)\n", encoding="utf-8")
    client = FakeResponsesClient(
        [_quotes_payload(_item("Here's Johnny", "Some Other Film"), _item("Fresh", "B"))]
    )

    result = generate_quotes(client, quotes_config, 2, history, rng=random.Random(0))

    assert result == ['"Fresh" - B (1980)']


def test_generate_quotes_skips_recent_title_despite_year_formatting(tmp_path, quotes_config):
    client = FakeResponsesClient(
        [_quotes_payload(_item("A", "The Shining", 1980), _item("B", "Alien", 1979))]
    )

    result = generate_quotes(
        client, quotes_config, 2, tmp_path / "h.txt",
        rng=random.Random(0), recent_movies={"the shining (1980)"},
    )

    assert result == ['"B" - Alien (1979)']


def test_generate_quotes_avoids_same_title_twice_in_batch(tmp_path, quotes_config):
    client = FakeResponsesClient([_quotes_payload(_item("A", "Halloween"), _item("B", "Halloween (1978)"))])

    result = generate_quotes(client, quotes_config, 2, tmp_path / "h.txt", rng=random.Random(0))

    assert result == ['"A" - Halloween (1980)']


def test_generate_quotes_drops_low_confidence_and_medium_without_verify(tmp_path, quotes_config):
    client = FakeResponsesClient(
        [_quotes_payload(
            _item("A", "F1", confidence="low"), _item("B", "F2", confidence="medium"), _item("C", "F3")
        )]
    )

    result = generate_quotes(client, quotes_config, 3, tmp_path / "h.txt", rng=random.Random(0))

    assert result == ['"C" - F3 (1980)']


def test_generate_quotes_retries_after_a_failed_attempt(tmp_path, quotes_config):
    client = FakeResponsesClient([RuntimeError("boom"), _quotes_payload(_item("A", "F1"))])

    result = generate_quotes(client, quotes_config, 1, tmp_path / "h.txt", rng=random.Random(0))

    assert result == ['"A" - F1 (1980)']


def test_generate_quotes_survives_malformed_json(tmp_path, quotes_config):
    client = FakeResponsesClient([{"nope": 1}, _quotes_payload({"quote": "x"}, _item("A", "F1"))])

    result = generate_quotes(client, quotes_config, 1, tmp_path / "h.txt", rng=random.Random(0))

    assert result == ['"A" - F1 (1980)']


def test_generate_quotes_raises_when_every_attempt_fails(tmp_path, quotes_config):
    client = FakeResponsesClient([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])

    with pytest.raises(QuoteGenerationError):
        generate_quotes(client, quotes_config, 2, tmp_path / "h.txt", rng=random.Random(0))


def test_generate_quotes_logs_warning_on_partial_result(tmp_path, quotes_config, caplog):
    client = FakeResponsesClient([_quotes_payload(_item("A", "F1"))] + [_quotes_payload()] * 2)

    result = generate_quotes(client, quotes_config, 3, tmp_path / "h.txt", rng=random.Random(0))

    assert len(result) == 1
    assert "Only got 1 unique quotes out of 3" in caplog.text


# ---- verification ------------------------------------------------------------------


def test_verify_drops_inaccurate_and_missing_and_corrects_year(quotes_config):
    candidates = [
        QuoteCandidate("A", "F1", 1980, "film", "", "high"),
        QuoteCandidate("B", "F2", 1981, "film", "", "high"),
        QuoteCandidate("C", "F3", 1982, "film", "", "high"),
    ]
    client = FakeResponsesClient([{
        "verdicts": [
            {"index": 1, "accurate": True, "correct_year": 1979, "note": "year off"},
            {"index": 2, "accurate": False, "correct_year": None, "note": "not found"},
        ]
    }])

    kept = verify_candidates(client, quotes_config, candidates)

    assert [(c.quote, c.year) for c in kept] == [("A", 1979)]


def test_verify_keeps_candidates_when_check_errors(quotes_config):
    candidates = [QuoteCandidate("A", "F1", 1980, "film", "", "high")]
    client = FakeResponsesClient([RuntimeError("search down")])

    assert verify_candidates(client, quotes_config, candidates) == candidates


def test_generate_quotes_with_verify_accepts_medium_and_filters(tmp_path, quotes_config):
    config = dataclasses.replace(quotes_config, verify=True)
    client = FakeResponsesClient([
        _quotes_payload(_item("A", "F1", confidence="medium"), _item("B", "F2")),
        {"verdicts": [
            {"index": 1, "accurate": True, "correct_year": None, "note": ""},
            {"index": 2, "accurate": False, "correct_year": None, "note": "misattributed"},
        ]},
        _quotes_payload(),
        _quotes_payload(),
    ])

    result = generate_quotes(client, config, 2, tmp_path / "h.txt", rng=random.Random(0))

    assert result == ['"A" - F1 (1980)']
    assert client.calls[1]["tools"] == [{"type": "web_search"}]


# ---- files & history -----------------------------------------------------------------


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

    assert history_path.read_text(encoding="utf-8") == "'A' - Movie1\n'B' - Movie2\n"
    assert list(history_path.parent.glob(".quotes_history_*.tmp")) == []


def test_append_quote_history_noop_for_empty_list(tmp_path):
    history_path = tmp_path / "quotes_history.txt"
    append_quote_history(history_path, [])
    assert not history_path.exists()


def test_load_recent_movies_missing_file_returns_empty_set(tmp_path):
    assert load_recent_movies(tmp_path / "missing.jsonl", datetime(2026, 1, 1), 14) == set()


def test_load_recent_movies_filters_by_cooldown_window(tmp_path):
    history_path = tmp_path / "movie_history.jsonl"
    now = datetime(2026, 1, 20)
    append_movie_history(history_path, ["The Shining (1980)"], now - timedelta(days=5))
    append_movie_history(history_path, ["Poltergeist"], now - timedelta(days=20))

    assert load_recent_movies(history_path, now, cooldown_days=14) == {"shining"}


def test_load_recent_movies_renormalizes_legacy_entries(tmp_path):
    history_path = tmp_path / "movie_history.jsonl"
    history_path.write_text(
        json.dumps({"movie": "the shining (1980)", "timestamp": "2026-01-19T00:00:00"}) + "\n",
        encoding="utf-8",
    )
    assert load_recent_movies(history_path, datetime(2026, 1, 20), 14) == {"shining"}


def test_append_movie_history_is_additive_and_atomic(tmp_path):
    history_path = tmp_path / "sub" / "movie_history.jsonl"
    append_movie_history(history_path, ["The Shining"], datetime(2026, 1, 1))
    append_movie_history(history_path, ["Poltergeist"], datetime(2026, 1, 2))

    assert len(history_path.read_text(encoding="utf-8").splitlines()) == 2
    assert list(history_path.parent.glob(".movie_history_*.tmp")) == []


def test_append_movie_history_skips_unknown_and_empty_list(tmp_path):
    history_path = tmp_path / "movie_history.jsonl"
    append_movie_history(history_path, [], datetime(2026, 1, 1))
    assert not history_path.exists()

    append_movie_history(history_path, ["Unknown"], datetime(2026, 1, 1))
    assert not history_path.exists()


def test_strong_profanity_is_detected_but_mild_words_pass():
    assert has_strong_profanity("I'm an English teacher, not fucking Tomb Raider.")
    assert has_strong_profanity("Welcome to prime time, bitch!")
    assert not has_strong_profanity("Damn you all to hell.")
    assert has_strong_profanity("You MOTHERFUCKER") and has_strong_profanity("bitches")
    assert not has_strong_profanity("Cockroaches in the shiitake, Scunthorpe, a fagot of sticks")


def test_generate_quotes_drops_profane_lines(tmp_path, quotes_config):
    client = FakeResponsesClient(
        [_quotes_payload(_item("Not fucking Tomb Raider", "The Descent"), _item("Clean", "Alien"))]
    )
    result = generate_quotes(client, quotes_config, 2, tmp_path / "h.txt", rng=random.Random(0))
    assert result == ['"Clean" - Alien (1980)']


def test_prompts_require_horror_genre_and_no_profanity(quotes_config):
    briefs = build_slot_briefs(quotes_config, 1, random.Random(0))
    instructions = build_quote_request(quotes_config, briefs, [], []).to_kwargs()["instructions"]
    assert "genuinely horror" in instructions and "profanity" in instructions
    candidate = QuoteCandidate("Boo", "Film", 1999, "film", "", "high")
    verify = build_verify_request(quotes_config, [candidate]).to_kwargs()["instructions"]
    assert "not generally classed as horror" in verify
