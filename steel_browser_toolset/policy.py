# ABOUTME: Builds a URL policy that allows http(s) addresses on a list of hosts and their subdomains.
# ABOUTME: The SDK uses it for `navigate`, and the driver uses the same policy for every request the browser makes.

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from anthropic.tools import ToolError
from anthropic.tools.browser import BetaURLContext, BetaURLPolicy

SCHEME_PREFIX = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)
WHITESPACE_OR_CONTROL = re.compile(r"[\s\x00-\x1f\x7f]")


def with_scheme(url: str) -> str:
    """The address that the browser opens: `https://` is added when the address has no scheme."""
    return url if SCHEME_PREFIX.match(url) else f"https://{url}"


def host_is_allowed(url: str, hosts: Iterable[str]) -> bool:
    """Whether `url` is an http(s) address on one of `hosts` or on a subdomain of one. `about:blank` is allowed."""
    if url.lower() == "about:blank":
        return True
    if WHITESPACE_OR_CONTROL.search(url):
        return False
    # A browser reads a backslash in a web address as a slash.
    try:
        parts = urlsplit(with_scheme(url).replace("\\", "/"))
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in hosts)


def allowed_hosts_policy(hosts: Iterable[str]) -> BetaURLPolicy:
    """A URL policy that refuses each address that `host_is_allowed` refuses.

    This is a host allowlist, not a full production policy. It does not resolve names, so a listed host that resolves
    to a private address is allowed.
    """
    allowed = [host.strip().lower().rstrip(".") for host in hosts]
    allowed = [host for host in allowed if host]
    if not allowed:
        raise ValueError("allowed_hosts_policy needs at least one host")

    def policy(_context: BetaURLContext, url: str) -> None:
        if not host_is_allowed(url, allowed):
            raise ToolError(f"blocked: {url} is not on an allowed host")

    return policy
