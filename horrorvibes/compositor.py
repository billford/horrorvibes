"""Composites quote text onto a generated background image.

The original script tinted the *entire* frame with a flat semi-transparent
black overlay, which flattens AI-generated art. This version draws a scrim
only behind the text blocks, so the generated image stays the visual
centerpiece (per spec 5.3).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from horrorvibes.config import CompositorConfig
from horrorvibes.textutil import split_quote_and_title, wrap_quote_lines

logger = logging.getLogger(__name__)

_LINE_HEIGHT_FACTOR = 1.7
_SCRIM_PADDING = 24


def select_font_path(font_paths: list[str]) -> str | None:
    """Return the first font path in the configured fallback chain that exists."""
    for font_path in font_paths:
        if Path(font_path).exists():
            return font_path
    return None


def _load_fonts(compositor_config: CompositorConfig) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    font_path = select_font_path(compositor_config.font_paths)
    if font_path is None:
        logger.warning("No configured font found, using PIL default font")
        return ImageFont.load_default(), ImageFont.load_default()

    quote_font = ImageFont.truetype(font_path, compositor_config.quote_font_size)
    movie_font = ImageFont.truetype(font_path, compositor_config.movie_font_size)
    return quote_font, movie_font


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    return draw.textlength(text, font=font)


def _draw_scrim(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    top_y: int,
    bottom_y: int,
    block_width: float,
    opacity: int,
) -> None:
    left = center_x - block_width / 2 - _SCRIM_PADDING
    right = center_x + block_width / 2 + _SCRIM_PADDING
    draw.rounded_rectangle(
        (left, top_y - _SCRIM_PADDING, right, bottom_y + _SCRIM_PADDING),
        radius=20,
        fill=(0, 0, 0, opacity),
    )


def compose_frame(  # pylint: disable=too-many-locals
    background_path: Path,
    quote: str,
    compositor_config: CompositorConfig,
    width: int,
    height: int,
    output_path: Path,
) -> Path:
    """Render one frame: background image + scrim-backed quote text + movie title."""
    background = Image.open(background_path).convert("RGBA")
    if background.size != (width, height):
        background = background.resize((width, height), Image.Resampling.LANCZOS)

    text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)

    quote_text, movie_title = split_quote_and_title(quote)
    quote_font, movie_font = _load_fonts(compositor_config)
    lines = wrap_quote_lines(quote_text, compositor_config.max_chars_per_line)
    line_height = int(compositor_config.quote_font_size * _LINE_HEIGHT_FACTOR)

    quote_top = height // 4
    quote_bottom = quote_top + line_height * max(len(lines), 1)
    quote_block_width = max((_text_width(draw, line, quote_font) for line in lines), default=0)
    _draw_scrim(draw, width // 2, quote_top, quote_bottom, quote_block_width, compositor_config.scrim_opacity)

    y = quote_top
    for line in lines:
        text_width = _text_width(draw, line, quote_font)
        x = (width - text_width) / 2
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=quote_font)
        y += line_height

    movie_text = f"- {movie_title}"
    movie_y = height * 3 // 4
    movie_height = int(compositor_config.movie_font_size * _LINE_HEIGHT_FACTOR)
    movie_width = _text_width(draw, movie_text, movie_font)
    _draw_scrim(
        draw, width // 2, movie_y, movie_y + movie_height, movie_width, compositor_config.scrim_opacity
    )
    movie_x = (width - movie_width) / 2
    draw.text((movie_x, movie_y), movie_text, fill=(255, 255, 255, 255), font=movie_font)

    composited = Image.alpha_composite(background, text_layer).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composited.save(output_path)
    return output_path
