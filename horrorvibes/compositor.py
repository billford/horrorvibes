"""Renders quote text as transparent overlay layers, styled by the run's
style pack.

For the animated video (video.py) the background and text are kept
separate, so the Ken Burns zoom moves the image while the words stay
still. Text is rendered as:

- a **title layer** (the movie/show attribution), and
- **word states**: state *k* has the first *k* words lit and the rest
  dimmed. video.py switches states as each word is narrated, giving
  karaoke-style captions without needing an ffmpeg built with libass.

``compose_frame`` flattens background + layers into one still image -- used
for the static fallback assembly and as a per-quote preview frame.

Text sits clear of the YouTube Shorts UI: nothing is drawn in the top ~10%
or bottom ~22% of the frame.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from horrorvibes.config import CompositorConfig, StylePack
from horrorvibes.textutil import split_quote_and_title, wrap_quote_lines

logger = logging.getLogger(__name__)

_LINE_HEIGHT_FACTOR = 1.45
_SCRIM_PADDING = 28
_DIM_ALPHA = 105
_TITLE_Y = 0.73  # top of the attribution line, as a fraction of height
_LAYOUT_ANCHORS = {"upper": 0.14, "center": 0.40, "lower": 0.68}
_MAX_TEXT_WIDTH = 0.86  # fraction of frame width any text block may use
_REFERENCE_WIDTH = 1080  # configured font sizes are for a 1080px-wide frame
_MIN_FONT_SIZE = 18


@dataclass(frozen=True)
class TextLayers:
    """Overlay PNGs for one quote (all full-frame RGBA)."""

    title: Path
    word_states: list[Path]
    word_count: int


def default_pack(compositor_config: CompositorConfig) -> StylePack:
    """A neutral pack matching the pre-style-pack look."""
    return StylePack(
        name="default",
        image_style="",
        font_paths=list(compositor_config.font_paths),
        scrim_opacity=compositor_config.scrim_opacity,
    )


def select_font_path(font_paths: list[str]) -> str | None:
    """Return the first font path in the configured fallback chain that exists."""
    for font_path in font_paths:
        if Path(font_path).exists():
            return font_path
    return None


def _load_font(paths: list[str], size: int) -> ImageFont.ImageFont:
    font_path = select_font_path(paths)
    if font_path is None:
        logger.warning("No configured font found, using PIL default font")
        return ImageFont.load_default(size=size)
    return ImageFont.truetype(font_path, size)


@dataclass(frozen=True)
class _Layout:
    lines: list[list[str]]
    line_height: int
    top: int
    block_width: float


def _scaled(size: int, width: int) -> int:
    return max(_MIN_FONT_SIZE, round(size * width / _REFERENCE_WIDTH))


def _fit_font(paths: list[str], size: int, texts: list[str], width: int):
    """Largest font at or below ``size`` whose widest text fits the frame."""
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    while True:
        font = _load_font(paths, size)
        widest = max((scratch.textlength(text, font=font) for text in texts), default=0)
        if widest <= width * _MAX_TEXT_WIDTH or size <= _MIN_FONT_SIZE:
            return font, size
        size = max(_MIN_FONT_SIZE, int(size * 0.9))


def _layout_quote(  # pylint: disable=too-many-arguments
    draw: ImageDraw.ImageDraw, lines: list[list[str]], font, font_size: int, pack: StylePack, height: int
) -> _Layout:
    line_height = int(font_size * _LINE_HEIGHT_FACTOR)
    block_height = line_height * max(len(lines), 1)
    anchor = _LAYOUT_ANCHORS.get(pack.layout, _LAYOUT_ANCHORS["center"])
    if pack.layout == "center":
        top = int(height * anchor - block_height / 2)
    elif pack.layout == "lower":
        top = int(height * anchor - block_height)
    else:
        top = int(height * anchor)
    top = max(int(height * 0.10), min(top, int(height * _TITLE_Y) - block_height - _SCRIM_PADDING * 2))
    block_width = max((draw.textlength(" ".join(line), font=font) for line in lines), default=0)
    return _Layout(lines=lines, line_height=line_height, top=top, block_width=block_width)


def _draw_scrim(draw, center_x: float, top: float, bottom: float, block_width: float, opacity: int) -> None:
    if opacity <= 0:
        return
    draw.rounded_rectangle(
        (
            center_x - block_width / 2 - _SCRIM_PADDING,
            top - _SCRIM_PADDING,
            center_x + block_width / 2 + _SCRIM_PADDING,
            bottom + _SCRIM_PADDING,
        ),
        radius=22,
        fill=(0, 0, 0, opacity),
    )


def _render_word_state(  # pylint: disable=too-many-arguments,too-many-locals
    layout: _Layout, font, pack: StylePack, width: int, height: int, lit_count: int
) -> Image.Image:
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bottom = layout.top + layout.line_height * len(layout.lines)
    _draw_scrim(draw, width / 2, layout.top, bottom, layout.block_width, pack.scrim_opacity)

    space = draw.textlength(" ", font=font)
    word_index = 0
    y = layout.top
    for line in layout.lines:
        x = (width - draw.textlength(" ".join(line), font=font)) / 2
        for word in line:
            lit = word_index < lit_count
            fill = (*pack.highlight_color, 255) if lit else (*pack.text_color, _DIM_ALPHA)
            draw.text(
                (x, y), word, font=font, fill=fill,
                stroke_width=pack.stroke_width, stroke_fill=(0, 0, 0, 255 if lit else _DIM_ALPHA),
            )
            x += draw.textlength(word, font=font) + space
            word_index += 1
        y += layout.line_height
    return layer


def _render_title(  # pylint: disable=too-many-arguments
    movie_title: str, font, pack: StylePack, width: int, height: int, font_size: int
) -> Image.Image:
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    text = f"— {movie_title}"
    text_width = draw.textlength(text, font=font)
    top = int(height * _TITLE_Y)
    _draw_scrim(draw, width / 2, top, top + font_size * 1.2, text_width, pack.scrim_opacity)
    draw.text(
        ((width - text_width) / 2, top), text, font=font, fill=(*pack.text_color, 255),
        stroke_width=max(pack.stroke_width - 1, 0), stroke_fill=(0, 0, 0, 255),
    )
    return layer


def render_text_layers(  # pylint: disable=too-many-arguments,too-many-locals
    quote_text: str,
    movie_title: str,
    pack: StylePack,
    compositor_config: CompositorConfig,
    width: int,
    height: int,
    out_dir: Path,
    index: int,
    karaoke: bool = True,
) -> TextLayers:
    """Write the title layer and word-state layers for one quote. With
    ``karaoke`` False there is a single, fully lit state."""
    out_dir.mkdir(parents=True, exist_ok=True)
    font_paths = list(pack.font_paths) + list(compositor_config.font_paths)
    lines = [line.split() for line in wrap_quote_lines(quote_text, compositor_config.max_chars_per_line)]
    quote_font, quote_size = _fit_font(
        font_paths,
        _scaled(compositor_config.quote_font_size, width),
        [" ".join(line) for line in lines],
        width,
    )
    title_font, title_size = _fit_font(
        font_paths, _scaled(compositor_config.movie_font_size, width), [f"\u2014 {movie_title}"], width
    )

    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    layout = _layout_quote(scratch, lines, quote_font, quote_size, pack, height)
    word_count = sum(len(line) for line in layout.lines)

    title_path = out_dir / f"title_{index + 1}.png"
    _render_title(movie_title, title_font, pack, width, height, title_size).save(title_path)

    lit_counts = range(word_count + 1) if karaoke else [word_count]
    state_paths = []
    for lit in lit_counts:
        path = out_dir / f"words_{index + 1}_{lit:02d}.png"
        _render_word_state(layout, quote_font, pack, width, height, lit).save(path)
        state_paths.append(path)
    return TextLayers(title=title_path, word_states=state_paths, word_count=word_count)


def flatten_frame(
    background_path: Path, layers: TextLayers, width: int, height: int, output_path: Path
) -> Path:
    """Background (resized to fill) + title + fully lit quote, as one RGB still."""
    frame = Image.open(background_path).convert("RGBA")
    if frame.size != (width, height):
        frame = frame.resize((width, height), Image.Resampling.LANCZOS)
    for layer_path in (layers.title, layers.word_states[-1]):
        with Image.open(layer_path) as layer:
            frame = Image.alpha_composite(frame, layer.convert("RGBA"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.convert("RGB").save(output_path)
    return output_path


def compose_frame(  # pylint: disable=too-many-arguments
    background_path: Path,
    quote: str,
    compositor_config: CompositorConfig,
    width: int,
    height: int,
    output_path: Path,
    pack: StylePack | None = None,
) -> Path:
    """Render one still frame: background + quote + attribution."""
    quote_text, movie_title = split_quote_and_title(quote)
    with tempfile.TemporaryDirectory() as tmp:
        layers = render_text_layers(
            quote_text, movie_title, pack or default_pack(compositor_config), compositor_config,
            width, height, Path(tmp), 0, karaoke=False,
        )
        return flatten_frame(background_path, layers, width, height, output_path)
