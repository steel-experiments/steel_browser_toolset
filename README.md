# steel_browser_toolset

[![CI](https://github.com/steel-experiments/steel_browser_toolset/actions/workflows/ci.yml/badge.svg)](https://github.com/steel-experiments/steel_browser_toolset/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)

Run Claude's [browser toolset](https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-sdk) on [Steel](https://steel.dev) cloud browsers.

The Anthropic SDK defines the toolset's 31 tools (`navigate`, `read_page`, `find`, `left_click`, `form_input`, `screenshot` and the rest), its policies and the tool runner, but you bring the browser. `SteelBrowser` runs each tool call in an isolated Steel session, and you can watch every run live or replay it later.

## Install

Python 3.12 or later:

```bash
uv add git+https://github.com/steel-experiments/steel_browser_toolset
```

You need a [Steel API key](https://app.steel.dev/settings/api-keys) and an [Anthropic API key](https://platform.claude.com/settings/keys), set as `STEEL_API_KEY` and `ANTHROPIC_API_KEY`.

## Quickstart

```python
import asyncio

from anthropic import AsyncAnthropic
from steel import AsyncSteel
from steel_browser_toolset import SteelBrowser, allowed_hosts_policy


async def main():
    browser = SteelBrowser(AsyncSteel(), url_policy=allowed_hosts_policy(["example.com", "iana.org"]))
    async with browser, AsyncAnthropic() as client:
        print("Watch live:", browser.session.session_viewer_url)
        runner = client.beta.messages.tool_runner(
            model="claude-opus-5-5",
            max_tokens=4096,
            tools=[browser],
            messages=[{"role": "user", "content": "Open example.com and summarize what the page says."}],
        )
        async for message in runner:
            print(message)


asyncio.run(main())
```

`SteelBrowser` opens a Steel session when the `async with` block starts and releases it when the block ends. A runnable version is in [`examples/quickstart.py`](examples/quickstart.py):

```bash
cp .env.example .env    # add your keys
uv run --env-file .env examples/quickstart.py "Open example.com and summarize what the page says."
```

## What the driver handles

- `read_page` and `find` read Chrome's accessibility tree and tag each element `[ref_N]`. A ref stays the same across reads, and Claude gets a "stale ref" error once its page is gone.
- The SDK checks only the URLs Claude passes to `navigate`. The driver applies the same URL policy to links, redirects and subresources, and tells Claude when it blocks a navigation.
- For uploads, the SDK's file policy checks paths on your machine. The driver sends each approved file to the session through the Steel Files API and attaches it to the input. Anthropic Files API document IDs work too if you pass `anthropic=AsyncAnthropic()`.
- Downloads are saved in the session and reported to Claude. Fetch the bytes with `await browser.download_bytes(download_id)`.
- Popups become the active tab. `alert`, `confirm` and `prompt` dialogs are dismissed, and Claude is told what they said.
- Errors reach Claude as short fixed messages, without the URLs or local paths that raw exceptions contain.

## Options

```python
SteelBrowser(
    AsyncSteel(),
    url_policy=allowed_hosts_policy(["example.com"]),
    session_options={"use_proxy": True, "solve_captcha": True},  # passed to sessions.create
    session_id=None,  # attach to an existing session; the driver will not release it
    viewport=(1280, 720),  # CSS pixels; every screenshot has this size
    request_guard="all",  # "all", "documents" (pages and frames only) or "off"
    configs={"read_console": {"enabled": True}},
)
```

Four tools are off by default: `javascript_exec`, `file_upload`, `read_console` and `read_network`. Turn them on through `configs`. `javascript_exec` and `file_upload` also need a `confirm` callback that asks a person.

To run your own setup code before Claude starts, such as a sign-in, use `browser.page()`. It returns the Playwright page of the active tab. The URL policy does not apply to this code.

## Safety

Each Steel session is a separate cloud browser, so Claude cannot reach your machine or your network through it. A few things remain your responsibility:

- Keep `url_policy` tight. Without one, the browser can go anywhere.
- Don't sign the session in to an account you wouldn't hand to Claude.
- Use `confirm` to gate purchases, sent messages and anything else you can't take back.
- Treat page text, titles, URLs and downloads as untrusted.

Anthropic's [safety guide for the toolset](https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-sdk#run-the-toolset-safely) covers the rest.

## Limits

- `read_page` and `find` see only the main frame. Use coordinates for anything inside an iframe.
- `find` matches words and roles. It does not call a model.
- The request guard does not see WebSocket connections or service worker requests.

## Development

```bash
uv sync
uv run pytest            # unit tests, no network
uv run pytest -m live    # all 31 tools against a real Steel session (needs STEEL_API_KEY)
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through [SECURITY.md](SECURITY.md).

## License

[Apache 2.0](LICENSE). Copyright 2026 steel.dev (Nen Labs).
