#!/usr/bin/env python3
"""Manual smoke test for a real ElevenLabs Music API call.

Not run by pytest (costs money and needs network/secrets -- see
tests/test_musicgen.py, which mocks the backend entirely). Requires
ELEVENLABS_API_KEY in the environment or .env file.

Usage:
    python3 scripts/smoke_test_musicgen.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position,import-error
from dotenv import load_dotenv
from horrorvibes.config import load_config
from horrorvibes.musicgen import generate_music

# pylint: enable=wrong-import-position,import-error


def main() -> None:
    """Generate one real music track via the ElevenLabs API and report the result."""
    load_dotenv()
    config = load_config("config.yaml")
    output_path = Path("./smoke_test_music.mp3")

    result = generate_music(config, output_path, api_key=os.getenv("ELEVENLABS_API_KEY"))
    print(f"Saved {result.path} via backend={result.backend_used}")


if __name__ == "__main__":
    main()
