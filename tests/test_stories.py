# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name
import json
import random
from datetime import datetime
from types import SimpleNamespace

import pytest

from horrorvibes import stories
from horrorvibes.config import load_config


class FakeClient:
    """Returns queued payloads in order and records each request."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self._payloads.pop(0)))


@pytest.fixture
def cfg(tmp_path):
    base = load_config(tmp_path / "missing.yaml").stories
    return stories.StoriesConfig(**{
        **base.__dict__,
        "history_path": tmp_path / "story_history.jsonl",
        "audit_dir": tmp_path / "stories",
        "channel_snapshot_path": tmp_path / "none.json",
        "words_min": 10, "words_max": 20, "beats_min": 2, "beats_max": 3,
    })


def _topics(*names):
    return {
        "topics": [{"name": n, "place": "Somewhere", "era": "1920s", "pitch": f"{n} pitch"} for n in names]
    }


def _research(urls=("https://a.org/1", "https://www.b.com/2")):
    return {
        "found": True, "legend": "L", "record": "R",
        "facts": [{"fact": "F", "source_url": urls[0]}],
        "names": [{"name": "Octavius", "respelling": "ock-TAY-vee-us", "ipa": ""}],
        "sources": [{"title": f"S{i}", "url": u} for i, u in enumerate(urls)],
    }


def _script(words=12, title="The Octavius: Ghost Ship of the Arctic"):
    half = " ".join(["word"] * (words // 2))
    beat = {"narration": half, "visual_scene": "ice", "delivery": "calm"}
    return {"title": title, "beats": [beat, beat]}


def _claims(*statuses):
    return {"claims": [{"claim": f"c{i}", "status": s, "correction": "", "source_url": ""}
                       for i, s in enumerate(statuses)]}


def test_happy_path_returns_checked_story(cfg):
    client = FakeClient([_topics("The Octavius"), _research(), _script(), _claims("supported", "supported")])
    story = stories.generate_story(client, cfg, rng=random.Random(1), used=[])
    assert story.topic.name == "The Octavius"
    assert story.script.word_count == 12
    assert len(story.claims) == 2
    assert "Sources:" in story.description() and "https://a.org/1" in story.description()
    # research and claim checks search the web; topic picking and writing don't
    assert ["tools" in call for call in client.calls] == [False, True, False, True]


def test_failed_claim_triggers_one_rewrite_with_corrections(cfg):
    client = FakeClient([
        _topics("The Octavius"), _research(), _script(), _claims("supported", "wrong"),
        _script(), _claims("supported"),
    ])
    story = stories.generate_story(client, cfg, rng=random.Random(1), used=[])
    assert story.claims == _claims("supported")["claims"]
    assert "Fix or remove" in client.calls[4]["input"]


def test_topic_dropped_after_second_failed_check(cfg):
    cfg = stories.StoriesConfig(**{**cfg.__dict__, "max_topic_attempts": 1})
    client = FakeClient([
        _topics("The Octavius"), _research(), _script(), _claims("wrong"), _script(), _claims("unsupported"),
    ])
    with pytest.raises(stories.StoryGenerationError):
        stories.generate_story(client, cfg, rng=random.Random(1), used=[])


def test_single_source_site_is_not_enough(cfg):
    cfg = stories.StoriesConfig(**{**cfg.__dict__, "max_topic_attempts": 1})
    client = FakeClient([_topics("The Octavius"), _research(urls=("https://a.org/1", "https://www.a.org/2"))])
    with pytest.raises(stories.StoryGenerationError):
        stories.generate_story(client, cfg, rng=random.Random(1), used=[])


def test_repeats_of_past_uploads_are_skipped(cfg):
    cfg = stories.StoriesConfig(**{**cfg.__dict__, "max_topic_attempts": 1})
    client = FakeClient(
        [_topics("SS Baychimo", "The Octavius"), _research(), _script(), _claims("supported")]
    )
    story = stories.generate_story(
        client, cfg, rng=random.Random(1), used=["The Ship That Refused to Sink  SS Baychimo"]
    )
    assert story.topic.name == "The Octavius"


def test_script_shape_problems():
    cfg = SimpleNamespace(words_min=10, words_max=20, beats_min=2, beats_max=3, beat_words_max=32)
    ok = stories.parse_script(_script())
    assert not stories.script_problems(cfg, ok)
    titled = stories.parse_script(_script(title="Real?"))
    assert "question or hashtag in title" in stories.script_problems(cfg, titled)
    assert "40 words" in stories.script_problems(cfg, stories.parse_script(_script(words=40)))


@pytest.mark.parametrize("text", ["Vaccines and the cover-up", "The 2020 election", "Chemtrails over Ohio"])
def test_off_limits_subjects(text):
    assert stories.is_off_limits(text)


def test_ordinary_legends_are_allowed():
    assert not stories.is_off_limits("The Brown Mountain Lights", "Mysterious glow over North Carolina")


def test_is_repeat_matches_names_inside_titles():
    topic = stories.Topic("Robert the Doll", "Key West", "1900s", "", "objects")
    assert stories.is_repeat(topic, ["Meet Robert The Doll: Key West's Haunted Legend"])
    assert not stories.is_repeat(topic, ["The Brown Mountain Lights"])


def test_history_and_snapshot_both_count_as_used(tmp_path):
    history = tmp_path / "h.jsonl"
    snapshot = tmp_path / "s.json"
    history.write_text(json.dumps({"topic": "The Octavius"}) + "\nnot json\n")
    snapshot.write_text(json.dumps({"videos": [{"title": "Ogopogo"}]}))
    assert stories.load_used_topics(history, snapshot) == ["The Octavius", "Ogopogo"]


def test_record_and_audit_round_trip(cfg):
    client = FakeClient([_topics("The Octavius"), _research(), _script(), _claims("supported")])
    story = stories.generate_story(client, cfg, rng=random.Random(1), used=[])
    when = datetime(2026, 11, 20, 9, 0)
    stories.record_story(cfg.history_path, story, when, "abc123")
    audit = stories.save_audit(cfg.audit_dir, story, when)
    assert stories.load_used_topics(cfg.history_path) == ["The Octavius"]
    assert json.loads(audit.read_text())["topic"]["name"] == "The Octavius"


def test_config_defaults_weight_ships_highest(tmp_path):
    config = load_config(tmp_path / "missing.yaml")
    top = max(config.stories.categories, key=config.stories.categories.get)
    assert "ship" in top
    assert config.stories.enabled is False
    assert config.stories.model == config.quotes.model


@pytest.mark.parametrize(
    "url,weak",
    [
        ("https://forum.pages14-18.com/viewtopic.php?t=1", True),
        ("https://arugula-pear-f8ed.squarespace.com/zebrina", True),
        ("https://www.reddit.com/r/x", True),
        ("https://example.org/forums/thread", True),
        ("https://www.nationalarchives.gov.uk/x", False),
        ("https://uboat.net/wwi/ships_hit/", False),
        ("https://www.odt.co.nz/news", False),
    ],
)
def test_weak_sources(url, weak):
    assert stories.is_weak_source(url) is weak


def test_weak_sources_do_not_count_toward_the_minimum():
    sources = [
        {"url": "https://forum.a.com/1"},
        {"url": "https://b.squarespace.com"},
        {"url": "https://uboat.net/x"},
    ]
    assert stories.source_domains(sources) == {"uboat.net"}
