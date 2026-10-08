# ABOUTME: Runs Claude's browser toolset on a Steel cloud browser: give it a task, and Claude does it in the browser.
# ABOUTME: Usage: uv run --env-file .env examples/quickstart.py "Open example.com and summarize what the page says."

from __future__ import annotations

import asyncio
import os
import sys

from anthropic import AsyncAnthropic
from steel import AsyncSteel

from steel_browser_toolset import SteelBrowser, allowed_hosts_policy

MODEL = os.getenv("MODEL", "claude-opus-5-5")
DEFAULT_TASK = "Open example.com and summarize what the page says."
# The hosts the browser may open, with their subdomains. The URL policy refuses every other address, also the
# addresses that a page or a redirect goes to.
ALLOWED_HOSTS = [host for host in os.getenv("ALLOWED_HOSTS", "example.com,iana.org").split(",") if host.strip()]


async def main() -> None:
    task = " ".join(sys.argv[1:]) or os.getenv("TASK") or DEFAULT_TASK
    for key in ("STEEL_API_KEY", "ANTHROPIC_API_KEY"):
        if not os.getenv(key):
            sys.exit(f"Set {key} in the environment, or in .env and run with `uv run --env-file .env`.")

    browser = SteelBrowser(AsyncSteel(), url_policy=allowed_hosts_policy(ALLOWED_HOSTS))
    async with browser, AsyncAnthropic() as client:
        print(f"Steel session: {browser.session.session_viewer_url}")
        print(f"Task: {task}\n")
        runner = client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=4096,
            max_iterations=50,
            tools=[browser],
            messages=[{"role": "user", "content": task}],
        )
        async for message in runner:
            for block in message.content:
                if block.type == "text":
                    print(block.text)
                elif block.type == "tool_use":
                    print(f"  -> {block.name} {block.input}")
    print(f"\nSession released. Replay: {browser.session.session_viewer_url}")


if __name__ == "__main__":
    asyncio.run(main())
