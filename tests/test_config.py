# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

from pathlib import Path

import pytest

from horrorvibes.config import ConfigError, load_config


def test_defaults_load_with_no_file(tmp_path):
    config = load_config(tmp_path / "does_not_exist.yaml")
    assert config.run.quote_count == 9
    assert config.image.backend == "local_sd"
    assert config.music.backend == "elevenlabs"
    assert config.publish.privacy_status == "private"


def test_partial_override_merges_with_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("run:\n  quote_count: 20\n", encoding="utf-8")

    config = load_config(path)

    assert config.run.quote_count == 20
    assert config.run.duration_per_quote_sec == 10  # untouched default


@pytest.mark.parametrize(
    "override,message_fragment",
    [
        ({"run": {"quote_count": 0}}, "quote_count"),
        ({"run": {"duration_per_quote_sec": -1}}, "duration_per_quote_sec"),
        ({"image": {"backend": "midjourney"}}, "image.backend"),
        ({"music": {"backend": "suno"}}, "music.backend"),
        ({"publish": {"privacy_status": "super-public"}}, "privacy_status"),
        ({"logging": {"level": "VERBOSE"}}, "logging.level"),
    ],
)
def test_invalid_values_raise_config_error(tmp_path, override, message_fragment):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(override), encoding="utf-8")

    with pytest.raises(ConfigError, match=message_fragment):
        load_config(path)


def test_local_is_a_valid_music_backend(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("music:\n  backend: local\n", encoding="utf-8")

    config = load_config(path)

    assert config.music.backend == "local"
    assert config.music.local_model == "stabilityai/stable-audio-open-1.0"


def test_local_crossfade_must_be_shorter_than_segment(tmp_path):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"music": {"local_segment_sec": 10, "local_crossfade_sec": 10}}), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="local_crossfade_sec"):
        load_config(path)


def test_mood_pool_sample_size_cannot_exceed_pool(tmp_path):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"music": {"mood_pool": ["a", "b"], "mood_pool_sample_size": 5}}), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="mood_pool_sample_size"):
        load_config(path)


def test_non_mapping_yaml_raises_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_malformed_yaml_raises_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("run: [unterminated\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_paths_are_resolved_to_path_objects(tmp_path):
    config = load_config(tmp_path / "missing.yaml")
    assert isinstance(config.run.quotes_dir, Path)
    assert isinstance(config.publish.token_path, Path)
