"""Unattended-safe YouTube upload.

Unlike the original script, this module NEVER falls back to
``InstalledAppFlow.run_local_server()`` (which opens a browser) -- an
unattended launchd run has no browser to open. If the stored refresh
token is missing or refresh fails, this raises PublishError so the
orchestrator can log it and skip the upload rather than hang.

The one-time interactive OAuth grant that produces the initial token
file must be done manually, once, before enabling the launchd job (see
README).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

from horrorvibes.config import PublishConfig
from horrorvibes.exceptions import PublishError

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def build_video_metadata(
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy_status: str,
) -> dict[str, Any]:
    """Pure construction of the YouTube API request body."""
    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }


def get_unattended_credentials(
    token_path: Path,
    scopes: list[str] | None = None,
    credentials_factory: Callable[[dict, list[str]], Credentials] = Credentials.from_authorized_user_info,
    request_factory: Callable[[], Any] = Request,
) -> Credentials:
    """Load stored credentials and refresh them if needed. Never opens a browser.

    Raises PublishError (not sys.exit) if there is no usable token -- the
    caller decides whether to skip the upload or abort the run.
    """
    scopes = scopes or YOUTUBE_UPLOAD_SCOPES

    if not token_path.exists():
        raise PublishError(
            f"No token file at {token_path}. Run the one-time interactive OAuth "
            "grant manually before enabling unattended uploads (see README)."
        )

    try:
        token_data = json.loads(token_path.read_text(encoding="utf-8"))
        creds = credentials_factory(token_data, scopes)
    except Exception as exc:
        raise PublishError(f"Failed to load credentials from {token_path}: {exc}") from exc

    if creds.valid:
        return creds

    if not (creds.expired and creds.refresh_token):
        raise PublishError(
            "Stored credentials are invalid and carry no refresh token; "
            "re-run the interactive OAuth grant manually."
        )

    try:
        creds.refresh(request_factory())
    except Exception as exc:
        raise PublishError(f"Credential refresh failed: {exc}") from exc

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload_video(
    video_path: Path, metadata: dict[str, Any], youtube_service: Any, media_factory: Callable[..., Any]
) -> str:
    """Upload ``video_path`` using an already-built YouTube API service object."""
    media = media_factory(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube_service.videos().insert(part="snippet,status", body=metadata, media_body=media)
    try:
        response = request.execute()
    except HttpError as exc:
        raise PublishError(f"YouTube API error: {exc}") from exc
    video_id = response["id"]
    logger.info("Uploaded video %s: https://www.youtube.com/watch?v=%s", video_path, video_id)
    return video_id


def _build_youtube_service(creds: Credentials) -> Any:
    from googleapiclient.discovery import build  # pylint: disable=import-outside-toplevel

    return build("youtube", "v3", credentials=creds)


def publish(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    publish_config: PublishConfig,
    youtube_service_factory: Callable[[Credentials], Any] | None = None,
    media_factory: Callable[..., Any] | None = None,
    credentials_factory: Callable[[dict, list[str]], Credentials] = Credentials.from_authorized_user_info,
    request_factory: Callable[[], Any] = Request,
) -> str:
    """End-to-end publish: unattended credential refresh + upload."""
    if media_factory is None:
        from googleapiclient.http import MediaFileUpload  # pylint: disable=import-outside-toplevel

        media_factory = MediaFileUpload

    if youtube_service_factory is None:
        youtube_service_factory = _build_youtube_service

    creds = get_unattended_credentials(
        publish_config.token_path, credentials_factory=credentials_factory, request_factory=request_factory
    )
    service = youtube_service_factory(creds)
    metadata = build_video_metadata(
        title, description, tags, publish_config.category_id, publish_config.privacy_status
    )
    return upload_video(video_path, metadata, service, media_factory)
