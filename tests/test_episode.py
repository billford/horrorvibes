# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import dataclasses
import random
from datetime import datetime

import pytest

from horrorvibes.config import load_config
from horrorvibes.episode import (
    MIXED,
    apply_format,
    choose_least_recent,
    load_history,
    plan_episode,
    record_episode,
)


@pytest.fixture
def config(tmp_path):
    base = load_config(tmp_path / "missing.yaml")
    return dataclasses.replace(
        base, episodes=dataclasses.replace(base.episodes, history_path=tmp_path / "episode_history.jsonl")
    )


def test_choose_least_recent_prefers_never_used_then_oldest():
    history = [{"pack": "a"}, {"pack": "b"}, {"pack": "a"}]
    assert choose_least_recent(["a", "b", "c"], history, "pack", random.Random(0)) == "c"
    assert choose_least_recent(["a", "b"], history, "pack", random.Random(0)) == "b"


def test_packs_rotate_through_every_pack_before_repeating(config):
    rng = random.Random(4)
    seen = []
    for day in range(len(config.style_packs)):
        plan = plan_episode(config, rng)
        record_episode(config.episodes.history_path, plan, datetime(2026, 1, day + 1))
        seen.append(plan.pack.name)
    assert sorted(seen) == sorted(pack.name for pack in config.style_packs)


def test_quote_count_within_range_and_override_wins(config):
    rng = random.Random(1)
    counts = {plan_episode(config, rng).quote_count for _ in range(30)}
    assert min(counts) >= config.episodes.quote_count_min and max(counts) <= config.episodes.quote_count_max
    assert plan_episode(config, rng, quote_count_override=3).quote_count == 3


def test_themed_episode_narrows_briefs_and_names_video(config):
    config = dataclasses.replace(config, episodes=dataclasses.replace(config.episodes, themed_share=1.0))
    plan = plan_episode(config, random.Random(0))
    fmt = next(f for f in config.episodes.formats if f.name == plan.format_name)
    assert plan.format_name != MIXED
    assert plan.headline(7) == fmt.headline.format(n=7)
    if fmt.themes:
        assert plan.quotes_config.themes == fmt.themes


def test_mixed_episode_uses_a_mixed_headline(config):
    config = dataclasses.replace(config, episodes=dataclasses.replace(config.episodes, themed_share=0.0))
    plan = plan_episode(config, random.Random(0))
    assert plan.format_name == MIXED
    assert plan.headline_template in config.episodes.mixed_headlines
    assert plan.quotes_config is config.quotes


def test_disabled_episodes_use_run_quote_count(config):
    config = dataclasses.replace(config, episodes=dataclasses.replace(config.episodes, enabled=False))
    plan = plan_episode(config, random.Random(0))
    assert plan.quote_count == config.run.quote_count and plan.format_name == MIXED


def test_apply_format_overrides_only_given_fields(config):
    fmt = next(f for f in config.episodes.formats if f.name == "eighties")
    narrowed = apply_format(config.quotes, fmt)
    assert narrowed.decades == [1980]
    assert narrowed.themes == config.quotes.themes


def test_load_history_skips_bad_lines(tmp_path):
    path = tmp_path / "h.jsonl"
    path.write_text('{"pack": "a"}\nnot json\n\n{"pack": "b"}\n', encoding="utf-8")
    assert [r["pack"] for r in load_history(path)] == ["a", "b"]
