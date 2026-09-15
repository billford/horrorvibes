#!/usr/bin/env python3
"""One-time interactive YouTube OAuth grant that produces token.json.

Unattended runs (publish.py) only ever refresh an existing token and never
open a browser, so this must be run once, by hand, at the machine's own
screen, before uploads can work. Needs client_secret.json (a Google Cloud
"Desktop app" OAuth client with the YouTube Data API v3 enabled) at
publish.client_secrets_path.

Usage:
    python3 scripts/authorize_youtube.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position,import-error
from google_auth_oauthlib.flow import InstalledAppFlow
from horrorvibes.config import load_config
from horrorvibes.exceptions import PublishError
from horrorvibes.publish import YOUTUBE_UPLOAD_SCOPES, get_unattended_credentials

# pylint: enable=wrong-import-position,import-error


def main() -> int:
    """Run the browser consent flow, save token.json, and prove it refreshes unattended."""
    publish_config = load_config("config.yaml").publish
    secrets_path = publish_config.client_secrets_path
    if not secrets_path.exists():
        print(f"Missing {secrets_path}: download the Desktop app OAuth client JSON from Google Cloud first.")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), YOUTUBE_UPLOAD_SCOPES)
    # offline + consent guarantees a refresh token, even if this account granted access before.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        print("Google returned no refresh token; unattended uploads would fail. Try again.")
        return 1

    publish_config.token_path.write_text(creds.to_json(), encoding="utf-8")
    publish_config.token_path.chmod(0o600)

    try:
        get_unattended_credentials(publish_config.token_path)
    except PublishError as exc:
        print(f"Saved {publish_config.token_path}, but it failed the unattended check: {exc}")
        return 1
    print(f"Saved {publish_config.token_path}; unattended uploads are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
