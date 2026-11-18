#!/usr/bin/env python3
"""Pull every upload's performance into channel_analytics.json and print a short summary.

Needs a token.json granted with yt-analytics.readonly (re-run scripts/authorize_youtube.py once if
this fails with "insufficient authentication scopes") and the YouTube Analytics API enabled in the
Google Cloud project.

Usage:
    python3 scripts/channel_analytics.py [--out channel_analytics.json]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position,import-error
from googleapiclient.discovery import build
from horrorvibes import analytics
from horrorvibes.config import load_config
from horrorvibes.publish import get_unattended_credentials

# pylint: enable=wrong-import-position,import-error

FIRST_UPLOAD_YEAR_START = date(2024, 1, 1)


def main() -> int:
    """Fetch, merge with the pipeline catalog, save, and print medians by format."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="channel_analytics.json")
    args = parser.parse_args()

    config = load_config("config.yaml")
    creds = get_unattended_credentials(config.publish.token_path)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)

    channel_id, uploads = analytics.fetch_uploads(youtube)
    today = date.today()
    metrics = analytics.fetch_video_metrics(yt_analytics, FIRST_UPLOAD_YEAR_START, today)
    traffic = analytics.fetch_traffic_sources(yt_analytics, FIRST_UPLOAD_YEAR_START, today)
    catalog = analytics.load_catalog(config.run.video_catalog_path)

    snapshot = analytics.merge_snapshot(channel_id, uploads, metrics, catalog, traffic)
    analytics.write_snapshot(snapshot, Path(args.out))
    print(f"Saved {len(uploads)} uploads ({len(metrics)} with Analytics metrics) to {args.out}")

    for row in analytics.summarize(snapshot["videos"], "pipeline.style_pack"):
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
