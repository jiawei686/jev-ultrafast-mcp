"""Guardrails. An MCP server hands browser control to an autonomous agent, so
the blast radius has to be bounded by the server, not by prompt text."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .config import Config


class SafetyError(RuntimeError):
    """Raised when a requested action is outside the configured envelope."""


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _matches(host: str, pattern: str) -> bool:
    pattern = pattern.lower().strip()
    if not pattern:
        return False
    if pattern.startswith("*."):
        return host == pattern[2:] or host.endswith(pattern[1:])
    return host == pattern or host.endswith("." + pattern)


def check_url(cfg: Config, url: str) -> None:
    """Reject navigation outside the configured domain envelope."""
    if url.startswith(("about:", "data:", "blob:")):
        return
    host = _host(url)
    if not host:
        raise SafetyError(f"Refusing to navigate to an unparseable URL: {url!r}")
    if any(_matches(host, pattern) for pattern in cfg.deny_domains):
        raise SafetyError(f"Domain {host!r} is on JEVMCP_DENY_DOMAINS.")
    if cfg.allow_domains and not any(_matches(host, pattern) for pattern in cfg.allow_domains):
        raise SafetyError(
            f"Domain {host!r} is outside JEVMCP_ALLOW_DOMAINS "
            f"({', '.join(cfg.allow_domains)}). That list is a setting, not a default — "
            "browser_doctor reports the whole envelope."
        )


def is_secret(cfg: Config, name: str, role: str) -> bool:
    """True when a field's value must never be echoed back to the agent."""
    if role and role.lower() == "password":
        return True
    haystack = (name or "").lower()
    return any(re.search(pattern, haystack, re.IGNORECASE) for pattern in cfg.secret_patterns)


def confirm_reason(cfg: Config, name: str, role: str) -> str | None:
    """Return why a click needs explicit confirmation, or None if it is safe."""
    if role and role.lower() not in {"button", "link", "menuitem", "tab"}:
        return None
    haystack = (name or "").lower()
    for pattern in cfg.confirm_patterns:
        if re.search(pattern, haystack, re.IGNORECASE):
            return f"matches confirmation rule {pattern!r}"
    return None


def redact(cfg: Config, value: str) -> str:
    """Never let a secret value cross the wire, even if it was typed by the agent."""
    if not value:
        return value
    return "*" * min(len(value), 12)
