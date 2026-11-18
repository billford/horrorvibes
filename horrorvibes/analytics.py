"""Pull the channel's own performance history so future runs can aim at what keeps viewers watching.

Two Google APIs are involved:

- YouTube Data API v3 lists every upload (title, length, publish time, public counts).
- YouTube Analytics API v2 adds what the public counts can't show: average % of each video watched,
  watch time and subscribers gained. It needs the ``yt-analytics.readonly`` scope.

Fetching is kept apart from the pure helpers (``merge_snapshot``, ``summarize``) so the latter can be
tested without network access.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ANALYTICS_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
    "likes,comments,shares,subscribersGained"
)
_DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_iso_duration(value: str | None) -> int:
    """Seconds in an ISO 8601 duration such as ``PT1M5S``. Unknown or empty -> 0."""
    match = _DURATION_RE.fullmatch(value or "")
    if not match:
        return 0
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_uploads(youtube: Any) -> tuple[str, list[dict]]:
    """Return (channel_id, every upload with its public stats), newest first."""
    channel = youtube.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids: list[str] = []
    page_token = None
    while True:
        page = (
            youtube.playlistItems()
            .list(part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=page_token)
            .execute()
        )
        video_ids += [item["contentDetails"]["videoId"] for item in page.get("items", [])]
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    uploads = []
    for batch in _chunks(video_ids, 50):
        response = (
            youtube.videos().list(part="snippet,contentDetails,statistics,status", id=",".join(batch)).execute()
        )
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            snippet = item["snippet"]
            uploads.append(
                {
                    "video_id": item["id"],
                    "title": snippet.get("title", ""),
                    "published_at": snippet.get("publishedAt"),
                    "tags": snippet.get("tags", []),
                    "duration_sec": parse_iso_duration(item.get("contentDetails", {}).get("duration")),
                    "privacy": item.get("status", {}).get("privacyStatus"),
                    "public_views": int(stats.get("viewCount", 0)),
                    "public_likes": int(stats.get("likeCount", 0)),
                    "public_comments": int(stats.get("commentCount", 0)),
                }
            )
    uploads.sort(key=lambda upload: upload["published_at"] or "", reverse=True)
    return channel["id"], uploads


def fetch_video_metrics(analytics: Any, start: date, end: date, page_size: int = 200) -> dict[str, dict]:
    """Lifetime-to-date Analytics metrics per video, keyed by video id."""
    metrics: dict[str, dict] = {}
    start_index = 1
    while True:
        report = (
            analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                metrics=ANALYTICS_METRICS,
                dimensions="video",
                sort="-views",
                maxResults=page_size,
                startIndex=start_index,
            )
            .execute()
        )
        names = [header["name"] for header in report.get("columnHeaders", [])]
        rows = report.get("rows") or []
        for row in rows:
            record = dict(zip(names, row))
            metrics[record.pop("video")] = record
        if len(rows) < page_size:
            return metrics
        start_index += page_size


def fetch_traffic_sources(analytics: Any, start: date, end: date) -> list[dict]:
    """Channel-wide views by where they came from (Shorts feed, search, suggested...)."""
    report = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views,estimatedMinutesWatched",
            dimensions="insightTrafficSourceType",
            sort="-views",
        )
        .execute()
    )
    names = [header["name"] for header in report.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in report.get("rows") or []]


def load_catalog(catalog_path: Path) -> dict[str, dict]:
    """This pipeline's own catalog entries keyed by YouTube id (entries never uploaded are skipped)."""
    if not catalog_path.exists():
        return {}
    entries = {}
    for line in catalog_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("youtube_video_id"):
            entries[entry["youtube_video_id"]] = entry
    return entries


def merge_snapshot(
    channel_id: str,
    uploads: list[dict],
    metrics: dict[str, dict],
    catalog: dict[str, dict],
    traffic_sources: list[dict] | None = None,
    fetched_at: datetime | None = None,
) -> dict:
    """Join public stats, Analytics metrics and our catalog into one snapshot record."""
    videos = []
    for upload in uploads:
        video = dict(upload)
        video["analytics"] = metrics.get(upload["video_id"])
        entry = catalog.get(upload["video_id"])
        video["pipeline"] = (
            {
                "style_pack": entry.get("style_pack"),
                "episode_format": entry.get("episode_format"),
                "quote_count": len(entry.get("quotes") or []),
            }
            if entry
            else None
        )
        videos.append(video)
    return {
        "fetched_at": (fetched_at or datetime.now(timezone.utc)).isoformat(),
        "channel_id": channel_id,
        "traffic_sources": traffic_sources or [],
        "videos": videos,
    }


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def summarize(videos: list[dict], key: str) -> list[dict]:
    """Per-group medians of views, % watched and subscribers gained, biggest groups first.

    ``key`` is a dotted path into each video, e.g. ``"pipeline.style_pack"``. Videos where the key is
    missing are left out. Medians, not means, because one viral video otherwise swamps a group.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for video in videos:
        value: Any = video
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None:
            groups[str(value)].append(video)

    rows = []
    for name, members in groups.items():
        stats = [member["analytics"] for member in members if member.get("analytics")]
        rows.append(
            {
                "group": name,
                "videos": len(members),
                "median_views": _median([member["public_views"] for member in members]),
                "median_pct_watched": _median([s["averageViewPercentage"] for s in stats]),
                "median_view_sec": _median([s["averageViewDuration"] for s in stats]),
                "subs_gained": sum(s["subscribersGained"] for s in stats),
            }
        )
    rows.sort(key=lambda row: row["videos"], reverse=True)
    return rows


def write_snapshot(snapshot: dict, path: Path) -> None:
    """Overwrite the snapshot file (it is a point-in-time pull, not a history)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    tmp.replace(path)
