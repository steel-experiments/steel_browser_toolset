# ABOUTME: Renders a CDP accessibility tree as the text outline that `read_page` and `find` return to Claude.
# ABOUTME: Each element gets a `[ref_N]` tag, and a DOM snapshot tells which elements are in the viewport.

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_OUTPUT_CHARS = 50_000
MAX_NAME_CHARS = 150
# A text run is page content, so it gets more room than the name of an element.
MAX_TEXT_CHARS = 1_000
MAX_FIND_RESULTS = 20
DEFAULT_DEPTH = 15

INTERACTIVE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "radio",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
    "treeitem",
}
# Roles that only group other nodes. The outline shows their children in their place, unless they have a name.
TRANSPARENT_ROLES = {"generic", "none", "presentation", "LabelText", "LayoutTable", "LayoutTableRow", "LayoutTableCell"}
# Roles whose subtree only repeats what the outline already shows: the text inside an input.
SKIP_SUBTREE_UNDER = {"textbox", "searchbox"}
SHOWN_PROPERTIES = ("checked", "selected", "expanded", "pressed", "disabled", "required", "focused", "level")

# Words in a `find` query that point to roles, such as "bar" in "search bar". They also match an element with no name.
FIND_SYNONYMS = {
    "search": {"searchbox", "textbox", "combobox"},
    "field": {"textbox", "searchbox", "combobox", "spinbutton"},
    "input": {"textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio"},
    "box": {"textbox", "searchbox", "combobox", "checkbox"},
    "bar": {"searchbox", "textbox", "combobox"},
    "button": {"button"},
    "link": {"link"},
    "checkbox": {"checkbox", "switch"},
    "toggle": {"switch", "checkbox"},
    "dropdown": {"combobox", "listbox"},
    "select": {"combobox", "listbox"},
    "menu": {"menu", "menuitem", "combobox"},
    "tab": {"tab"},
    "heading": {"heading"},
    "title": {"heading"},
    "image": {"img", "image"},
}
STOP_WORDS = {"the", "a", "an", "to", "of", "for", "on", "in", "with", "and", "or", "that", "this", "is", "at"}

RefFor = Callable[[int], str]
"""Gives the `ref_N` of a DOM node, by its backend node id. The same node gets the same ref each time."""


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float


def viewport_rects(snapshot: dict[str, Any]) -> dict[int, Rect]:
    """The layout box of each rendered node of the main document, by backend node id, in viewport pixels."""
    documents = snapshot.get("documents") or []
    if not documents:
        return {}
    document = documents[0]
    backend_ids = document["nodes"]["backendNodeId"]
    scroll_x = document.get("scrollOffsetX", 0)
    scroll_y = document.get("scrollOffsetY", 0)
    rects: dict[int, Rect] = {}
    for node_index, (x, y, width, height) in zip(document["layout"]["nodeIndex"], document["layout"]["bounds"]):
        rects[backend_ids[node_index]] = Rect(x - scroll_x, y - scroll_y, width, height)
    return rects


def _value(field: dict[str, Any] | None) -> Any:
    return (field or {}).get("value")


def _clean(text: Any, limit: int = MAX_NAME_CHARS) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _properties(node: dict[str, Any]) -> dict[str, Any]:
    return {prop["name"]: _value(prop.get("value")) for prop in node.get("properties") or []}


def _role(node: dict[str, Any]) -> str:
    return str(_value(node.get("role")) or "")


def is_interactive(node: dict[str, Any]) -> bool:
    role = _role(node)
    if role == "RootWebArea":
        return False
    return role in INTERACTIVE_ROLES or _properties(node).get("focusable") is True


def describe(node: dict[str, Any], ref: str | None) -> str:
    """One outline entry, such as `textbox "Full name" [ref_4] value="Ada" required`."""
    role = _role(node)
    name = _clean(_value(node.get("name")), MAX_TEXT_CHARS if role == "StaticText" else MAX_NAME_CHARS)
    parts = [role if role != "StaticText" else "text"]
    if name:
        parts.append(f'"{name}"')
    if ref:
        parts.append(f"[{ref}]")
    text_value = _clean(_value(node.get("value")))
    if text_value and text_value != name:
        parts.append(f'value="{text_value}"')
    props = _properties(node)
    for key in SHOWN_PROPERTIES:
        value = props.get(key)
        if value is None or value is False or value == "false":
            continue
        parts.append(key if value is True or value == "true" else f"{key}={value}")
    return " ".join(parts)


class _Tree:
    def __init__(self, ax_nodes: list[dict[str, Any]]) -> None:
        self.by_id = {node["nodeId"]: node for node in ax_nodes}
        self.roots = [node for node in ax_nodes if node.get("parentId") not in self.by_id]

    def children(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        return [self.by_id[child] for child in node.get("childIds") or [] if child in self.by_id]

    def by_backend_id(self, backend_id: int) -> dict[str, Any] | None:
        for node in self.by_id.values():
            if node.get("backendDOMNodeId") == backend_id:
                return node
        return None


def _in_viewport(rect: Rect | None, viewport: tuple[int, int]) -> bool:
    if rect is None or rect.width <= 0 or rect.height <= 0:
        return False
    width, height = viewport
    return rect.x < width and rect.y < height and rect.x + rect.width > 0 and rect.y + rect.height > 0


def render_outline(
    ax_nodes: list[dict[str, Any]],
    rects: dict[int, Rect],
    viewport: tuple[int, int],
    ref_for: RefFor,
    *,
    filter: str | None = None,
    depth: int | None = None,
    root_backend_id: int | None = None,
) -> str | None:
    """The outline that `read_page` returns.

    `filter` is None for the elements in the viewport, "interactive" for interactive elements anywhere on the page,
    and "all" for every rendered element. Returns None when `root_backend_id` is not in the tree.
    """
    tree = _Tree(ax_nodes)
    if root_backend_id is not None:
        root = tree.by_backend_id(root_backend_id)
        if root is None:
            return None
        roots = [root]
    else:
        roots = tree.roots
    max_depth = DEFAULT_DEPTH if depth is None else depth
    lines: list[str] = []
    size = 0
    truncated = False

    def shown(node: dict[str, Any], parent_name: str) -> bool:
        role = _role(node)
        name = _clean(_value(node.get("name")))
        if node.get("ignored") or role in ("InlineTextBox", "LineBreak", "RootWebArea"):
            return False
        if role in TRANSPARENT_ROLES and not name:
            return False
        if role == "StaticText" and (not name or name == parent_name or filter == "interactive"):
            return False  # empty, or the name of the element that holds it, which the outline already shows
        backend_id = node.get("backendDOMNodeId")
        rect = rects.get(backend_id) if backend_id is not None else None
        if filter == "interactive":
            return is_interactive(node) and rect is not None and rect.width > 0 and rect.height > 0
        if filter == "all":
            return rect is not None
        return _in_viewport(rect, viewport)

    def visit(node: dict[str, Any], level: int, parent_name: str) -> None:
        nonlocal size, truncated
        if truncated or level >= max_depth:
            return
        role = _role(node)
        next_level, name_for_children = level, parent_name
        if shown(node, parent_name):
            backend_id = node.get("backendDOMNodeId")
            ref = ref_for(backend_id) if backend_id is not None and role != "StaticText" else None
            line = "  " * level + "- " + describe(node, ref)
            if size + len(line) + 1 > MAX_OUTPUT_CHARS:
                truncated = True
                return
            lines.append(line)
            size += len(line) + 1
            next_level, name_for_children = level + 1, _clean(_value(node.get("name")))
        if role in SKIP_SUBTREE_UNDER or role == "StaticText":
            return
        for child in tree.children(node):
            if role == "LabelText" and _role(child) == "StaticText":
                continue  # the label text is the name of the control in the label
            visit(child, next_level, name_for_children)

    for root in roots:
        visit(root, 0, "")
    if truncated:
        lines.append(f"[Output cut at {MAX_OUTPUT_CHARS} characters. Read a subtree with `ref`, or a smaller `depth`.]")
    if not lines:
        return "(No matching elements.)"
    return "\n".join(lines)


def _words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOP_WORDS]


def find_matches(ax_nodes: list[dict[str, Any]], rects: dict[int, Rect], query: str, ref_for: RefFor) -> str:
    """The `find` result: up to 20 rendered elements whose role, name, value or text match the words of `query`.

    A match on a text run is shown as that text, with the ref of the element that holds it.
    """
    words = _words(query)
    if not words:
        return "No matches: the query has no words to match."
    tree = _Tree(ax_nodes)
    best: dict[int, tuple[float, int, str]] = {}  # by the backend id of the element that the ref points to
    for order, node in enumerate(ax_nodes):
        role = _role(node)
        if node.get("ignored") or role in ("InlineTextBox", "RootWebArea") or node.get("backendDOMNodeId") is None:
            continue
        holder = _text_holder(tree, node) if role == "StaticText" else node
        if holder is None:
            continue
        backend_id = holder["backendDOMNodeId"]
        rect = rects.get(backend_id)
        if rect is None or rect.width <= 0 or rect.height <= 0:
            continue
        score = _score(words, role, _clean(_value(node.get("name"))), _clean(_value(node.get("value"))))
        if score == 0:
            continue
        if is_interactive(holder):
            score += 0.5
        if backend_id in best and best[backend_id][0] >= score:
            continue
        line = (
            describe(node, None) + f" [{ref_for(backend_id)}]"
            if role == "StaticText"
            else describe(node, ref_for(backend_id))
        )
        best[backend_id] = (score, order, line)
    if not best:
        return "No matches."
    ranked = sorted(best.values(), key=lambda item: (-item[0], item[1]))
    return "\n".join(f"- {line}" for _, _, line in ranked[:MAX_FIND_RESULTS])


def _text_holder(tree: _Tree, node: dict[str, Any]) -> dict[str, Any] | None:
    """The nearest element above a text run that a ref can point to."""
    parent = tree.by_id.get(node.get("parentId"))
    while parent is not None:
        if not parent.get("ignored") and parent.get("backendDOMNodeId") is not None and _role(parent) != "StaticText":
            return parent
        parent = tree.by_id.get(parent.get("parentId"))
    return None


def _score(words: list[str], role: str, name: str, value: str) -> float:
    name_words = set(_words(name))
    value_words = set(_words(value))
    score = 0.0
    for word in words:
        singular = word[:-1] if word.endswith("s") and len(word) > 3 else word
        if word in name_words or singular in name_words:
            score += 2
        elif len(singular) >= 3 and any(name_word.startswith(singular) for name_word in name_words):
            score += 1
        if word in value_words:
            score += 1
        if role in FIND_SYNONYMS.get(word, ()) or word == role.lower():
            score += 1.5
    return score
