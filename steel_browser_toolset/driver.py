# ABOUTME: SteelBrowser, a driver for Claude's browser toolset that runs each member on a Steel cloud browser session.
# ABOUTME: It drives the page with Playwright over CDP, and moves uploads and downloads with the Steel Files API.

from __future__ import annotations

import asyncio
import base64
import inspect
import re
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal, Self, override

from anthropic import AsyncAnthropic
from anthropic.tools import ToolError
from anthropic.tools.browser import (
    BetaAsyncAbstractBrowserToolset20260801,
    BetaBrowserNavigateResult,
    BetaBrowserState,
    BetaDialogDismissed,
    BetaNavigationRefused,
    BetaScreenshotResult,
    BetaToolsetCallContext,
    BetaURLContext,
)
from anthropic.types.beta import (
    BetaBrowserCloseTabInput,
    BetaBrowserCoordinateTarget,
    BetaBrowserDoubleClickInput,
    BetaBrowserFileUploadInput,
    BetaBrowserFindInput,
    BetaBrowserFormInputInput,
    BetaBrowserGetPageTextInput,
    BetaBrowserHoldKeyInput,
    BetaBrowserHoverInput,
    BetaBrowserJavascriptExecInput,
    BetaBrowserKeyInput,
    BetaBrowserLeftClickDragInput,
    BetaBrowserLeftClickInput,
    BetaBrowserLeftMouseDownInput,
    BetaBrowserLeftMouseUpInput,
    BetaBrowserListTabsInput,
    BetaBrowserMemberInput,
    BetaBrowserMemberName,
    BetaBrowserMiddleClickInput,
    BetaBrowserMouseMoveInput,
    BetaBrowserNavigateInput,
    BetaBrowserNewTabInput,
    BetaBrowserReadConsoleInput,
    BetaBrowserReadNetworkInput,
    BetaBrowserReadPageInput,
    BetaBrowserRefTarget,
    BetaBrowserRightClickInput,
    BetaBrowserScreenshotInput,
    BetaBrowserScrollInput,
    BetaBrowserScrollToInput,
    BetaBrowserStateTabEntryParam,
    BetaBrowserSwitchTabInput,
    BetaBrowserTripleClickInput,
    BetaBrowserTypeInput,
    BetaBrowserWaitInput,
    BetaBrowserZoomInput,
)
from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Dialog,
    Page,
    Playwright,
    Request,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from steel import APIError as SteelAPIError
from steel import AsyncSteel

from .keys import UnknownKeyError, chord_string, parse_chord, parse_sequence
from .policy import with_scheme
from .tree import MAX_OUTPUT_CHARS, find_matches, render_outline, viewport_rects

NAVIGATION_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 15_000
HISTORY_LOAD_TIMEOUT_MS = 10_000
TITLE_TIMEOUT_S = 2
MAX_DURATION_S = 30
MAX_KEY_REPEAT = 100
SCROLL_PIXELS_PER_NOTCH = 100
# `type` sends one key event for each character up to this length, and inserts longer text in one step, because each
# key event is a round trip to the remote browser.
MAX_TYPED_CHARS = 200
MAX_LOG_ENTRIES = 1_000
MAX_DIALOG_MESSAGE_CHARS = 500
STALE_REF = "Unknown or stale ref: {ref}. Call read_page or find again for current refs."
# Steel saves browser downloads in the session's file namespace, which the Files API reads.
SESSION_DOWNLOAD_DIR = "/files"

RequestGuard = Literal["all", "documents", "off"]


class SteelBrowser(BetaAsyncAbstractBrowserToolset20260801):
    """Claude's browser toolset on a Steel cloud browser.

    Use it as an async context manager. On enter, it creates a Steel session (or attaches to `session_id`) and connects
    to it over CDP. On exit, it disconnects and releases the session it created. A session it attached to stays open.

    `steel` is the Steel client. `session_options` go to `sessions.create`, for example `{"use_proxy": True,
    "solve_captcha": True}`. `viewport` is the page size in CSS pixels, and every screenshot has this size.
    `request_guard` tells which browser requests the `url_policy` also checks: "all" (the default), "documents"
    (pages and frames only) or "off". `anthropic` is the client that reads Files API documents for `file_upload`.
    The other keyword arguments are the SDK toolset options (`url_policy`, `configs`, `confirm`, `file_policy`,
    `tool_configs`).
    """

    def __init__(
        self,
        steel: AsyncSteel,
        *,
        session_id: str | None = None,
        session_options: dict[str, Any] | None = None,
        viewport: tuple[int, int] = (1280, 720),
        request_guard: RequestGuard = "all",
        anthropic: AsyncAnthropic | None = None,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self._steel = steel
        self._session_id = session_id
        self._session_options = dict(session_options or {})
        self._viewport = viewport
        self._url_policy = options.get("url_policy")
        self._request_guard = request_guard if callable(self._url_policy) else "off"
        self._anthropic = anthropic
        self.session: Any = None
        self._owns_session = False
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._browser_cdp: CDPSession | None = None
        self._tabs: dict[str, Page] = {}
        self._cdp_sessions: dict[str, CDPSession] = {}
        self._active: str | None = None
        self._next_tab = 1
        self._refs: dict[str, tuple[str, int]] = {}
        self._ref_names: dict[tuple[str, int], str] = {}
        self._next_ref = 1
        self._changes: list[Any] = []
        self._console: dict[str, deque[str]] = {}
        self._network: dict[str, deque[str]] = {}
        self._downloads: dict[str, str] = {}
        self._next_upload = 1

    # --- lifecycle ---

    @override
    async def __aenter__(self) -> Self:
        await super().__aenter__()
        try:
            await self._start()
        except BaseException:
            await self.close()
            raise
        return self

    async def _start(self) -> None:
        if self._session_id is not None:
            self.session = await self._steel.sessions.retrieve(self._session_id)
        else:
            width, height = self._viewport
            options = {"dimensions": {"width": width, "height": height}, **self._session_options}
            self.session = await self._steel.sessions.create(**options)
            self._owns_session = True
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            f"{self.session.websocket_url}&apiKey={self._steel.steel_api_key}"
        )
        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        if self._request_guard != "off":
            await self._context.route("**/*", self._guard)
        self._browser_cdp = await self._browser.new_browser_cdp_session()
        self._browser_cdp.on("Browser.downloadWillBegin", self._on_download_begin)
        self._browser_cdp.on("Browser.downloadProgress", self._on_download_progress)
        await self._browser_cdp.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allowAndName", "downloadPath": SESSION_DOWNLOAD_DIR, "eventsEnabled": True},
        )
        pages = self._context.pages or [await self._context.new_page()]
        for page in pages:
            await self._register(page)
        self._active = next(iter(self._tabs))
        self._changes.clear()  # the tabs that were open on attach are not news to the model
        self._context.on("page", self._on_page)

    @override
    async def close(self) -> None:
        """Disconnect from the browser, and release the Steel session if this driver created it. Safe to call twice."""
        await super().close()
        if self._browser is not None:
            try:
                await self._browser.close()
            except PlaywrightError:
                pass  # the connection is already gone
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        if self._owns_session and self.session is not None:
            try:
                await self._steel.sessions.release(self.session.id)
            except SteelAPIError:
                pass  # the session timed out or was released already
            self._owns_session = False

    async def download_bytes(self, download_id: str) -> bytes:
        """The content of a finished download, read from the Steel session with the Files API.

        `download_id` is the `download_id` of a `download_completed` state change. Do not open or run the file
        until a person decides to: a page chose its content.
        """
        response = await self._steel.sessions.files.download(download_id, session_id=self.session.id)
        return await response.read()

    def page(self, tab_id: str | None = None) -> Page:
        """The Playwright page of a tab (the active tab when `tab_id` is None), for setup code outside the model loop,
        such as a sign-in. The URL policy and `confirm` do not check what this code does."""
        return self._tab(tab_id)[1]

    # --- tabs and events ---

    async def _register(self, page: Page) -> str:
        """Add a page as a tab, or return its tab id when it is already a tab."""
        for tab_id, known in self._tabs.items():
            if known is page:
                return tab_id
        tab_id = f"tab_{self._next_tab}"
        self._next_tab += 1
        self._tabs[tab_id] = page
        self._console[tab_id] = deque(maxlen=MAX_LOG_ENTRIES)
        self._network[tab_id] = deque(maxlen=MAX_LOG_ENTRIES)
        self._changes.append({"type": "tab_opened", "tab_id": tab_id})
        page.on("console", lambda message: self._log(self._console, tab_id, f"[{message.type}] {message.text}"))
        page.on("pageerror", lambda error: self._log(self._console, tab_id, f"[pageerror] {error}"))
        page.on("requestfinished", lambda request: self._on_request_finished(tab_id, request))
        page.on("requestfailed", lambda request: self._on_request_failed(tab_id, request))
        page.on("dialog", self._on_dialog)
        page.on("close", lambda _page: self._on_close(tab_id))
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        width, height = self._viewport
        await page.set_viewport_size({"width": width, "height": height})
        return tab_id

    async def _on_page(self, page: Page) -> None:
        # A page the site opened (a popup, a link with target=_blank) becomes the active tab, as in a browser.
        known = any(page is existing for existing in self._tabs.values())
        tab_id = await self._register(page)
        if not known:
            self._active = tab_id

    def _on_close(self, tab_id: str) -> None:
        self._tabs.pop(tab_id, None)
        self._cdp_sessions.pop(tab_id, None)
        self._console.pop(tab_id, None)
        self._network.pop(tab_id, None)
        if self._active == tab_id:
            self._active = next(reversed(self._tabs), None)

    @staticmethod
    def _log(store: dict[str, deque[str]], tab_id: str, line: str) -> None:
        # An event can arrive after its tab closed, when the tab has no log.
        if tab_id in store:
            store[tab_id].append(line)

    async def _on_request_finished(self, tab_id: str, request: Request) -> None:
        try:
            response = await request.response()
        except PlaywrightError:
            response = None  # the page closed before the response could be read
        status = str(response.status) if response else "-"
        mime = (response.headers.get("content-type", "-").split(";")[0] if response else "-") or "-"
        elapsed = request.timing.get("responseEnd", -1)
        took = f" {round(elapsed)}ms" if elapsed >= 0 else ""
        self._log(self._network, tab_id, f"{request.method} {request.url} {status} {mime}{took}")

    def _on_request_failed(self, tab_id: str, request: Request) -> None:
        self._log(self._network, tab_id, f"{request.method} {request.url} failed {request.failure or 'unknown error'}")

    async def _on_dialog(self, dialog: Dialog) -> None:
        self._changes.append(BetaDialogDismissed(kind=dialog.type, message=dialog.message[:MAX_DIALOG_MESSAGE_CHARS]))
        try:
            await dialog.dismiss()
        except PlaywrightError:
            pass  # the page closed the dialog itself

    def _on_download_begin(self, event: dict[str, Any]) -> None:
        self._downloads[event["guid"]] = event.get("url", "")
        self._changes.append({"type": "download_started", "download_id": event["guid"], "url": event.get("url", "")})

    def _on_download_progress(self, event: dict[str, Any]) -> None:
        download_id = event["guid"]
        url = self._downloads.get(download_id, "")
        if event.get("state") == "completed":
            self._changes.append(
                {
                    "type": "download_completed",
                    "download_id": download_id,
                    "url": url,
                    "size_bytes": int(event.get("receivedBytes", 0)),
                }
            )
        elif event.get("state") == "canceled":
            self._changes.append(
                {"type": "download_failed", "download_id": download_id, "url": url, "error": "download canceled"}
            )

    async def _guard(self, route: Route) -> None:
        request = route.request
        if self._request_guard == "documents" and request.resource_type not in ("document", "iframe"):
            await route.fallback()
            return
        try:
            verdict = self._url_policy(BetaURLContext(), request.url)  # type: ignore[misc]
            if inspect.isawaitable(verdict):
                await verdict
        except Exception:  # noqa: BLE001 - any exception from the policy refuses the request, as in the SDK
            if request.is_navigation_request():
                self._changes.append(BetaNavigationRefused())
            await route.abort("blockedbyclient")
            return
        await route.fallback()

    def _tab(self, tab_id: str | None) -> tuple[str, Page]:
        resolved = tab_id or self._active
        if resolved is None or resolved not in self._tabs:
            raise ToolError(f"No open tab has the id {tab_id}." if tab_id else "No tab is open. Call new_tab first.")
        return resolved, self._tabs[resolved]

    async def _cdp(self, tab_id: str) -> CDPSession:
        if tab_id not in self._cdp_sessions:
            assert self._context is not None
            self._cdp_sessions[tab_id] = await self._context.new_cdp_session(self._tabs[tab_id])
        return self._cdp_sessions[tab_id]

    async def _tab_entry(self, tab_id: str) -> BetaBrowserStateTabEntryParam:
        page = self._tabs[tab_id]
        return {"tab_id": tab_id, "title": await _title(page), "url": page.url, "active": tab_id == self._active}

    @override
    async def _browser_state(self, context: BetaToolsetCallContext) -> BetaBrowserState:
        # Take the tab ids and the changes together, with no await between them. The SDK drops a `tab_opened` for a
        # tab that is not in the same report, and a popup can open while the titles are read.
        tab_ids, changes, self._changes = list(self._tabs), self._changes, []
        tabs = [await self._tab_entry(tab_id) for tab_id in tab_ids if tab_id in self._tabs]
        # Mark exactly one tab active, from one reading of the active tab: it can change while the titles are read.
        active = self._active if any(tab["tab_id"] == self._active for tab in tabs) else None
        for index, tab in enumerate(tabs):
            tab["active"] = tab["tab_id"] == active if active else index == len(tabs) - 1
        return BetaBrowserState(tabs=tabs, state_changes=changes)

    # --- refs and points ---

    def _ref_for(self, tab_id: str) -> Callable[[int], str]:
        def ref_for(backend_id: int) -> str:
            key = (tab_id, backend_id)
            if key not in self._ref_names:
                name = f"ref_{self._next_ref}"
                self._next_ref += 1
                self._ref_names[key] = name
                self._refs[name] = key
            return self._ref_names[key]

        return ref_for

    def _backend_id(self, tab_id: str, target: BetaBrowserRefTarget) -> int:
        owner = self._refs.get(target.ref)
        if owner is None or owner[0] != tab_id:
            raise ToolError(STALE_REF.format(ref=target.ref))
        return owner[1]

    async def _point(
        self, tab_id: str, target: BetaBrowserCoordinateTarget | BetaBrowserRefTarget
    ) -> tuple[float, float]:
        """The viewport point to act on: the coordinate itself, or the centre of the ref's element after scrolling it
        into view."""
        if target.type == "coordinate":
            width, height = self._viewport
            if not (0 <= target.x < width and 0 <= target.y < height):
                raise ToolError(f"[{target.x}, {target.y}] is outside the {width}x{height} viewport")
            return float(target.x), float(target.y)
        backend_id = self._backend_id(tab_id, target)
        cdp = await self._cdp(tab_id)
        try:
            await cdp.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_id})
            quads = (await cdp.send("DOM.getContentQuads", {"backendNodeId": backend_id}))["quads"]
        except PlaywrightError:
            raise ToolError(STALE_REF.format(ref=target.ref)) from None
        if not quads:
            raise ToolError(f"{target.ref} has no visible box on the page.")
        quad = quads[0]
        return sum(quad[0::2]) / 4, sum(quad[1::2]) / 4

    async def _object_id(self, tab_id: str, target: BetaBrowserRefTarget) -> str:
        backend_id = self._backend_id(tab_id, target)
        cdp = await self._cdp(tab_id)
        try:
            resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})
        except PlaywrightError:
            raise ToolError(STALE_REF.format(ref=target.ref)) from None
        return resolved["object"]["objectId"]

    @asynccontextmanager
    async def _held(self, page: Page, modifiers: str | None) -> AsyncIterator[None]:
        keys = _chord(modifiers) if modifiers else []
        pressed: list[str] = []
        try:
            for key in keys:
                await page.keyboard.down(key)
                pressed.append(key)
            yield
        finally:
            for key in reversed(pressed):
                await page.keyboard.up(key)

    async def _click(self, input: Any, button: Literal["left", "right", "middle"], count: int) -> None:
        tab_id, page = self._tab(input.tab_id)
        x, y = await self._point(tab_id, input.target)
        async with self._held(page, input.modifiers):
            await page.mouse.click(x, y, button=button, click_count=count)

    # --- error mapping ---

    @override
    async def execute(
        self, context: BetaToolsetCallContext, name: BetaBrowserMemberName, input: BetaBrowserMemberInput
    ) -> Any:
        # Playwright and Steel errors can hold addresses and paths, so the model reads a fixed phrase instead.
        try:
            return await super().execute(context, name, input)
        except PlaywrightTimeoutError:
            raise ToolError(f"{name} failed: the page did not respond in time") from None
        except PlaywrightError as error:
            raise ToolError(f"{name} failed: {_reason(error)}") from None
        except SteelAPIError:
            raise ToolError(f"{name} failed: the Steel session did not accept the request") from None

    # --- navigation and tabs ---

    @override
    async def navigate(
        self, context: BetaToolsetCallContext, input: BetaBrowserNavigateInput
    ) -> BetaBrowserNavigateResult:
        _, page = self._tab(input.tab_id)
        if input.url in ("back", "forward"):
            # A page restored from the back/forward cache fires no DOMContentLoaded, so wait for the commit, then
            # for the DOM of a page that loads again.
            move = page.go_back if input.url == "back" else page.go_forward
            response = await move(wait_until="commit")
            with suppress(PlaywrightTimeoutError):
                await page.wait_for_load_state("domcontentloaded", timeout=HISTORY_LOAD_TIMEOUT_MS)
        elif input.url == "reload":
            response = await page.reload(wait_until="domcontentloaded")
        else:
            # Open the address the way the URL policy reads it, and refuse every scheme but http(s) here too.
            url = with_scheme(input.url)
            if url.lower() != "about:blank" and not re.match(r"https?://", url, re.IGNORECASE):
                raise ToolError("blocked: only http and https addresses can be opened")
            response = await page.goto(url, wait_until="domcontentloaded")
        title = await _title(page)
        return BetaBrowserNavigateResult(
            url=page.url, status=response.status if response else None, title=title or None
        )

    @override
    async def new_tab(
        self, context: BetaToolsetCallContext, input: BetaBrowserNewTabInput
    ) -> BetaBrowserStateTabEntryParam:
        assert self._context is not None
        page = await self._context.new_page()
        tab_id = await self._register(page)
        self._active = tab_id
        await page.bring_to_front()
        return await self._tab_entry(tab_id)

    @override
    async def list_tabs(
        self, context: BetaToolsetCallContext, input: BetaBrowserListTabsInput
    ) -> list[BetaBrowserStateTabEntryParam]:
        return [await self._tab_entry(tab_id) for tab_id in list(self._tabs)]

    @override
    async def switch_tab(
        self, context: BetaToolsetCallContext, input: BetaBrowserSwitchTabInput
    ) -> BetaBrowserStateTabEntryParam:
        tab_id, page = self._tab(input.tab_id)
        await page.bring_to_front()
        self._active = tab_id
        return await self._tab_entry(tab_id)

    @override
    async def close_tab(self, context: BetaToolsetCallContext, input: BetaBrowserCloseTabInput) -> None:
        tab_id, page = self._tab(input.tab_id)
        await page.close()
        self._on_close(tab_id)  # the close event can come after this call returns

    # --- reading ---

    @override
    async def screenshot(
        self, context: BetaToolsetCallContext, input: BetaBrowserScreenshotInput
    ) -> BetaScreenshotResult:
        _, page = self._tab(input.tab_id)
        png = await page.screenshot(type="png", scale="css")
        return BetaScreenshotResult(data=base64.b64encode(png).decode(), media_type="image/png")

    @override
    async def zoom(self, context: BetaToolsetCallContext, input: BetaBrowserZoomInput) -> BetaScreenshotResult:
        tab_id, page = self._tab(input.tab_id)
        width, height = self._viewport
        if len(input.region) != 4:
            raise ToolError("region must be [x0, y0, x1, y1]")
        x0, y0, x1, y1 = input.region
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ToolError(f"region must be inside the {width}x{height} viewport, with x0 < x1 and y0 < y1")
        # Scale the region up to fill at most the viewport size, and never more than 4 times. Chrome draws the region
        # again at this scale, so small text stays sharp.
        factor = max(min(width / (x1 - x0), height / (y1 - y0), 4.0), 1.0)
        # The CDP clip is in page coordinates, and Chrome multiplies its scale by the device pixel ratio.
        scroll_x, scroll_y, ratio = await page.evaluate("[window.scrollX, window.scrollY, window.devicePixelRatio]")
        cdp = await self._cdp(tab_id)
        shot = await cdp.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "clip": {
                    "x": x0 + scroll_x,
                    "y": y0 + scroll_y,
                    "width": x1 - x0,
                    "height": y1 - y0,
                    "scale": factor / ratio,
                },
            },
        )
        return BetaScreenshotResult(data=shot["data"], media_type="image/png")

    async def _page_model(self, tab_id: str) -> tuple[list[dict[str, Any]], Any]:
        cdp = await self._cdp(tab_id)
        ax = await cdp.send("Accessibility.getFullAXTree")
        snapshot = await cdp.send("DOMSnapshot.captureSnapshot", {"computedStyles": []})
        return ax["nodes"], viewport_rects(snapshot)

    @override
    async def read_page(self, context: BetaToolsetCallContext, input: BetaBrowserReadPageInput) -> str:
        tab_id, _ = self._tab(input.tab_id)
        if input.depth is not None and not 1 <= input.depth <= 15:
            raise ToolError("depth must be between 1 and 15")
        root = self._backend_id(tab_id, BetaBrowserRefTarget(type="ref", ref=input.ref)) if input.ref else None
        nodes, rects = await self._page_model(tab_id)
        outline = render_outline(
            nodes,
            rects,
            self._viewport,
            self._ref_for(tab_id),
            filter=input.filter,
            depth=input.depth,
            root_backend_id=root,
        )
        if outline is None:
            raise ToolError(STALE_REF.format(ref=input.ref))
        return outline

    @override
    async def find(self, context: BetaToolsetCallContext, input: BetaBrowserFindInput) -> str:
        tab_id, _ = self._tab(input.tab_id)
        nodes, rects = await self._page_model(tab_id)
        return find_matches(nodes, rects, input.query, self._ref_for(tab_id))

    @override
    async def get_page_text(self, context: BetaToolsetCallContext, input: BetaBrowserGetPageTextInput) -> str:
        _, page = self._tab(input.tab_id)
        return _bounded(await page.evaluate(PAGE_TEXT_JS) or "")

    @override
    async def read_console(self, context: BetaToolsetCallContext, input: BetaBrowserReadConsoleInput) -> str:
        tab_id, _ = self._tab(input.tab_id)
        return _drain(self._console[tab_id])

    @override
    async def read_network(self, context: BetaToolsetCallContext, input: BetaBrowserReadNetworkInput) -> str:
        tab_id, _ = self._tab(input.tab_id)
        return _drain(self._network[tab_id])

    @override
    async def javascript_exec(self, context: BetaToolsetCallContext, input: BetaBrowserJavascriptExecInput) -> str:
        _, page = self._tab(input.tab_id)
        outcome = await page.evaluate(JAVASCRIPT_EXEC_JS, input.text)
        if not outcome["ok"]:
            raise ToolError(f"The script threw: {outcome['value'][:1000]}")
        return _bounded(outcome["value"])

    # --- pointer ---

    @override
    async def left_click(self, context: BetaToolsetCallContext, input: BetaBrowserLeftClickInput) -> None:
        await self._click(input, "left", 1)

    @override
    async def right_click(self, context: BetaToolsetCallContext, input: BetaBrowserRightClickInput) -> None:
        await self._click(input, "right", 1)

    @override
    async def middle_click(self, context: BetaToolsetCallContext, input: BetaBrowserMiddleClickInput) -> None:
        await self._click(input, "middle", 1)

    @override
    async def double_click(self, context: BetaToolsetCallContext, input: BetaBrowserDoubleClickInput) -> None:
        await self._click(input, "left", 2)

    @override
    async def triple_click(self, context: BetaToolsetCallContext, input: BetaBrowserTripleClickInput) -> None:
        await self._click(input, "left", 3)

    @override
    async def hover(self, context: BetaToolsetCallContext, input: BetaBrowserHoverInput) -> None:
        tab_id, page = self._tab(input.tab_id)
        x, y = await self._point(tab_id, input.target)
        await page.mouse.move(x, y)

    @override
    async def mouse_move(self, context: BetaToolsetCallContext, input: BetaBrowserMouseMoveInput) -> None:
        tab_id, page = self._tab(input.tab_id)
        x, y = await self._point(tab_id, input.target)
        await page.mouse.move(x, y)

    @override
    async def left_mouse_down(self, context: BetaToolsetCallContext, input: BetaBrowserLeftMouseDownInput) -> None:
        tab_id, page = self._tab(input.tab_id)
        x, y = await self._point(tab_id, input.target)
        await page.mouse.move(x, y)
        await page.mouse.down()

    @override
    async def left_mouse_up(self, context: BetaToolsetCallContext, input: BetaBrowserLeftMouseUpInput) -> None:
        tab_id, page = self._tab(input.tab_id)
        x, y = await self._point(tab_id, input.target)
        await page.mouse.move(x, y)
        await page.mouse.up()

    @override
    async def left_click_drag(self, context: BetaToolsetCallContext, input: BetaBrowserLeftClickDragInput) -> None:
        tab_id, page = self._tab(input.tab_id)
        start = await self._point(tab_id, input.from_)
        end = await self._point(tab_id, input.target)
        await page.mouse.move(*start)
        await page.mouse.down()
        try:
            await page.mouse.move(*end, steps=10)
        finally:
            await page.mouse.up()

    @override
    async def scroll(self, context: BetaToolsetCallContext, input: BetaBrowserScrollInput) -> None:
        tab_id, page = self._tab(input.tab_id)
        amount = 3 if input.scroll_amount is None else input.scroll_amount
        if not 1 <= amount <= 10:
            raise ToolError("scroll_amount must be between 1 and 10")
        x, y = await self._point(tab_id, input.target)
        pixels = amount * SCROLL_PIXELS_PER_NOTCH
        dx, dy = {"up": (0, -pixels), "down": (0, pixels), "left": (-pixels, 0), "right": (pixels, 0)}[
            input.scroll_direction
        ]
        await page.mouse.move(x, y)
        await page.mouse.wheel(dx, dy)

    @override
    async def scroll_to(self, context: BetaToolsetCallContext, input: BetaBrowserScrollToInput) -> None:
        tab_id, _ = self._tab(input.tab_id)
        await self._point(tab_id, input.target)

    # --- keyboard and timing ---

    @override
    async def type(self, context: BetaToolsetCallContext, input: BetaBrowserTypeInput) -> None:
        _, page = self._tab(input.tab_id)
        if len(input.text) <= MAX_TYPED_CHARS:
            await page.keyboard.type(input.text)
        else:
            await page.keyboard.insert_text(input.text)

    @override
    async def key(self, context: BetaToolsetCallContext, input: BetaBrowserKeyInput) -> None:
        _, page = self._tab(input.tab_id)
        repeat = 1 if input.repeat is None else input.repeat
        if not 1 <= repeat <= MAX_KEY_REPEAT:
            raise ToolError(f"repeat must be between 1 and {MAX_KEY_REPEAT}")
        try:
            chords = parse_sequence(input.text)
        except UnknownKeyError as error:
            raise ToolError(str(error)) from None
        for _ in range(repeat):
            for chord in chords:
                await page.keyboard.press(chord_string(chord))

    @override
    async def hold_key(self, context: BetaToolsetCallContext, input: BetaBrowserHoldKeyInput) -> None:
        _, page = self._tab(input.tab_id)
        _check_duration(input.duration)
        keys = _chord(input.text)
        pressed: list[str] = []
        try:
            for key in keys:
                await page.keyboard.down(key)
                pressed.append(key)
            await asyncio.sleep(input.duration)
        finally:
            for key in reversed(pressed):
                await page.keyboard.up(key)

    @override
    async def wait(self, context: BetaToolsetCallContext, input: BetaBrowserWaitInput) -> None:
        _check_duration(input.duration)
        await asyncio.sleep(input.duration)

    # --- forms and files ---

    @override
    async def form_input(self, context: BetaToolsetCallContext, input: BetaBrowserFormInputInput) -> str:
        tab_id, _ = self._tab(input.tab_id)
        object_id = await self._object_id(tab_id, input.target)
        cdp = await self._cdp(tab_id)
        result = await cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": FORM_INPUT_JS,
                "arguments": [{"value": input.value}],
                "returnByValue": True,
            },
        )
        outcome = result.get("result", {}).get("value") or {"ok": False, "message": "the element did not answer"}
        if not outcome["ok"]:
            raise ToolError(outcome["message"])
        return outcome["message"]

    @override
    async def file_upload(self, context: BetaToolsetCallContext, input: BetaBrowserFileUploadInput) -> str:
        tab_id, _ = self._tab(input.tab_id)
        backend_id = self._backend_id(tab_id, input.target)
        cdp = await self._cdp(tab_id)
        try:
            node = (await cdp.send("DOM.describeNode", {"backendNodeId": backend_id}))["node"]
        except PlaywrightError:
            raise ToolError(STALE_REF.format(ref=input.target.ref)) from None
        attributes = node.get("attributes") or []
        kind = dict(zip(attributes[0::2], attributes[1::2])).get("type", "").lower()
        if node.get("nodeName") != "INPUT" or kind != "file":
            raise ToolError(f"{input.target.ref} is not a file input.")
        # The file policy checked the paths on this machine, so the driver reads them here and sends their content to
        # the Steel session. The browser then attaches the copies in the session.
        files = [(Path(path).name, await asyncio.to_thread(Path(path).read_bytes)) for path in input.paths or []]
        for document_id in input.document_ids or []:
            files.append(await self._document(document_id))
        if not files:
            raise ToolError("Give at least one path or document id.")
        remote_paths = []
        for name, content in files:
            uploaded = await self._steel.sessions.files.upload(
                self.session.id, file=(name, content), path=f"uploads/{self._next_upload}/{name}"
            )
            self._next_upload += 1
            remote_paths.append(uploaded.path)
        await cdp.send("DOM.setFileInputFiles", {"files": remote_paths, "backendNodeId": backend_id})
        return f"Attached {len(remote_paths)} file(s)."

    async def _document(self, document_id: str) -> tuple[str, bytes]:
        if self._anthropic is None:
            raise ToolError("Document uploads are not available: the driver has no Anthropic client.")
        metadata = await self._anthropic.beta.files.retrieve_metadata(document_id)
        content = await self._anthropic.beta.files.download(document_id)
        return Path(metadata.filename).name or document_id, await content.read()


# --- helpers ---


async def _title(page: Page) -> str:
    """The page title, or an empty string while the page is busy or navigating. The next report has the title."""
    try:
        return await asyncio.wait_for(page.title(), timeout=TITLE_TIMEOUT_S)
    except (TimeoutError, PlaywrightError):
        return ""


def _chord(text: str) -> list[str]:
    try:
        return parse_chord(text)
    except UnknownKeyError as error:
        raise ToolError(str(error)) from None


def _check_duration(duration: float) -> None:
    if not 0 <= duration <= MAX_DURATION_S:
        raise ToolError(f"duration must be between 0 and {MAX_DURATION_S} seconds")


def _bounded(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n[Output cut at {MAX_OUTPUT_CHARS} characters.]"


def _drain(entries: deque[str]) -> str:
    lines = list(entries)
    entries.clear()
    return _bounded("\n".join(lines))


def _reason(error: PlaywrightError) -> str:
    """A fixed phrase for a Playwright error. The raw message can hold addresses and local paths."""
    message = error.message or ""
    network = re.search(r"net::ERR_[A-Z_]+", message)
    if network:
        if network.group() == "net::ERR_BLOCKED_BY_CLIENT":
            return "the URL policy refused an address that the page went to"
        return f"the browser reported {network.group()}"
    if "closed" in message.lower():
        return "the tab or the browser is closed"
    return "the browser returned an error"


# --- the JavaScript the members run in the page ---

PAGE_TEXT_JS = """() => {
  const body = document.body;
  if (!body) return '';
  const all = body.innerText || '';
  const main = document.querySelector('main, article, [role=main]');
  // Prefer the main content, unless it holds only a small part of the page's text.
  if (main && main.innerText && main.innerText.length >= all.length * 0.5) return main.innerText.trim();
  return all.trim();
}"""

JAVASCRIPT_EXEC_JS = """async (code) => {
  const show = (value) => {
    if (value === undefined) return 'undefined';
    if (typeof value === 'string') return value;
    try { const json = JSON.stringify(value); return json === undefined ? String(value) : json; }
    catch { return String(value); }
  };
  try {
    // An indirect eval runs the code in the global scope; its top-level const and let stay inside this call.
    let value = (0, eval)(code);
    if (value instanceof Promise) value = await value;
    return { ok: true, value: show(value) };
  } catch (error) {
    return { ok: false, value: String(error && error.message || error) };
  }
}"""

FORM_INPUT_JS = """function (value) {
  const el = this;
  const tag = el.tagName;
  const fire = () => {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  if (tag === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
    if (typeof value !== 'boolean') return { ok: false, message: 'Use true or false for a checkbox or radio button.' };
    if (el.checked !== value) el.click();
    return { ok: el.checked === value, message: el.checked === value ? `Set to ${value}.` : 'The page did not keep the change.' };
  }
  if (tag === 'SELECT') {
    const wanted = String(value);
    const option = [...el.options].find((o) => o.value === wanted) || [...el.options].find((o) => o.text.trim() === wanted.trim());
    if (!option) return { ok: false, message: 'No option has that value or text.' };
    el.value = option.value;
    fire();
    return { ok: true, message: `Selected "${option.text.trim()}".` };
  }
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    if (el.type === 'file') return { ok: false, message: 'Use file_upload for a file input.' };
    const proto = tag === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    // The native setter, so that frameworks that track the value property see the change.
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(value));
    fire();
    return { ok: el.value === String(value), message: el.value === String(value) ? 'Value set.' : 'The page changed the value.' };
  }
  if (el.isContentEditable) {
    el.focus();
    el.textContent = String(value);
    el.dispatchEvent(new InputEvent('input', { bubbles: true }));
    return { ok: true, message: 'Value set.' };
  }
  return { ok: false, message: 'The element is not a form field.' };
}"""
