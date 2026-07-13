# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

from PIL import Image

from horrorvibes.compositor import compose_frame, select_font_path
from horrorvibes.config import CompositorConfig


def test_select_font_path_returns_first_existing_path(tmp_path):
    missing = tmp_path / "missing.ttf"
    present = tmp_path / "present.ttf"
    present.write_bytes(b"not-a-real-font-but-exists")

    assert select_font_path([str(missing), str(present)]) == str(present)


def test_select_font_path_returns_none_when_nothing_exists(tmp_path):
    assert select_font_path([str(tmp_path / "a.ttf"), str(tmp_path / "b.ttf")]) is None


def _config(**overrides):
    defaults = {
        "font_paths": [],  # forces the PIL default font -- deterministic, no system dependency
        "quote_font_size": 20,
        "movie_font_size": 16,
        "max_chars_per_line": 15,
        "scrim_opacity": 140,
    }
    defaults.update(overrides)
    return CompositorConfig(**defaults)


def test_compose_frame_produces_correctly_sized_rgb_image(tmp_path):
    background_path = tmp_path / "bg.png"
    Image.new("RGB", (200, 300), color=(10, 10, 10)).save(background_path)
    output_path = tmp_path / "frame.png"

    result = compose_frame(
        background_path, "'Redrum.' - The Shining (1980)", _config(), 200, 300, output_path
    )

    assert result == output_path
    with Image.open(output_path) as img:
        assert img.size == (200, 300)
        assert img.mode == "RGB"


def test_compose_frame_resizes_mismatched_background(tmp_path):
    background_path = tmp_path / "bg.png"
    Image.new("RGB", (50, 50), color=(0, 0, 0)).save(background_path)
    output_path = tmp_path / "frame.png"

    compose_frame(background_path, "'Hi.' - Movie", _config(), 200, 300, output_path)

    with Image.open(output_path) as img:
        assert img.size == (200, 300)


def test_compose_frame_handles_missing_movie_title(tmp_path):
    background_path = tmp_path / "bg.png"
    Image.new("RGB", (200, 300)).save(background_path)
    output_path = tmp_path / "frame.png"

    # No " - " separator at all -- should not raise, title falls back to Unknown.
    compose_frame(background_path, "Just a quote with no title", _config(), 200, 300, output_path)

    assert output_path.exists()


def test_compose_frame_handles_long_multiline_quotes(tmp_path):
    background_path = tmp_path / "bg.png"
    Image.new("RGB", (200, 300)).save(background_path)
    output_path = tmp_path / "frame.png"

    long_quote = "'" + " ".join(["word"] * 30) + ".' - Some Very Long Movie Title (1999)"
    compose_frame(background_path, long_quote, _config(), 200, 300, output_path)

    assert output_path.exists()
