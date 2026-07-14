"""A durable, append-only record of every quote that's appeared in a
generated video -- so a quote (or movie) can be searched for later to
find which video it was used in, long after the run that made it.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from horrorvibes.textutil import split_quote_and_title


def build_catalog_entry(
    video_path: Path,
    quotes: list[str],
    youtube_video_id: str | None,
    timestamp: datetime,
) -> dict:
    """Pure construction of one catalog record -- no I/O."""
    return {
        "timestamp": timestamp.isoformat(),
        "video_path": str(video_path),
        "youtube_video_id": youtube_video_id,
        "quotes": [
            {"quote": quote_text, "movie": movie}
            for quote_text, movie in (split_quote_and_title(quote) for quote in quotes)
        ],
    }


def append_entry(catalog_path: Path, entry: dict) -> None:
    """Atomically append one JSON-lines record (write-temp + rename),
    matching quotes.append_quote_history's pattern so a crash mid-write
    can't corrupt the catalog."""
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    existing = catalog_path.read_text(encoding="utf-8") if catalog_path.exists() else ""
    new_content = existing + json.dumps(entry) + "\n"

    fd, tmp_name = tempfile.mkstemp(dir=str(catalog_path.parent), prefix=".video_catalog_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(new_content)
        os.replace(tmp_name, catalog_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
