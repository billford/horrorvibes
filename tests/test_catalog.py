# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import json
from datetime import datetime
from pathlib import Path

from horrorvibes.catalog import append_entry, build_catalog_entry


def test_build_catalog_entry_splits_quotes_into_text_and_movie():
    entry = build_catalog_entry(
        Path("output/video.mp4"),
        ["'He came home.' - Halloween (1978)", "No separator here"],
        youtube_video_id="abc123",
        timestamp=datetime(2026, 7, 14, 18, 0, 0),
    )

    assert entry["video_path"] == "output/video.mp4"
    assert entry["youtube_video_id"] == "abc123"
    assert entry["timestamp"] == "2026-07-14T18:00:00"
    assert entry["quotes"] == [
        {"quote": "'He came home.'", "movie": "Halloween (1978)"},
        {"quote": "No separator here", "movie": "Unknown"},
    ]


def test_append_entry_is_additive_and_atomic(tmp_path):
    catalog_path = tmp_path / "sub" / "video_catalog.jsonl"
    entry1 = build_catalog_entry(Path("v1.mp4"), ["'A.' - M1"], None, datetime(2026, 1, 1))
    entry2 = build_catalog_entry(Path("v2.mp4"), ["'B.' - M2"], "xyz", datetime(2026, 1, 2))

    append_entry(catalog_path, entry1)
    append_entry(catalog_path, entry2)

    lines = catalog_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["video_path"] == "v1.mp4"
    assert json.loads(lines[1])["video_path"] == "v2.mp4"
    assert list(catalog_path.parent.glob(".video_catalog_*.tmp")) == []


def test_append_entry_creates_parent_directories(tmp_path):
    catalog_path = tmp_path / "deep" / "nested" / "video_catalog.jsonl"
    append_entry(catalog_path, build_catalog_entry(Path("v.mp4"), ["'A.' - M"], None, datetime(2026, 1, 1)))

    assert catalog_path.exists()
