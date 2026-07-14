"""Per-run YouTube title/description/hashtags, built from the quotes
actually in that video rather than a fixed generic blurb -- so uploads
are properly described and discoverable instead of all sharing the same
"Haunting Horror Movie Quotes" title regardless of content.
"""

from __future__ import annotations

import re

from horrorvibes.textutil import split_quote_and_title

_MAX_TITLE_LEN = 100
_GENERIC_TAGS = ["horror", "horrormoviequotes", "scarymovies", "horrorshorts", "shorts"]


def featured_movies(quotes: list[str]) -> list[str]:
    """Deduplicated movie titles in first-appearance order, dropping any
    quote whose title couldn't be parsed out."""
    movies: list[str] = []
    for quote in quotes:
        _, movie = split_quote_and_title(quote)
        if movie != "Unknown" and movie not in movies:
            movies.append(movie)
    return movies


def _hashtag_safe(movie_title: str) -> str:
    """"A Nightmare on Elm Street (1984)" -> "ANightmareOnElmStreet"."""
    without_year = re.sub(r"\(.*?\)", "", movie_title)
    alnum_only = re.sub(r"[^A-Za-z0-9 ]", "", without_year)
    return "".join(word.capitalize() for word in alnum_only.split())


def build_video_hashtags(quotes: list[str], max_tags: int = 15) -> list[str]:
    """Generic horror tags plus one per featured movie, deduplicated,
    capped at ``max_tags`` (YouTube tags have a combined length limit)."""
    tags = list(_GENERIC_TAGS)
    lower_tags = {tag.lower() for tag in tags}
    for movie in featured_movies(quotes):
        tag = _hashtag_safe(movie)
        if tag and tag.lower() not in lower_tags:
            tags.append(tag)
            lower_tags.add(tag.lower())
    return tags[:max_tags]


def build_video_title(quotes: list[str], max_movies: int = 3) -> str:
    """A content-specific title naming a few of the featured films,
    truncated to YouTube's title length limit."""
    base = f"{len(quotes)} Bone-Chilling Horror Movie Quotes"
    movies = featured_movies(quotes)
    if not movies:
        return base

    named = movies[:max_movies]
    suffix = " | " + ", ".join(named) + (" & More" if len(movies) > max_movies else "")
    title = base + suffix
    if len(title) > _MAX_TITLE_LEN:
        title = title[: _MAX_TITLE_LEN - 1].rstrip() + "…"
    return title


def build_video_description(quotes: list[str]) -> str:
    """Lists every quote and its film -- an actually useful, searchable
    description instead of generic boilerplate -- plus hashtags."""
    lines = ["A collection of the most spine-chilling quotes from horror cinema:", ""]
    for i, quote in enumerate(quotes, start=1):
        quote_text, movie = split_quote_and_title(quote)
        lines.append(f"{i}. {quote_text} — {movie}")
    lines.append("")
    lines.append("Which one's your favorite? Let us know in the comments.")
    lines.append("")
    lines.append(" ".join(f"#{tag}" for tag in build_video_hashtags(quotes)))
    return "\n".join(lines)
