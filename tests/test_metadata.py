# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

from horrorvibes.metadata import (
    build_video_description,
    build_video_hashtags,
    build_video_title,
    featured_movies,
)

QUOTES = [
    "'He came home.' - Halloween (1978)",
    "'Whatever you do, don't fall asleep.' - A Nightmare on Elm Street (1984)",
    "'I'm your boyfriend now, Nancy.' - A Nightmare on Elm Street (1984)",
    "No separator here",
]


def test_featured_movies_dedupes_and_drops_unknown():
    assert featured_movies(QUOTES) == ["Halloween (1978)", "A Nightmare on Elm Street (1984)"]


def test_build_video_title_names_featured_movies():
    title = build_video_title(QUOTES)
    assert title.startswith("4 Bone-Chilling Horror Quotes")
    assert "Halloween (1978)" in title
    assert "A Nightmare on Elm Street (1984)" in title
    assert "& More" not in title  # only 2 movies total, both fit under max_movies=3


def test_build_video_title_adds_and_more_when_movies_exceed_max():
    quotes = [f"'Q{i}.' - Movie{i}" for i in range(6)]
    title = build_video_title(quotes, max_movies=3)
    assert "& More" in title


def test_build_video_title_falls_back_to_base_when_no_movies_parsed():
    assert build_video_title(["No separator here"]) == "1 Bone-Chilling Horror Quotes"


def test_build_video_title_truncates_to_youtube_limit():
    quotes = [f"'Q{i}.' - Some Extremely Long Movie Title Number {i} Indeed" for i in range(10)]
    title = build_video_title(quotes, max_movies=8)
    assert len(title) <= 100


def test_build_video_hashtags_includes_generic_and_movie_tags():
    tags = build_video_hashtags(QUOTES)
    assert "horror" in tags
    assert "Halloween" in tags
    assert "ANightmareOnElmStreet" in tags


def test_build_video_hashtags_deduplicates_case_insensitively():
    quotes = ["'A.' - Halloween", "'B.' - halloween"]
    tags = build_video_hashtags(quotes)
    assert tags.count("Halloween") + tags.count("halloween") == 1


def test_build_video_hashtags_respects_max_tags():
    quotes = [f"'Q{i}.' - Movie{i}" for i in range(30)]
    tags = build_video_hashtags(quotes, max_tags=10)
    assert len(tags) == 10


def test_build_video_description_lists_every_quote_and_movie():
    description = build_video_description(QUOTES)
    assert "1. 'He came home.' — Halloween (1978)" in description
    assert "2. 'Whatever you do, don't fall asleep.' — A Nightmare on Elm Street (1984)" in description
    assert "#horror" in description
