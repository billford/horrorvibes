"""Unit tests for imagegen.py.

Only prompt construction, fallback-chain control flow, and file naming are
covered here -- real Stable Diffusion inference and real OpenAI Images API
calls are never exercised (see scripts/smoke_test_imagegen.py for a manual,
non-pytest smoke test of actual local SD inference).
"""

# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison
import dataclasses
from types import SimpleNamespace

import pytest
from PIL import Image

from horrorvibes.config import load_config
from horrorvibes.exceptions import ImageGenerationError
from horrorvibes.imagegen import (
    MfluxBackend,
    build_scene_prompt,
    build_sd_prompt,
    generate_image,
    render_gradient,
)


@pytest.fixture
def config(tmp_path):
    return load_config(tmp_path / "missing.yaml")


def with_backend(config, backend):
    return dataclasses.replace(config, image=dataclasses.replace(config.image, backend=backend))


def test_build_sd_prompt_never_includes_literal_typography_instructions():
    prompt = build_sd_prompt("Redrum.", "The Shining", "expressionist horror painting")
    assert "The Shining" in prompt
    assert "Redrum." in prompt
    assert "expressionist horror painting" in prompt


def test_render_gradient_produces_correct_size_image():
    img = render_gradient(index=0, width=100, height=200)
    assert img.size == (100, 200)
    assert img.mode == "RGB"


def test_render_gradient_cycles_through_color_pairs_deterministically():
    img_a = render_gradient(index=0, width=10, height=10)
    img_b = render_gradient(index=9, width=10, height=10)  # wraps to same pair as index 0
    assert img_a.getpixel((0, 0)) == img_b.getpixel((0, 0))


def test_generate_image_uses_primary_backend_when_it_succeeds(config, tmp_path):
    calls = []

    def local_sd(prompt, index, output_path):
        calls.append("local_sd")
        Image.new("RGB", (4, 4)).save(output_path)

    def openai_backend(prompt, index, output_path):
        calls.append("openai")

    def gradient(prompt, index, output_path):
        calls.append("gradient")

    result = generate_image(
        "'Redrum.' - The Shining",
        0,
        with_backend(config, "local_sd"),
        tmp_path,
        backends={"local_sd": local_sd, "openai": openai_backend, "gradient": gradient},
    )

    assert calls == ["local_sd"]
    assert result.backend_used == "local_sd"
    assert result.path.exists()


def test_generate_image_falls_back_local_sd_to_openai(config, tmp_path, caplog):
    calls = []

    def local_sd(prompt, index, output_path):
        calls.append("local_sd")
        raise RuntimeError("MPS out of memory")

    def openai_backend(prompt, index, output_path):
        calls.append("openai")
        Image.new("RGB", (4, 4)).save(output_path)

    def gradient(prompt, index, output_path):
        calls.append("gradient")

    with caplog.at_level("WARNING"):
        result = generate_image(
            "'Redrum.' - The Shining",
            0,
            with_backend(config, "local_sd"),
            tmp_path,
            backends={"local_sd": local_sd, "openai": openai_backend, "gradient": gradient},
        )

    assert calls == ["local_sd", "openai"]
    assert result.backend_used == "openai"
    assert any("local_sd" in record.message for record in caplog.records)


def test_generate_image_falls_all_the_way_to_gradient(config, tmp_path):
    calls = []

    def failing(prompt, index, output_path):
        raise RuntimeError("nope")

    def gradient(prompt, index, output_path):
        calls.append("gradient")
        Image.new("RGB", (4, 4)).save(output_path)

    result = generate_image(
        "'Redrum.' - The Shining",
        0,
        with_backend(config, "local_sd"),
        tmp_path,
        backends={"local_sd": failing, "openai": failing, "gradient": gradient},
    )

    assert calls == ["gradient"]
    assert result.backend_used == "gradient"


def test_generate_image_backend_openai_skips_local_sd_entirely(config, tmp_path):
    calls = []

    def local_sd(prompt, index, output_path):
        calls.append("local_sd")

    def openai_backend(prompt, index, output_path):
        calls.append("openai")
        Image.new("RGB", (4, 4)).save(output_path)

    result = generate_image(
        "'Redrum.' - The Shining",
        0,
        with_backend(config, "openai"),
        tmp_path,
        backends={"local_sd": local_sd, "openai": openai_backend, "gradient": lambda *a: None},
    )

    assert calls == ["openai"]
    assert result.backend_used == "openai"


def test_generate_image_raises_when_entire_chain_fails(config, tmp_path):
    def failing(prompt, index, output_path):
        raise RuntimeError("nope")

    with pytest.raises(ImageGenerationError):
        generate_image(
            "'Redrum.' - The Shining",
            0,
            with_backend(config, "gradient"),
            tmp_path,
            backends={"gradient": failing},
        )


def test_generate_image_names_file_by_index(config, tmp_path):
    def backend(prompt, index, output_path):
        Image.new("RGB", (2, 2)).save(output_path)

    result = generate_image(
        "'Redrum.' - The Shining",
        4,
        with_backend(config, "gradient"),
        tmp_path,
        backends={"gradient": backend},
    )

    assert result.path.name == "background_5.png"


def test_closest_supported_size_uses_gpt_image_portrait_size():
    from horrorvibes.imagegen import _closest_supported_size

    assert _closest_supported_size("gpt-image-1", 1080, 1920) == "1024x1536"
    assert _closest_supported_size("gpt-image-1", 1920, 1080) == "1536x1024"
    assert _closest_supported_size("dall-e-3", 1080, 1920) == "1024x1792"


# ---- mflux backend & scene prompts ---------------------------------------------------


def test_scene_prompt_combines_scene_style_and_portrait_no_text():
    prompt = build_scene_prompt("A fog-bound pier at dusk.", "woodcut print")
    assert prompt.startswith("A fog-bound pier at dusk. woodcut print.")
    assert "no text" in prompt


def test_generate_image_prefers_visual_scene_and_pack_style(tmp_path):
    config = load_config(tmp_path / "missing.yaml")
    prompts = []
    backends = {name: (lambda p, i, out: (prompts.append(p), out.write_bytes(b"x"))) for name in ("mflux",)}

    result = generate_image(
        "'Redrum.' - The Shining", 0, config, tmp_path, backends=backends,
        visual_scene="An empty hotel corridor", style="VHS still",
    )

    assert result.backend_used == "mflux"
    assert prompts[0].startswith("An empty hotel corridor. VHS still.")
    assert "Shining" not in prompts[0]


def test_mflux_cmd_and_missing_binary(tmp_path, monkeypatch):
    config = load_config(tmp_path / "missing.yaml").image
    backend = MfluxBackend(config, runner=lambda *a, **k: None)
    cmd = backend.build_cmd("a prompt", 42, tmp_path / "o.png")
    assert cmd[0] == "mflux-generate-flux2"
    assert cmd[cmd.index("--model") + 1] == "flux2-klein-4b"
    assert cmd[cmd.index("--prompt") + 1] == "a prompt"
    assert cmd[cmd.index("--seed") + 1] == "42"

    monkeypatch.setattr("horrorvibes.imagegen.shutil.which", lambda name: None)
    with pytest.raises(ImageGenerationError, match="not found"):
        backend("p", 0, tmp_path / "o.png")


def test_mflux_failure_raises_so_chain_falls_through(tmp_path, monkeypatch):
    config = load_config(tmp_path / "missing.yaml").image
    monkeypatch.setattr("horrorvibes.imagegen.shutil.which", lambda name: "/usr/bin/mflux")
    def runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stderr="oom")

    with pytest.raises(ImageGenerationError, match="oom"):
        MfluxBackend(config, runner=runner)("p", 0, tmp_path / "o.png")
