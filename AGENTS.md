# AGENTS.md

## What this is

`steel_browser_toolset` is a driver for Claude's browser toolset (`browser_toolset_20260801`). `SteelBrowser` is a
subclass of the Anthropic SDK class `BetaAsyncAbstractBrowserToolset20260801`. It runs each of the 31 browser members
on a Steel cloud browser session. It drives the page with Playwright over CDP, and it moves uploads and downloads with
the Steel Files API.

## Layout

- `steel_browser_toolset/driver.py`: `SteelBrowser`, the session lifecycle, tabs, events and the 31 members.
- `steel_browser_toolset/tree.py`: the accessibility outline for `read_page` and `find` (no I/O).
- `steel_browser_toolset/keys.py`: key names and chords to Playwright key names (no I/O).
- `steel_browser_toolset/policy.py`: the host allowlist URL policy (no I/O).
- `examples/quickstart.py`: a full run with the tool runner.
- `tests/`: unit tests, and `test_live.py`, which needs a Steel API key.
- `docs/steel-docs-integration.mdx`: a draft page for the Steel docs site.

## Commands

```bash
uv sync                                   # install
uv run ruff check . && uv run ruff format --check .
uv run pytest                             # unit tests, no network
uv run pytest -m live                     # live tests, needs STEEL_API_KEY
uv run --env-file .env examples/quickstart.py "task"   # needs STEEL_API_KEY and ANTHROPIC_API_KEY
```

## Rules

- Every code file starts with two `ABOUTME:` comment lines.
- Write comments and docs in ASD-STE100 Simplified Technical English.
- A bug fix starts with a failing test. A change to the driver needs the live tests to pass.
- Text that reaches the model (errors, results) uses fixed phrases. Never put `str(exception)` in a `ToolError`.
- Keep `_browser_state` free of exceptions: an exception there stops the run.

## Facts that are not in the code

- The SDK drops a `tab_opened` change for a tab that is not in the same report. Read the tab ids and drain the changes
  with no `await` between them.
- A page restored from the back/forward cache fires no `DOMContentLoaded`. History moves wait for `commit`.
- Steel `websocketUrl` carries a token with a 120 s lifetime. An open CDP connection stays usable after it expires.
- A file uploaded with the Steel Files API without `path` gets a UUID as its name in the page. The driver sets `path`.
- `browser-use` 0.13.11 ships a Claude toolset that does not import with any released `anthropic` package. This is
  why Steel has its own driver.
