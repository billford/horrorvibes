# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import httplib2
import pytest
from googleapiclient.errors import HttpError

from horrorvibes.exceptions import PublishError
from horrorvibes.publish import (
    build_video_metadata,
    get_unattended_credentials,
    publish,
    upload_video,
)


class FakeCreds:
    def __init__(self, valid, expired=False, refresh_token=None, refresh_raises=None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._refresh_raises = refresh_raises
        self.refreshed = False

    def refresh(self, request):
        if self._refresh_raises:
            raise self._refresh_raises
        self.refreshed = True
        self.valid = True

    def to_json(self):
        return '{"token": "refreshed"}'


def test_build_video_metadata_shape():
    metadata = build_video_metadata("title", "desc", ["a", "b"], "17", "unlisted")
    assert metadata["snippet"]["title"] == "title"
    assert metadata["snippet"]["tags"] == ["a", "b"]
    assert metadata["status"]["privacyStatus"] == "unlisted"
    assert metadata["status"]["selfDeclaredMadeForKids"] is False


def test_get_unattended_credentials_raises_when_no_token_file(tmp_path):
    with pytest.raises(PublishError, match="one-time interactive OAuth"):
        get_unattended_credentials(tmp_path / "token.json")


def test_get_unattended_credentials_returns_valid_creds_unchanged(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    creds = FakeCreds(valid=True)

    result = get_unattended_credentials(
        token_path, credentials_factory=lambda data, scopes: creds, request_factory=lambda: object()
    )

    assert result is creds
    assert creds.refreshed is False


def test_get_unattended_credentials_refreshes_expired_token(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    creds = FakeCreds(valid=False, expired=True, refresh_token="rt")

    result = get_unattended_credentials(
        token_path, credentials_factory=lambda data, scopes: creds, request_factory=lambda: object()
    )

    assert result.refreshed is True
    assert token_path.read_text(encoding="utf-8") == '{"token": "refreshed"}'


def test_get_unattended_credentials_raises_without_refresh_token(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    creds = FakeCreds(valid=False, expired=True, refresh_token=None)

    with pytest.raises(PublishError, match="refresh token"):
        get_unattended_credentials(
            token_path, credentials_factory=lambda data, scopes: creds, request_factory=lambda: object()
        )


def test_get_unattended_credentials_raises_when_refresh_fails(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    creds = FakeCreds(valid=False, expired=True, refresh_token="rt", refresh_raises=RuntimeError("revoked"))

    with pytest.raises(PublishError, match="Credential refresh failed"):
        get_unattended_credentials(
            token_path, credentials_factory=lambda data, scopes: creds, request_factory=lambda: object()
        )


def test_get_unattended_credentials_never_triggers_a_browser_flow(tmp_path, monkeypatch):
    # If this ever called InstalledAppFlow.run_local_server, it would try to
    # bind a local port / open a browser -- assert that code path doesn't exist
    # by making sure no such import happens; a NameError/ImportError would
    # surface as a test failure rather than a hang.
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    creds = FakeCreds(valid=True)

    get_unattended_credentials(
        token_path, credentials_factory=lambda data, scopes: creds, request_factory=lambda: object()
    )
    import horrorvibes.publish as publish_module

    assert "InstalledAppFlow" not in dir(publish_module)


class FakeInsertRequest:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def execute(self):
        if self._error:
            raise self._error
        return self._response


class FakeVideosResource:
    def __init__(self, request):
        self._request = request

    def insert(self, part, body, media_body):  # pylint: disable=unused-argument
        return self._request


class FakeYouTubeService:
    def __init__(self, request):
        self._resource = FakeVideosResource(request)

    def videos(self):
        return self._resource


def test_upload_video_returns_video_id_on_success(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"data")
    service = FakeYouTubeService(FakeInsertRequest(response={"id": "abc123"}))
    media_calls = []

    def media_factory(path, mimetype, resumable):
        media_calls.append((path, mimetype, resumable))
        return object()

    video_id = upload_video(video_path, {"snippet": {}}, service, media_factory)

    assert video_id == "abc123"
    assert media_calls == [(str(video_path), "video/mp4", True)]


def test_upload_video_wraps_http_error_as_publish_error(tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"data")
    resp = httplib2.Response({"status": 403})
    http_error = HttpError(resp, b'{"error": "forbidden"}')
    service = FakeYouTubeService(FakeInsertRequest(error=http_error))

    with pytest.raises(PublishError):
        upload_video(video_path, {"snippet": {}}, service, lambda *a, **k: object())


def test_publish_wires_credentials_service_and_upload_together(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"data")

    from horrorvibes.config import load_config

    config = load_config(tmp_path / "missing.yaml")
    import dataclasses

    publish_config = dataclasses.replace(config.publish, token_path=token_path, privacy_status="unlisted")

    creds = FakeCreds(valid=True)
    service = FakeYouTubeService(FakeInsertRequest(response={"id": "xyz"}))

    video_id = publish(
        video_path,
        "title",
        "desc",
        ["tag"],
        publish_config,
        youtube_service_factory=lambda c: service,
        media_factory=lambda *a, **k: object(),
        credentials_factory=lambda data, scopes: creds,
        request_factory=lambda: object(),
    )

    assert video_id == "xyz"
