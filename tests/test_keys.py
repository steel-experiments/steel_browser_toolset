# ABOUTME: Unit tests for the conversion of Claude key names and chords into Playwright key names.
# ABOUTME: They need no browser and no network.

import pytest

from steel_browser_toolset.keys import UnknownKeyError, chord_string, parse_chord, parse_sequence


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Return", ["Enter"]),
        ("BackSpace", ["Backspace"]),
        ("Page_Up", ["PageUp"]),
        ("a", ["a"]),
        ("ctrl+a", ["Control", "a"]),
        ("cmd+shift+p", ["Control", "Shift", "p"]),
        ("alt+F4", ["Alt", "F4"]),
        ("ctrl++", ["Control", "+"]),
        ("+", ["+"]),
        ("space", ["Space"]),
        ("Up", ["ArrowUp"]),
    ],
)
def test_parse_chord(text, expected):
    assert parse_chord(text) == expected


def test_parse_sequence_splits_on_spaces():
    assert parse_sequence("Backspace Backspace Delete") == [["Backspace"], ["Backspace"], ["Delete"]]


def test_chord_string_joins_for_playwright():
    assert chord_string(parse_chord("ctrl+shift+t")) == "Control+Shift+t"


@pytest.mark.parametrize("text", ["", "   ", "ctrl+", "Hyper", "ctrl+nope"])
def test_unknown_or_malformed_keys_raise(text):
    with pytest.raises(UnknownKeyError):
        parse_sequence(text)
