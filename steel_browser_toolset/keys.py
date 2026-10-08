# ABOUTME: Converts the key names, chords and sequences that Claude sends into Playwright key names.
# ABOUTME: The `key` and `hold_key` members and the click `modifiers` use it.

from __future__ import annotations

# Steel browsers run on Linux, so "cmd" and the other Mac names map to Control. The model often sends Mac shortcuts.
MODIFIERS = {
    "ctrl": "Control",
    "control": "Control",
    "cmd": "Control",
    "command": "Control",
    "meta": "Meta",
    "super": "Meta",
    "win": "Meta",
    "windows": "Meta",
    "alt": "Alt",
    "option": "Alt",
    "opt": "Alt",
    "shift": "Shift",
}

NAMED_KEYS = {
    "return": "Enter",
    "enter": "Enter",
    "kp_enter": "Enter",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "Backspace",
    "back_space": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "space": "Space",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "page_up": "PageUp",
    "prior": "PageUp",
    "pagedown": "PageDown",
    "page_down": "PageDown",
    "next": "PageDown",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "capslock": "CapsLock",
    "caps_lock": "CapsLock",
    "contextmenu": "ContextMenu",
    "menu": "ContextMenu",
    "plus": "+",
    "minus": "-",
    **{f"f{n}": f"F{n}" for n in range(1, 13)},
}


class UnknownKeyError(ValueError):
    """A key name that the driver does not know."""


def key_name(name: str) -> str:
    """The Playwright name of one key, such as `Enter` for `Return`, or the character for a single character."""
    if len(name) == 1:
        return name
    lowered = name.lower()
    if lowered in MODIFIERS:
        return MODIFIERS[lowered]
    if lowered in NAMED_KEYS:
        return NAMED_KEYS[lowered]
    raise UnknownKeyError(f"unknown key: {name}")


def parse_chord(text: str) -> list[str]:
    """The keys of one chord, such as `ctrl+shift+p`, in press order. A `+` as the last key is written `ctrl++`."""
    if text == "+":
        return ["+"]
    if text.endswith("++"):
        return [*parse_chord(text[:-2]), "+"] if len(text) > 2 else ["+"]
    parts = text.split("+")
    if any(part == "" for part in parts):
        raise UnknownKeyError(f"malformed key chord: {text}")
    return [key_name(part) for part in parts]


def parse_sequence(text: str) -> list[list[str]]:
    """The chords of a space-separated sequence, such as `Backspace Backspace ctrl+a`. A lone space key is `space`."""
    chords = [parse_chord(part) for part in text.split()]
    if not chords:
        raise UnknownKeyError("no key given")
    return chords


def chord_string(keys: list[str]) -> str:
    """One chord in the form that Playwright `keyboard.press` reads, such as `Control+Shift+P`."""
    return "+".join(keys)
