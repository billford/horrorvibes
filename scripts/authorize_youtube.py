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

    # readonly is only here so this script can name the channel the token is bound to -- uploads
    # themselves need nothing beyond YOUTUBE_UPLOAD_SCOPES.
    scopes = YOUTUBE_UPLOAD_SCOPES + ["https://www.googleapis.com/auth/youtube.readonly"]
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes)
    print(
        "In the browser, pick the CHANNEL you want uploads to go to (a Brand Account such as a\n"
        "second channel is listed separately from the Google account that manages it)."
    )
    # select_account forces the chooser even when already signed in -- without it Google silently
    # reuses the default channel, which is how an upload once landed on the wrong one.
    # offline + consent guarantees a refresh token, even if this account granted access before.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="select_account consent")
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
    print(f"Uploads will go to: {_channel_title(creds)}")
    return 0


def _channel_title(creds) -> str:
    """Name the channel this token uploads to, so a wrong pick is caught before a run uses it."""
    try:
        from googleapiclient.discovery import build  # pylint: disable=import-outside-toplevel

        service = build("youtube", "v3", credentials=creds)
        request = service.channels().list(part="snippet", mine=True)  # pylint: disable=no-member
        items = request.execute().get("items") or []
        return items[0]["snippet"]["title"] if items else "unknown (no channel returned)"
    except Exception as exc:  # pylint: disable=broad-except
        return f"unknown (channel lookup failed: {exc})"


if __name__ == "__main__":
    sys.exit(main())
