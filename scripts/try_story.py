#!/usr/bin/env python3
"""Research, write and fact-check one story Short, and print it. Renders and uploads nothing.

Usage:
    python3 scripts/try_story.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position,import-error
from dotenv import load_dotenv
from openai import OpenAI

from horrorvibes import stories
from horrorvibes.config import load_config

# pylint: enable=wrong-import-position,import-error


def main() -> int:
    """Generate one story and print its title, script, claims and sources."""
    load_dotenv(".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config("config.yaml").stories
    story = stories.generate_story(OpenAI(), cfg)
    audit = stories.save_audit(cfg.audit_dir, story, datetime.now())

    print(f"\n{story.script.title}\n({story.topic.category}; {story.script.word_count} words)\n")
    for i, beat in enumerate(story.script.beats, 1):
        print(f"[{i}] ({beat.delivery}) {beat.narration}\n    image: {beat.visual_scene}")
    print("\nPronunciations:")
    for name in story.research.names:
        print(f"- {name['name']}: {name['respelling']} {name['ipa']}".rstrip())
    print(f"\nClaims checked: {len(story.claims)}, all supported")
    print("\n" + story.description())
    print(f"\nAudit saved to {audit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
