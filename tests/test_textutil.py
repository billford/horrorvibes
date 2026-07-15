# pylint: disable=missing-function-docstring,missing-class-docstring,redefined-outer-name,unused-argument,unnecessary-lambda,import-outside-toplevel,use-implicit-booleaness-not-comparison

import pytest

from horrorvibes.textutil import split_quote_and_title, wrap_quote_lines


@pytest.mark.parametrize(
    "raw,expected_quote,expected_title",
    [
        ("'They're here.' - Poltergeist (1982)", "'They're here.'", "Poltergeist (1982)"),
        ("1. 'Come with me.' - The Terminator (1984)", "'Come with me.'", "The Terminator (1984)"),
        ("No separator here", "No separator here", "Unknown"),
        ("2) 'Redrum.' - The Shining (1980)", "'Redrum.'", "The Shining (1980)"),
    ],
)
def test_split_quote_and_title(raw, expected_quote, expected_title):
    quote_text, movie_title = split_quote_and_title(raw)
    assert quote_text == expected_quote
    assert movie_title == expected_title


def test_wrap_quote_lines_respects_max_chars():
    lines = wrap_quote_lines("this is a fairly long horror quote to wrap", max_chars_per_line=15)
    assert all(len(line) <= 15 for line in lines)
    assert " ".join(lines).split() == "this is a fairly long horror quote to wrap".split()


def test_wrap_quote_lines_single_long_word_becomes_its_own_line():
    lines = wrap_quote_lines("supercalifragilisticexpialidocious", max_chars_per_line=10)
    assert lines == ["supercalifragilisticexpialidocious"]


def test_wrap_quote_lines_empty_string():
    assert wrap_quote_lines("", max_chars_per_line=10) == []
