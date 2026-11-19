# pylint: disable=missing-function-docstring,unbalanced-tuple-unpacking
import json

from horrorvibes import analytics


def test_parse_iso_duration():
    assert analytics.parse_iso_duration("PT1M5S") == 65
    assert analytics.parse_iso_duration("PT2H") == 7200
    assert analytics.parse_iso_duration("P1DT1S") == 86401
    assert analytics.parse_iso_duration(None) == 0
    assert analytics.parse_iso_duration("garbage") == 0


def _upload(video_id, views):
    return {"video_id": video_id, "title": video_id, "published_at": "2026-01-01", "public_views": views}


def _metrics(pct, subs=0):
    return {"averageViewPercentage": pct, "averageViewDuration": 30, "subscribersGained": subs}


def test_merge_snapshot_joins_metrics_and_catalog():
    snapshot = analytics.merge_snapshot(
        "UC1",
        [_upload("a", 10), _upload("b", 20)],
        {"a": _metrics(55)},
        {"a": {"style_pack": "woodcut_folk", "episode_format": "mixed", "quotes": [{}, {}]}},
    )
    a, b = snapshot["videos"]
    assert a["analytics"]["averageViewPercentage"] == 55
    assert a["pipeline"] == {"style_pack": "woodcut_folk", "episode_format": "mixed", "quote_count": 2}
    assert b["analytics"] is None and b["pipeline"] is None


def test_summarize_uses_medians_and_skips_missing_keys():
    videos = [
        {**_upload("a", 10), "analytics": _metrics(40, 1), "pipeline": {"style_pack": "x"}},
        {**_upload("b", 30), "analytics": _metrics(60, 2), "pipeline": {"style_pack": "x"}},
        {**_upload("c", 9000), "analytics": _metrics(90), "pipeline": {"style_pack": "x"}},
        {**_upload("d", 5), "analytics": None, "pipeline": None},
    ]
    (row,) = analytics.summarize(videos, "pipeline.style_pack")
    assert row == {
        "group": "x", "videos": 3, "median_views": 30, "median_pct_watched": 60,
        "median_view_sec": 30, "subs_gained": 3,
    }


def test_load_catalog_keeps_only_uploaded_entries(tmp_path):
    path = tmp_path / "video_catalog.jsonl"
    lines = [json.dumps({"youtube_video_id": None}), json.dumps({"youtube_video_id": "v1"}), "not json"]
    path.write_text("\n".join(lines) + "\n")
    assert list(analytics.load_catalog(path)) == ["v1"]


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _FakeReports:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return _Exec(self.pages[len(self.calls) - 1])


class _FakeAnalytics:
    def __init__(self, pages):
        self._reports = _FakeReports(pages)

    def reports(self):
        return self._reports


def test_fetch_video_metrics_batches_ids_through_a_filter():
    headers = [{"name": "video"}, {"name": "views"}]
    fake = _FakeAnalytics([
        {"columnHeaders": headers, "rows": [["v1", 5], ["v2", 4]]},
        {"columnHeaders": headers, "rows": [["v3", 1]]},
    ])
    from datetime import date  # pylint: disable=import-outside-toplevel

    ids = ["v1", "v2", "v3"]
    result = analytics.fetch_video_metrics(fake, ids, date(2025, 1, 1), date(2026, 1, 1), batch_size=2)
    assert result == {"v1": {"views": 5}, "v2": {"views": 4}, "v3": {"views": 1}}
    assert [call["filters"] for call in fake.reports().calls] == ["video==v1,v2", "video==v3"]
