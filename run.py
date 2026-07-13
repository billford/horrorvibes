#!/usr/bin/env python3
"""Thin CLI entrypoint. All settings live in config.yaml; the flags below
only cover what you'd plausibly want to override per-invocation."""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys

import openai
from dotenv import load_dotenv

from horrorvibes.config import load_config
from horrorvibes.logging_config import configure_logging
from horrorvibes.orchestrator import main_unattended


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the small set of per-invocation CLI overrides."""
    parser = argparse.ArgumentParser(description="Horror Movie Quote Video Generator")
    parser.add_argument(
        "--config", default="config.yaml", help="Path to config.yaml (default: ./config.yaml)"
    )
    parser.add_argument(
        "--quotes", type=int, default=None, help="Override run.quote_count for this invocation"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Run the full pipeline but never upload to YouTube"
    )
    parser.add_argument(
        "--force", action="store_true", help="Ignore the every-other-day cadence guard and run now"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: load config, wire up the OpenAI client, run the pipeline."""
    load_dotenv()
    args = parse_args(argv)

    config = load_config(args.config)
    if args.dry_run:
        no_upload = dataclasses.replace(config.publish, youtube_upload=False)
        config = dataclasses.replace(config, publish=no_upload)

    configure_logging(config.logging)

    chat_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    force = args.force or args.quotes is not None
    return main_unattended(config, chat_client, force=force, quote_count=args.quotes)


if __name__ == "__main__":
    sys.exit(main())
