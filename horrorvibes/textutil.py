"""Small pure text-handling helpers shared by imagegen and compositor."""

from __future__ import annotations

import re


def split_quote_and_title(raw_quote: str) -> tuple[str, str]:
    """Split "'QUOTE' - MOVIE TITLE (YEAR)" into (quote_text, movie_title).

    Falls back to "Unknown" for the title if the separator isn't present,
    and strips a leading enumeration marker like "1. " or "2) " some chat
    responses prepend to each line.
    """
    parts = raw_quote.split(" - ")
    quote_text = parts[0].strip()
    movie_title = parts[1].strip() if len(parts) > 1 else "Unknown"
    quote_text = re.sub(r"^\d+[.)]\s*", "", quote_text)
    return quote_text, movie_title


def wrap_quote_lines(quote_text: str, max_chars_per_line: int) -> list[str]:
    """Greedily wrap ``quote_text`` into lines no longer than max_chars_per_line."""
    words = quote_text.split()
    lines: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join(current + [word])
        if len(candidate) <= max_chars_per_line:
            current.append(word)
        elif current:
            lines.append(" ".join(current))
            current = [word]
        else:
            lines.append(word)

    if current:
        lines.append(" ".join(current))

    return lines
