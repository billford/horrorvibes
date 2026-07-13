"""AI-generated background images, with a local-SD -> OpenAI -> gradient chain.

Backend chain is deliberately injectable (see ``generate_image``'s
``backends`` parameter) so unit tests can assert fallback ordering and
logging without ever loading a real diffusion model or calling a real
API -- see the module docstring in tests/test_imagegen.py.
"""

from __future__ import annotations

import base64
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image, ImageDraw

from horrorvibes.config import Config, ImageConfig
from horrorvibes.exceptions import ImageGenerationError
from horrorvibes.textutil import split_quote_and_title

logger = logging.getLogger(__name__)

BackendFn = Callable[[str, int, Path], None]

_BACKEND_CHAIN: dict[str, tuple[str, ...]] = {
    "local_sd": ("local_sd", "openai", "gradient"),
    "openai": ("openai", "gradient"),
    "gradient": ("gradient",),
}

_GRADIENT_COLOR_PAIRS: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
    ((120, 0, 0), (40, 0, 0)),
    ((0, 0, 120), (0, 0, 40)),
    ((80, 0, 100), (30, 0, 40)),
    ((0, 80, 80), (0, 30, 30)),
    ((100, 80, 0), (40, 30, 0)),
    ((80, 80, 80), (30, 30, 30)),
    ((0, 100, 0), (0, 40, 0)),
    ((100, 0, 100), (40, 0, 40)),
    ((100, 50, 0), (40, 20, 0)),
)


@dataclass(frozen=True)
class ImageResult:
    """Which file was produced and which backend in the chain produced it."""

    path: Path
    backend_used: str


def build_sd_prompt(quote_text: str, movie_title: str, style_suffix: str) -> str:
    """Build the diffusion prompt. Never renders the quote as literal text --
    SD is unreliable at in-image typography, so this only conveys mood; the
    words themselves are composited separately (see compositor.py)."""
    return f'Visual mood inspired by the horror film "{movie_title}": {quote_text}. {style_suffix}'


class ImageBackend(Protocol):
    """Callable shape every entry in the fallback chain must implement."""

    def __call__(self, prompt: str, index: int, output_path: Path) -> None: ...


class LocalSDBackend:
    """Local Stable Diffusion via diffusers on the Apple Silicon MPS backend.

    The pipeline is loaded lazily on first use and cached on the instance,
    so importing this module (or constructing this class) never triggers
    a multi-GB model download / load.
    """

    def __init__(self, config: ImageConfig, width: int, height: int) -> None:
        self._config = config
        self._width = width
        self._height = height
        self._pipeline = None

    def _pipeline_or_load(self):
        if self._pipeline is None:
            import torch  # pylint: disable=import-outside-toplevel
            from diffusers import AutoPipelineForText2Image  # pylint: disable=import-outside-toplevel

            logger.info(
                "Loading local SD model %s (device=%s)", self._config.sd_model, self._config.sd_device
            )
            pipeline = AutoPipelineForText2Image.from_pretrained(
                self._config.sd_model, torch_dtype=torch.float16
            )
            self._pipeline = pipeline.to(self._config.sd_device)
        return self._pipeline

    def __call__(self, prompt: str, index: int, output_path: Path) -> None:
        pipeline = self._pipeline_or_load()
        result = pipeline(
            prompt=prompt,
            num_inference_steps=self._config.sd_steps,
            guidance_scale=self._config.sd_guidance_scale,
            width=self._width,
            height=self._height,
        )
        result.images[0].save(output_path)


class OpenAIImageBackend:
    """Fallback path: OpenAI Images API, using the subscription the user already pays for."""

    def __init__(self, config: ImageConfig, width: int, height: int, client=None) -> None:
        self._config = config
        self._width = width
        self._height = height
        self._client = client

    def _client_or_default(self):
        if self._client is None:
            import openai  # pylint: disable=import-outside-toplevel

            self._client = openai.OpenAI()
        return self._client

    def __call__(self, prompt: str, index: int, output_path: Path) -> None:
        client = self._client_or_default()
        size = _closest_supported_size(self._width, self._height)
        response = client.images.generate(
            model=self._config.openai_model,
            prompt=prompt,
            size=size,
            n=1,
        )
        image_b64 = response.data[0].b64_json
        output_path.write_bytes(base64.b64decode(image_b64))


def _closest_supported_size(width: int, height: int) -> str:
    """OpenAI's image API accepts a fixed set of sizes; map our 9:16 target
    to the closest portrait option instead of passing an unsupported size."""
    return "1024x1792" if height >= width else "1792x1024"


def render_gradient(  # pylint: disable=too-many-locals
    index: int, width: int, height: int, rng: random.Random | None = None
) -> Image.Image:
    """Pure-ish gradient + texture renderer (the pre-existing last-resort look)."""
    rng = rng or random.Random()  # nosec B311 - cosmetic texture variation, not security-sensitive
    color1, color2 = _GRADIENT_COLOR_PAIRS[index % len(_GRADIENT_COLOR_PAIRS)]

    img = Image.new("RGB", (width, height), color=color1)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        r = int(color1[0] + (color2[0] - color1[0]) * y / height)
        g = int(color1[1] + (color2[1] - color1[1]) * y / height)
        b = int(color1[2] + (color2[2] - color1[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    texture = img.convert("RGBA")
    texture_draw = ImageDraw.Draw(texture)
    for _ in range(100):
        x = rng.randint(0, width)
        y = rng.randint(0, height)
        size = rng.randint(5, 100)
        texture_draw.ellipse((x - size, y - size, x + size, y + size), fill=(0, 0, 0, rng.randint(0, 50)))

    return texture.convert("RGB")


class GradientBackend:
    """Last-resort backend: no AI involved, always succeeds."""

    def __init__(self, width: int, height: int, rng: random.Random | None = None) -> None:
        self._width = width
        self._height = height
        self._rng = rng

    def __call__(self, prompt: str, index: int, output_path: Path) -> None:
        render_gradient(index, self._width, self._height, self._rng).save(output_path)


def default_backends(config: Config) -> dict[str, ImageBackend]:
    """Build the real, config-driven backend instances for each chain link."""
    return {
        "local_sd": LocalSDBackend(config.image, config.run.width, config.run.height),
        "openai": OpenAIImageBackend(config.image, config.run.width, config.run.height),
        "gradient": GradientBackend(config.run.width, config.run.height),
    }


def generate_image(
    quote: str,
    index: int,
    config: Config,
    images_dir: Path,
    backends: dict[str, ImageBackend] | None = None,
) -> ImageResult:
    """Generate one background image for ``quote``, walking the fallback chain
    rooted at ``config.image.backend``. Always returns a result -- the
    gradient backend never raises, so a run never fails outright over one
    image (per spec)."""
    backends = backends if backends is not None else default_backends(config)
    chain = _BACKEND_CHAIN[config.image.backend]

    quote_text, movie_title = split_quote_and_title(quote)
    prompt = build_sd_prompt(quote_text, movie_title, config.image.style_suffix)

    images_dir.mkdir(parents=True, exist_ok=True)
    output_path = images_dir / f"background_{index + 1}.png"

    for name in chain:
        try:
            backends[name](prompt, index, output_path)
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Image backend %r failed for quote %d, falling back", name, index + 1, exc_info=True
            )
            continue
        logger.info("Image %d generated via %r backend", index + 1, name)
        return ImageResult(path=output_path, backend_used=name)

    raise ImageGenerationError(f"All image backends in chain {chain} failed for quote index {index}")
