"""AI-generated background images: mflux (FLUX.2) -> local SD -> OpenAI -> gradient.

Backend chain is deliberately injectable (see ``generate_image``'s
``backends`` parameter) so unit tests can assert fallback ordering and
logging without ever loading a real diffusion model or calling a real
API -- see the module docstring in tests/test_imagegen.py.
"""

from __future__ import annotations

import base64
import logging
import random
import shutil
import sys
import subprocess  # nosec B404 - no shell=True anywhere below
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image, ImageDraw, ImageStat

from horrorvibes.config import Config, ImageConfig
from horrorvibes.exceptions import ImageGenerationError
from horrorvibes.textutil import split_quote_and_title

logger = logging.getLogger(__name__)

BackendFn = Callable[[str, int, Path], None]

_BACKEND_CHAIN: dict[str, tuple[str, ...]] = {
    "mflux": ("mflux", "local_sd", "openai", "gradient"),
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
    """Fallback prompt when no scene description is available. Never renders
    the quote as literal text -- the words are composited separately."""
    return f'Visual mood inspired by the horror film "{movie_title}": {quote_text}. {style_suffix}'


def build_scene_prompt(visual_scene: str, style: str) -> str:
    """Prompt from the quote model's original scene description plus the
    style pack's look. Portrait framing and no lettering are stated
    explicitly, since text is composited separately."""
    scene = visual_scene.strip().rstrip(".")
    return (
        f"{scene}. {style}. Vertical portrait composition, full-bleed edge to edge, "
        "no border or frame, no text, lettering, signature or watermark."
    )


def _flat_depth(gray: Image.Image, side: str, limit: int, max_std: float) -> tuple[int, float]:
    """How many rows/columns in from ``side`` are near-uniform, and their mean."""
    width, height = gray.size
    depth, means = 0, []
    for k in range(limit):
        box = {
            "top": (0, k, width, k + 1), "bottom": (0, height - 1 - k, width, height - k),
            "left": (k, 0, k + 1, height), "right": (width - 1 - k, 0, width - k, height),
        }[side]
        stat = ImageStat.Stat(gray.crop(box))
        if stat.stddev[0] > max_std:
            break
        depth, means = k + 1, means + [stat.mean[0]]
    return depth, (sum(means) / len(means) if means else 0.0)


def trim_border(  # pylint: disable=too-many-locals
    path: Path, max_fraction: float = 0.12, max_std: float = 14.0, min_px: int = 4
) -> bool:
    """Crop a drawn print/photo border off a generated image, keeping its size
    and aspect. Only fires when a flat band of similar colour runs round all
    four edges. Returns whether it
    trimmed."""
    with Image.open(path) as opened:
        img = opened.convert("RGB")
    width, height = img.size
    gray = img.convert("L")
    sides = {
        side: _flat_depth(gray, side, int((height if side in ("top", "bottom") else width) * max_fraction),
                          max_std)
        for side in ("top", "bottom", "left", "right")
    }
    depths = [d for d, _ in sides.values()]
    means = [m for _, m in sides.values()]
    # Light borders only: horror scenes are often black at every edge, and a
    # dark frame can't be told apart from a dark night.
    if min(depths) < min_px or min(means) < 180:
        return False
    pad = 2  # the frame's inner edge is often a soft line
    left, top = sides["left"][0] + pad, sides["top"][0] + pad
    right, bottom = width - sides["right"][0] - pad, height - sides["bottom"][0] - pad
    inner_w, inner_h = right - left, bottom - top
    # centre-crop the inside back to the original aspect before scaling up
    target = width / height
    if inner_w / inner_h > target:
        new_w = int(inner_h * target)
        left += (inner_w - new_w) // 2
        right = left + new_w
    else:
        new_h = int(inner_w / target)
        top += (inner_h - new_h) // 2
        bottom = top + new_h
    img.crop((left, top, right, bottom)).resize((width, height), Image.Resampling.LANCZOS).save(path)
    return True


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


class MfluxBackend:
    """FLUX.2 Klein through mflux (Apple MLX), invoked as a CLI so the heavy
    MLX stack stays out of this process and a hung generation can be timed
    out. Fails fast (and the chain falls through) if mflux isn't installed."""

    def __init__(
        self, config: ImageConfig, runner=subprocess.run, rng: random.Random | None = None
    ) -> None:
        self._config = config
        self._runner = runner
        self._rng = rng or random.Random()  # nosec B311 - image seed variety, not security-sensitive

    def build_cmd(self, prompt: str, seed: int, output_path: Path) -> list[str]:
        """argv for one generation -- pure, for testing."""
        return [
            self._config.mflux_command,
            "--model", self._config.mflux_model,
            "--prompt", prompt,
            "--steps", str(self._config.mflux_steps),
            "--seed", str(seed),
            "--width", str(self._config.mflux_width),
            "--height", str(self._config.mflux_height),
            "--output", str(output_path),
        ]

    def resolve_command(self) -> str | None:
        """The mflux executable: on PATH, else next to the running Python.
        ``pip install mflux`` puts it in the venv's bin/, which isn't on
        PATH when the venv isn't activated (launchd, or calling
        .venv/bin/python directly)."""
        found = shutil.which(self._config.mflux_command)
        if found:
            return found
        beside_python = Path(sys.executable).parent / self._config.mflux_command
        return str(beside_python) if beside_python.is_file() else None

    def __call__(self, prompt: str, index: int, output_path: Path) -> None:
        command = self.resolve_command()
        if command is None:
            raise ImageGenerationError(
                f"{self._config.mflux_command} not found on PATH or beside {sys.executable} "
                "(pip install mflux)"
            )
        cmd = self.build_cmd(prompt, self._rng.randint(0, 2**31 - 1), output_path)
        cmd[0] = command
        # nosec B603 - argv list, no shell; prompt is passed as a single argument
        result = self._runner(  # nosec B603
            cmd, capture_output=True, text=True, check=False, timeout=self._config.mflux_timeout_sec
        )
        if result.returncode != 0 or not output_path.exists():
            raise ImageGenerationError(f"mflux failed ({result.returncode}): {result.stderr[-500:]}")


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
        size = _closest_supported_size(self._config.openai_model, self._width, self._height)
        response = client.images.generate(
            model=self._config.openai_model,
            prompt=prompt,
            size=size,
            n=1,
        )
        image_b64 = response.data[0].b64_json
        output_path.write_bytes(base64.b64decode(image_b64))


def _closest_supported_size(model: str, width: int, height: int) -> str:
    """OpenAI's image API accepts a fixed, per-model set of sizes; map our
    9:16 target to the closest portrait option instead of passing an
    unsupported size. dall-e-3 offers 1024x1792, but gpt-image-* models only
    accept 1024x1536 and reject 1792 outright."""
    long_side = "1792" if model.startswith("dall-e") else "1536"
    return f"1024x{long_side}" if height >= width else f"{long_side}x1024"


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
        "mflux": MfluxBackend(config.image),
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
    visual_scene: str | None = None,
    style: str | None = None,
) -> ImageResult:
    """Generate one background image for ``quote``, walking the fallback chain
    rooted at ``config.image.backend``. Always returns a result -- the
    gradient backend never raises, so a run never fails outright over one
    image (per spec)."""
    backends = backends if backends is not None else default_backends(config)
    chain = _BACKEND_CHAIN[config.image.backend]

    style = style or config.image.style_suffix
    if visual_scene:
        prompt = build_scene_prompt(visual_scene, style)
    else:
        quote_text, movie_title = split_quote_and_title(quote)
        prompt = build_sd_prompt(quote_text, movie_title, style)

    images_dir.mkdir(parents=True, exist_ok=True)
    output_path = images_dir / f"background_{index + 1}.png"

    output_path.unlink(missing_ok=True)  # never mistake last run's image for this one's
    for name in chain:
        try:
            backends[name](prompt, index, output_path)
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Image backend %r failed for quote %d, falling back", name, index + 1, exc_info=True
            )
            continue
        logger.info("Image %d generated via %r backend", index + 1, name)
        if name != "gradient":
            try:
                if trim_border(output_path):
                    logger.info("Trimmed a drawn border off image %d", index + 1)
            except OSError:
                logger.warning("Border check failed for image %d", index + 1, exc_info=True)
        return ImageResult(path=output_path, backend_used=name)

    raise ImageGenerationError(f"All image backends in chain {chain} failed for quote index {index}")
