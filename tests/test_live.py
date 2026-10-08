# ABOUTME: Live tests that send the toolset the tool calls a model sends, against a real Steel session.
# ABOUTME: Run them with `uv run pytest -m live`; they need STEEL_API_KEY and create one Steel session per module.

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from anthropic.tools.browser import BetaLocalFilePolicy
from anthropic.types.beta import BetaToolUseBlock
from PIL import Image
from steel import AsyncSteel

from steel_browser_toolset import SteelBrowser, allowed_hosts_policy

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

FIXTURES = Path(__file__).parent / "fixtures"

PLAYGROUND = """<!doctype html>
<title>Playground</title>
<h1>Playground</h1>
<p id="status">Nothing happened yet.</p>
<button onclick="document.getElementById('status').textContent = 'Clicked once.'">Press me</button>
<button ondblclick="document.getElementById('status').textContent = 'Clicked twice.'">Double me</button>
<button onclick="alert('Are you sure?')">Warn me</button>
<button onclick="console.log('hello from the page'); console.error('bad thing')">Log me</button>
<label>Free text <input id="free"></label>
<label>Attachment <input id="attachment" type="file"></label>
<a id="download" href="data:text/plain;base64,c3RlZWwgYmVhbXM=" download="beams.txt">Get beams</a>
<a id="escape" href="https://evil.example.net/">Leave</a>
<div style="height: 3000px"></div>
<p id="bottom">The bottom of the page</p>
"""


def tool_use(name: str, input: dict[str, Any]) -> BetaToolUseBlock:
    return BetaToolUseBlock(type="tool_use", id=f"toolu_{name}", name=name, input=input, toolset_name="browser")


def blocks(result: dict[str, Any]) -> list[dict[str, Any]]:
    content = result.get("content", "")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [dict(block) for block in content]


def text(result: dict[str, Any]) -> str:
    return "\n".join(str(block.get("text", "")) for block in blocks(result) if block["type"] == "text")


def state(result: dict[str, Any]) -> dict[str, Any]:
    found = [block for block in blocks(result) if block["type"] == "browser_state"]
    assert len(found) == 1, f"expected one browser_state block, got {blocks(result)}"
    return found[0]


def image(result: dict[str, Any]) -> Image.Image:
    found = [block for block in blocks(result) if block["type"] == "image"]
    assert len(found) == 1
    return Image.open(io.BytesIO(base64.b64decode(found[0]["source"]["data"])))


@pytest.fixture(scope="module")
def upload_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("uploads")
    (root / "order.csv").write_text("grade,count\nA572,12\n")
    return root


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(upload_root: Path):
    if not os.environ.get("STEEL_API_KEY"):
        pytest.fail("STEEL_API_KEY is not set: live tests need a Steel API key")
    driver = SteelBrowser(
        AsyncSteel(),
        url_policy=allowed_hosts_policy(["example.com"]),
        configs={
            "javascript_exec": {"enabled": True},
            "file_upload": {"enabled": True},
            "read_console": {"enabled": True},
            "read_network": {"enabled": True},
        },
        confirm=lambda _context: True,
        file_policy=BetaLocalFilePolicy(upload_roots=[upload_root]),
    )
    async with driver:
        yield driver


async def call(browser: SteelBrowser, name: str, **input: Any) -> dict[str, Any]:
    return await browser.tool_result(tool_use(name, input))


async def load_playground(browser: SteelBrowser) -> None:
    await browser.page().set_content(PLAYGROUND)


async def ref_of(browser: SteelBrowser, query: str) -> str:
    found = text(await call(browser, "find", query=query))
    return found.split("[", 1)[1].split("]", 1)[0]


async def page_value(browser: SteelBrowser, expression: str) -> Any:
    return await browser.page().evaluate(expression)


async def test_navigate_reports_the_page_and_the_tab(browser):
    result = await call(browser, "navigate", url="example.com")
    assert not result.get("is_error"), result
    assert text(result) == "Navigated to https://example.com/ — Example Domain (HTTP 200)"
    tabs = state(result)["tabs"]
    assert [tab["active"] for tab in tabs].count(True) == 1
    assert any(tab["url"] == "https://example.com/" for tab in tabs)


@pytest.mark.parametrize("url", ["https://evil.example.net/", "javascript:alert(1)", "file:///etc/passwd"])
async def test_navigate_refuses_addresses_outside_the_policy(browser, url):
    result = await call(browser, "navigate", url=url)
    assert result.get("is_error") is True
    assert text(result).startswith("blocked:")


async def test_back_and_reload(browser):
    await call(browser, "navigate", url="https://example.com/")
    await call(browser, "navigate", url="https://www.example.com/")
    back = await call(browser, "navigate", url="back")
    assert not back.get("is_error"), back
    assert "https://example.com/" in text(back)
    assert not (await call(browser, "navigate", url="reload")).get("is_error")


async def test_read_page_and_click_by_ref(browser):
    await load_playground(browser)
    outline = text(await call(browser, "read_page", filter="interactive"))
    line = next(line for line in outline.splitlines() if '"Press me"' in line)
    ref = line.split("[", 1)[1].split("]", 1)[0]
    result = await call(browser, "left_click", target={"type": "ref", "ref": ref})
    assert not result.get("is_error"), result
    assert await page_value(browser, "document.getElementById('status').textContent") == "Clicked once."


async def test_double_click_by_coordinate(browser):
    await load_playground(browser)
    box = await browser.page().locator("text=Double me").bounding_box()
    x, y = int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)
    result = await call(browser, "double_click", target={"type": "coordinate", "x": x, "y": y})
    assert not result.get("is_error"), result
    assert await page_value(browser, "document.getElementById('status').textContent") == "Clicked twice."


async def test_read_page_default_filter_hides_off_viewport_and_all_shows_it(browser):
    await load_playground(browser)
    assert "The bottom of the page" not in text(await call(browser, "read_page"))
    assert "The bottom of the page" in text(await call(browser, "read_page", filter="all"))


async def test_type_key_and_get_page_text(browser):
    await load_playground(browser)
    ref = await ref_of(browser, "free text field")
    await call(browser, "left_click", target={"type": "ref", "ref": ref})
    assert not (await call(browser, "type", text="steel beams")).get("is_error")
    assert await page_value(browser, "document.getElementById('free').value") == "steel beams"
    assert not (await call(browser, "key", text="ctrl+a Backspace")).get("is_error")
    assert await page_value(browser, "document.getElementById('free').value") == ""
    assert "Nothing happened yet." in text(await call(browser, "get_page_text"))


async def test_unknown_key_is_refused(browser):
    result = await call(browser, "key", text="Hyper")
    assert result.get("is_error") is True and "unknown key" in text(result)


async def test_form_input_sets_fields(browser):
    await browser.page().set_content((FIXTURES / "form_page.html").read_text())
    name = await ref_of(browser, "full name")
    grade = await ref_of(browser, "grade")
    shipping = await ref_of(browser, "express shipping checkbox")
    assert text(await call(browser, "form_input", target={"type": "ref", "ref": name}, value="Grace")).endswith(
        "\nValue set."
    )
    assert text(await call(browser, "form_input", target={"type": "ref", "ref": grade}, value="A36")).endswith(
        '\nSelected "A36".'
    )
    assert not (await call(browser, "form_input", target={"type": "ref", "ref": shipping}, value=False)).get("is_error")
    values = await page_value(
        browser,
        "[document.querySelector('[name=name]').value, document.querySelector('select').value,"
        " document.querySelector('[type=checkbox]').checked]",
    )
    assert values == ["Grace", "a36", False]
    refused = await call(browser, "form_input", target={"type": "ref", "ref": grade}, value="Z99")
    assert refused.get("is_error") is True and "No option" in text(refused)


async def test_stale_ref_after_navigation(browser):
    await load_playground(browser)
    ref = await ref_of(browser, "press me button")
    await call(browser, "navigate", url="https://example.com/")
    result = await call(browser, "left_click", target={"type": "ref", "ref": ref})
    assert result.get("is_error") is True and "stale ref" in text(result)


async def test_screenshot_and_zoom_sizes(browser):
    await load_playground(browser)
    assert image(await call(browser, "screenshot")).size == (1280, 720)
    zoomed = image(await call(browser, "zoom", region=[0, 0, 320, 180]))
    assert zoomed.size == (1280, 720)
    refused = await call(browser, "zoom", region=[0, 0, 2000, 100])
    assert refused.get("is_error") is True


async def test_zoom_redraws_the_region_sharp_on_a_scrolled_page(browser):
    await load_playground(browser)
    await page_value(
        browser,
        "window.scrollTo(0, 1000);"
        " document.body.insertAdjacentHTML('beforeend', '<div style=\"position: fixed; left: 20px; top: 20px;"
        " width: 3px; height: 3px; background: #000\"></div>')",
    )
    zoomed = image(await call(browser, "zoom", region=[0, 0, 320, 180])).convert("L")
    # The square is at x 20..23 and y 20..23, so 80..92 at 4 times. A redrawn edge has no gray pixels between them.
    row = [zoomed.getpixel((x, 86)) for x in range(70, 102)]
    assert row.count(0) == 12, row
    assert all(value in (0, 255) for value in row), row


async def test_coordinate_outside_the_viewport_is_refused(browser):
    result = await call(browser, "left_click", target={"type": "coordinate", "x": 1280, "y": 10})
    assert result.get("is_error") is True and "outside" in text(result)


async def test_scroll_and_scroll_to(browser):
    await load_playground(browser)
    assert not (
        await call(
            browser,
            "scroll",
            target={"type": "coordinate", "x": 600, "y": 300},
            scroll_direction="down",
            scroll_amount=5,
        )
    ).get("is_error")
    assert await page_value(browser, "window.scrollY") > 0
    bottom = await ref_of(browser, "bottom of the page")
    await page_value(browser, "window.scrollTo(0, 0)")
    assert not (await call(browser, "scroll_to", target={"type": "ref", "ref": bottom})).get("is_error")
    assert await page_value(browser, "window.scrollY") > 2000


async def test_dialog_is_dismissed_and_reported(browser):
    await load_playground(browser)
    result = await call(browser, "left_click", target={"type": "ref", "ref": await ref_of(browser, "warn me button")})
    assert not result.get("is_error"), result
    # The dialog can open after the click returns, so the next call reports it.
    reported = text(result) + text(await call(browser, "wait", duration=0.5))
    assert "Are you sure?" in reported and "dismissed" in reported


async def test_request_guard_refuses_a_link_to_another_host(browser):
    await load_playground(browser)
    result = await call(browser, "left_click", target={"type": "ref", "ref": await ref_of(browser, "leave link")})
    later = await call(browser, "wait", duration=1)
    assert "evil.example.net" not in browser.page().url
    assert "refused" in (text(result) + text(later)).lower()


async def test_javascript_exec(browser):
    await load_playground(browser)
    assert text(await call(browser, "javascript_exec", text="document.title")) == "Playground"
    assert text(await call(browser, "javascript_exec", text="const n = 2; n * 21")) == "42"
    assert text(await call(browser, "javascript_exec", text="const n = 3; n")) == "3"
    assert text(await call(browser, "javascript_exec", text="Promise.resolve({a: 1})")) == '{"a":1}'
    thrown = await call(browser, "javascript_exec", text="throw new Error('nope')")
    assert thrown.get("is_error") is True and "nope" in text(thrown)


async def test_console_and_network(browser):
    await call(browser, "read_console")  # drop entries from earlier tests
    await call(browser, "navigate", url="https://example.com/")
    network = text(await call(browser, "read_network"))
    assert "GET https://example.com/ 200 text/html" in network
    await load_playground(browser)
    await call(browser, "left_click", target={"type": "ref", "ref": await ref_of(browser, "log me button")})
    console = text(await call(browser, "read_console"))
    assert "[log] hello from the page" in console and "[error] bad thing" in console
    assert text(await call(browser, "read_console")) in ("", "(empty)")


async def test_file_upload_sends_the_file_to_the_session(browser, upload_root):
    await load_playground(browser)
    ref = await ref_of(browser, "attachment")
    result = await call(
        browser, "file_upload", target={"type": "ref", "ref": ref}, paths=[str(upload_root / "order.csv")]
    )
    assert not result.get("is_error"), result
    attached = await page_value(
        browser,
        "(async () => { const f = document.getElementById('attachment').files[0]; return [f.name, await f.text()] })()",
    )
    assert attached == ["order.csv", "grade,count\nA572,12\n"]


async def test_file_upload_outside_the_upload_root_is_refused(browser, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not send")
    await load_playground(browser)
    ref = await ref_of(browser, "attachment")
    result = await call(browser, "file_upload", target={"type": "ref", "ref": ref}, paths=[str(secret)])
    assert result.get("is_error") is True
    assert await page_value(browser, "document.getElementById('attachment').files.length") == 0


async def test_download_is_reported_and_readable(browser):
    await load_playground(browser)
    clicked = await call(browser, "left_click", target={"type": "ref", "ref": await ref_of(browser, "get beams link")})
    # A small download can finish before the click's own report, so collect from that report too.
    changes: list[dict[str, Any]] = list(state(clicked).get("state_changes", []))
    for _ in range(10):
        changes += state(await call(browser, "wait", duration=0.5)).get("state_changes", [])
        if any(change["type"] == "download_completed" for change in changes):
            break
    completed = next(change for change in changes if change["type"] == "download_completed")
    assert completed["size_bytes"] == len("steel beams")
    assert await browser.download_bytes(completed["download_id"]) == b"steel beams"


async def test_tabs(browser):
    opened = await call(browser, "new_tab")
    tabs = state(opened)["tabs"]
    new_id = next(tab["tab_id"] for tab in tabs if tab["active"])
    assert len(tabs) >= 2
    assert {"type": "tab_opened", "tab_id": new_id} in state(opened).get("state_changes", [])
    first_id = next(tab["tab_id"] for tab in tabs if not tab["active"])
    switched = state(await call(browser, "switch_tab", tab_id=first_id))
    assert next(tab for tab in switched["tabs"] if tab["active"])["tab_id"] == first_id
    closed = state(await call(browser, "close_tab", tab_id=new_id))
    assert all(tab["tab_id"] != new_id for tab in closed["tabs"])
    missing = await call(browser, "switch_tab", tab_id="tab_999")
    assert missing.get("is_error") is True


@pytest.mark.parametrize(
    ("name", "input"), [("wait", {"duration": 31}), ("hold_key", {"text": "shift", "duration": 31})]
)
async def test_long_durations_are_refused(browser, name, input):
    result = await call(browser, name, **input)
    assert result.get("is_error") is True and "between 0 and 30" in text(result)


async def test_drag_and_mouse_buttons(browser):
    await browser.page().set_content(
        "<div id=box style='width:600px;height:400px' "
        'onmousedown="this.dataset.down=event.clientX" onmouseup="this.dataset.up=event.clientX"></div>'
    )
    result = await call(
        browser,
        "left_click_drag",
        **{"from": {"type": "coordinate", "x": 50, "y": 50}},
        target={"type": "coordinate", "x": 400, "y": 60},
    )
    assert not result.get("is_error"), result
    assert json.loads(await page_value(browser, "JSON.stringify(document.getElementById('box').dataset)")) == {
        "down": "50",
        "up": "400",
    }
    await call(browser, "left_mouse_down", target={"type": "coordinate", "x": 100, "y": 100})
    await call(browser, "left_mouse_up", target={"type": "coordinate", "x": 200, "y": 100})
    assert await page_value(browser, "document.getElementById('box').dataset.up") == "200"


async def test_popup_becomes_the_active_tab(browser):
    await browser.page().set_content("<button onclick=\"window.open('about:blank#popup')\">Open popup</button>")
    clicked = await call(browser, "left_click", target={"type": "ref", "ref": await ref_of(browser, "open popup")})
    report = (
        state(clicked) if "tab_opened" in json.dumps(state(clicked)) else state(await call(browser, "wait", duration=1))
    )
    opened = [change["tab_id"] for change in report.get("state_changes", []) if change["type"] == "tab_opened"]
    assert len(opened) == 1
    assert next(tab for tab in report["tabs"] if tab["active"])["tab_id"] == opened[0]
    listed = state(await call(browser, "list_tabs"))
    assert any(tab["tab_id"] == opened[0] for tab in listed["tabs"])
    await call(browser, "close_tab", tab_id=opened[0])


async def test_other_pointer_and_key_members(browser):
    await browser.page().set_content(
        "<p id=line>steel beams and girders</p>"
        "<div id=pad style='width:300px;height:100px' oncontextmenu=\"this.dataset.menu='yes'; return false\""
        ' onmouseover="this.dataset.over=\'yes\'" onauxclick="this.dataset.aux=event.button"></div>'
        "<input id=field onkeydown=\"if (event.shiftKey) this.dataset.shift='yes'\">"
    )
    line = await ref_of(browser, "steel beams girders")
    assert not (await call(browser, "triple_click", target={"type": "ref", "ref": line})).get("is_error")
    assert (await page_value(browser, "getSelection().toString()")).strip() == "steel beams and girders"
    box = await browser.page().locator("#pad").bounding_box()
    point = {"type": "coordinate", "x": int(box["x"] + 50), "y": int(box["y"] + 50)}
    for name in ("hover", "right_click", "middle_click", "mouse_move"):
        result = await call(browser, name, target=point)
        assert not result.get("is_error"), (name, result)
    dataset = json.loads(await page_value(browser, "JSON.stringify(document.getElementById('pad').dataset)"))
    assert dataset == {"over": "yes", "menu": "yes", "aux": "1"}
    await call(browser, "left_click", target={"type": "ref", "ref": await ref_of(browser, "field")})
    assert not (await call(browser, "hold_key", text="shift", duration=0.2)).get("is_error")
    assert await page_value(browser, "document.getElementById('field').dataset.shift") == "yes"
