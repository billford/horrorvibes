#!/usr/bin/env python3
"""Manual smoke test for real local Stable Diffusion inference.

Not run by pytest -- model loading and inference are slow/heavy and are
deliberately excluded from the automated test suite (see tests/test_imagegen.py
docstring). Run this by hand on lola after changing sd_model, sd_steps, or
the LocalSDBackend implementation, to eyeball actual output quality/timing.

Usage:
    python3 scripts/smoke_test_imagegen.py "'Come with me if you want to live.' - The Terminator (1984)"
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position,import-error
from horrorvibes.config import load_config
from horrorvibes.imagegen import LocalSDBackend, build_sd_prompt
from horrorvibes.textutil import split_quote_and_title

# pylint: enable=wrong-import-position,import-error


def main() -> None:
    """Generate one real image via local SD and report how long it took."""
    quote = sys.argv[1] if len(sys.argv) > 1 else "'They're here.' - Poltergeist (1982)"
    config = load_config("config.yaml")

    quote_text, movie_title = split_quote_and_title(quote)
    prompt = build_sd_prompt(quote_text, movie_title, config.image.style_suffix)
    print(f"Prompt: {prompt}")

    backend = LocalSDBackend(config.image, config.run.width, config.run.height)
    output_path = Path("./smoke_test_output.png")

    start = time.monotonic()
    backend(prompt, 0, output_path)
    elapsed = time.monotonic() - start

    print(f"Saved {output_path} in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
