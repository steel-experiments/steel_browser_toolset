# ABOUTME: Unit tests for the host allowlist URL policy that the SDK and the request guard share.
# ABOUTME: They need no browser and no network.

import pytest
from anthropic.tools import ToolError
from anthropic.tools.browser import BetaURLContext

from steel_browser_toolset.policy import allowed_hosts_policy, host_is_allowed, with_scheme

HOSTS = ["example.com", "iana.org"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "example.com/path",
        "http://www.example.com/a?b=c",
        "https://EXAMPLE.com.",
        "about:blank",
        "https://iana.org/domains",
    ],
)
def test_allowed(url):
    assert host_is_allowed(url, HOSTS)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com",
        "https://example.com.evil.com",
        "https://notexample.com",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,hi",
        "view-source:https://example.com",
        "ftp://example.com",
        " https://example.com",
        "https://example.com\n.evil.com",
        "https://evil.com\\@example.com",
        "http://169.254.169.254/latest",
    ],
)
def test_refused(url):
    assert not host_is_allowed(url, HOSTS)


def test_policy_raises_tool_error_with_the_address():
    policy = allowed_hosts_policy(HOSTS)
    policy(BetaURLContext(), "example.com")
    with pytest.raises(ToolError, match="blocked: https://evil.com is not on an allowed host"):
        policy(BetaURLContext(), "https://evil.com")


def test_policy_needs_a_host():
    with pytest.raises(ValueError):
        allowed_hosts_policy([" ", ""])


def test_with_scheme():
    assert with_scheme("example.com") == "https://example.com"
    assert with_scheme("http://example.com") == "http://example.com"
