# ABOUTME: Unit tests for the accessibility outline that `read_page` and `find` return.
# ABOUTME: The fixtures are an AX tree and a DOM snapshot captured from a real Steel session (tests/fixtures/form_page.html).

import json
from pathlib import Path

import pytest

from steel_browser_toolset.tree import MAX_OUTPUT_CHARS, find_matches, render_outline, viewport_rects

FIXTURES = Path(__file__).parent / "fixtures"
VIEWPORT = (1280, 720)


@pytest.fixture
def page():
    ax = json.loads((FIXTURES / "form_page_ax.json").read_text())["nodes"]
    rects = viewport_rects(json.loads((FIXTURES / "form_page_snapshot.json").read_text()))
    refs: dict[int, str] = {}
    ref_for = lambda backend_id: refs.setdefault(backend_id, f"ref_{len(refs) + 1}")
    return ax, rects, ref_for, refs


def test_default_outline_shows_viewport_elements_with_refs(page):
    ax, rects, ref_for, _ = page
    outline = render_outline(ax, rects, VIEWPORT, ref_for)
    assert '- heading "Place an order"' in outline and "level=1" in outline
    assert 'textbox "Full name"' in outline and 'value="Ada"' in outline
    assert 'combobox "Grade"' in outline and 'value="A572"' in outline
    assert 'checkbox "Express shipping"' in outline and "checked" in outline
    assert 'button "Submit order"' in outline and "disabled" in outline
    # The footer link is 2,000 px below the viewport, and the hidden button is not rendered.
    assert "Terms of service" not in outline
    assert "Hidden button" not in outline
    # Label text and link text are the names of their elements, so they are not repeated as text lines.
    assert 'text "Full name' not in outline
    assert 'text "Home"' not in outline


def test_interactive_filter_lists_only_controls_including_off_viewport(page):
    ax, rects, ref_for, _ = page
    outline = render_outline(ax, rects, VIEWPORT, ref_for, filter="interactive")
    lines = outline.splitlines()
    assert all(line.startswith("- ") for line in lines)
    assert any('link "Terms of service"' in line for line in lines)
    assert not any("heading" in line or "paragraph" in line for line in lines)


def test_all_filter_includes_off_viewport_elements(page):
    ax, rects, ref_for, _ = page
    assert 'link "Terms of service"' in render_outline(ax, rects, VIEWPORT, ref_for, filter="all")


def test_refs_are_stable_across_reads(page):
    ax, rects, ref_for, refs = page
    first = render_outline(ax, rects, VIEWPORT, ref_for)
    count = len(refs)
    assert render_outline(ax, rects, VIEWPORT, ref_for) == first
    assert len(refs) == count


def test_depth_limits_levels(page):
    ax, rects, ref_for, _ = page
    outline = render_outline(ax, rects, VIEWPORT, ref_for, depth=2)
    assert "form" in outline
    assert "textbox" not in outline


def test_subtree_from_ref_and_unknown_root(page):
    ax, rects, ref_for, _ = page
    form_backend_id = next(node["backendDOMNodeId"] for node in ax if node["role"]["value"] == "form")
    outline = render_outline(ax, rects, VIEWPORT, ref_for, root_backend_id=form_backend_id)
    assert outline.splitlines()[0].startswith("- form")
    assert "navigation" not in outline
    assert render_outline(ax, rects, VIEWPORT, ref_for, root_backend_id=999_999) is None


def test_output_is_cut_at_the_limit(page):
    _, rects, ref_for, _ = page
    many = []
    for index in range(4_000):
        many.append(
            {
                "nodeId": f"n{index}",
                "ignored": False,
                "role": {"value": "button"},
                "name": {"value": f"Button number {index}"},
                "backendDOMNodeId": 10_000 + index,
                "childIds": [],
            }
        )
    many_rects = {10_000 + index: rects[next(iter(rects))] for index in range(4_000)}
    outline = render_outline(many, many_rects, VIEWPORT, ref_for)
    assert len(outline) <= MAX_OUTPUT_CHARS + 200
    assert outline.endswith("or a smaller `depth`.]")


@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("search bar", 'searchbox "Search catalog"'),
        ("add to cart button", 'button "Add to cart"'),
        ("terms", 'link "Terms of service"'),
        ("grade dropdown", 'combobox "Grade"'),
        ("express shipping checkbox", 'checkbox "Express shipping"'),
    ],
)
def test_find_ranks_the_best_match_first(page, query, first):
    ax, rects, ref_for, _ = page
    result = find_matches(ax, rects, query, ref_for)
    assert result.splitlines()[0].startswith(f"- {first} [ref_")


def test_find_without_matches(page):
    ax, rects, ref_for, _ = page
    assert find_matches(ax, rects, "zzz", ref_for) == "No matches."
    assert find_matches(ax, rects, "the of", ref_for).startswith("No matches")


def test_find_matches_text_and_points_the_ref_at_its_element(page):
    ax, rects, ref_for, refs = page
    result = find_matches(ax, rects, "order steel beams", ref_for)
    first = result.splitlines()[0]
    assert first.startswith('- text "Fill in the form to order steel beams." [ref_')
    paragraph = next(node["backendDOMNodeId"] for node in ax if node["role"]["value"] == "paragraph")
    assert first.endswith(f"[{refs[paragraph]}]")


def test_find_lists_an_element_once_when_its_name_and_text_match(page):
    ax, rects, ref_for, _ = page
    lines = find_matches(ax, rects, "add to cart", ref_for).splitlines()
    assert lines[0].startswith('- button "Add to cart"')
    assert not any(line.startswith('- text "Add to cart"') for line in lines)


def test_text_runs_keep_more_characters_than_names(page):
    _, rects, ref_for, _ = page
    long_text = "steel " * 100
    nodes = [
        {
            "nodeId": "1",
            "ignored": False,
            "role": {"value": "button"},
            "name": {"value": long_text},
            "backendDOMNodeId": 1,
            "childIds": ["2"],
        },
        {
            "nodeId": "2",
            "ignored": False,
            "role": {"value": "paragraph"},
            "name": {"value": ""},
            "backendDOMNodeId": 2,
            "parentId": "1",
            "childIds": ["3"],
        },
        {
            "nodeId": "3",
            "ignored": False,
            "role": {"value": "StaticText"},
            "name": {"value": long_text},
            "backendDOMNodeId": 3,
            "parentId": "2",
            "childIds": [],
        },
    ]
    box = rects[next(iter(rects))]
    lines = render_outline(nodes, {1: box, 2: box, 3: box}, VIEWPORT, ref_for, filter="all").splitlines()
    button, text_line = lines[0], lines[-1]
    assert len(button) < 200
    assert text_line.strip().startswith('- text "') and len(text_line) > 550
