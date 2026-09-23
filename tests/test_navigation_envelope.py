"""Two tools can open a tab, and the domain envelope has to bind both.

`JEVMCP_ALLOW_DOMAINS` / `JEVMCP_DENY_DOMAINS` are the only thing between an
autonomous agent and a host it was told not to visit, and `browser_doctor`
advertises them as a guard. A guard that one of two call sites forgets is not a
guard: `browser_tabs(action="new")` issued `Target.createTarget` itself and never
called `check_url`, so the same URL was refused through `browser_act`'s `tab` op
and accepted through the tool -- and the tab it opened could then be switched to
and driven. `browser_tabs` was also annotated `openWorldHint=False`, which tells
a host it cannot reach a new host; that claim was false in the same way, and it
is `WRITES` now (`test_annotations.py` holds the split).

The tests below drive the real `Session` with a recording CDP, so the property
asserted is the guard firing and not whether Chrome is installed. The last two
are about the shape of the code rather than about one call -- one guarded door
per way of changing a page's address, so a third call site cannot appear quietly.

Refusing is also a message, not an exception: `browser_tabs` did not catch
`SafetyError`, so a refusal raised out of the tool as a protocol-level crash,
which a host cannot tell apart from a bug in this server.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jev_ultrafast_mcp import server
from jev_ultrafast_mcp.browser import Session, Step
from jev_ultrafast_mcp.config import Config

PACKAGE = Path(__file__).resolve().parent.parent / "jev_ultrafast_mcp"

# The commands that decide where a page is. `"Page.navigate"` is quoted so that
# `Page.navigateToHistoryEntry` -- which goes somewhere the envelope already
# allowed -- is not counted as a way of choosing a destination.
NAVIGATION_METHODS = ('"Target.createTarget"', '"Page.navigate"')

# One envelope, exercised from both sides: inside it, a wildcard inside it, and
# two kinds of outside -- the deny list, which wins over the allow list, and a
# host that is merely absent from it.
ENVELOPE = {"allow_domains": ["example.com", "*.corp.example.com"],
            "deny_domains": ["admin.example.com"]}
CASES = [
    ("https://example.com/a", False),
    ("https://api.corp.example.com/b", False),
    ("https://evil.test/a", True),
    ("https://admin.example.com/", True),
]


class RecordingCdp:
    """Answers the calls a session makes, and remembers every one."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.events: list[dict] = []

    def call(self, method: str, timeout: float | None = None, **params: object) -> dict:
        self.calls.append((method, params))
        if method == "Target.createTarget":
            return {"targetId": "tab-new"}
        if method == "Target.getTargets":
            return {"targetInfos": []}
        return {}

    def evaluate(self, expression: str, session_id: str, **kwargs: object):
        return None

    def close(self) -> None:
        pass

    def created(self) -> list[str]:
        """The URLs this session actually asked the browser to open."""
        return [params["url"] for method, params in self.calls
                if method == "Target.createTarget"]


class StubManager:
    """`server._session` goes through this; only `session()` is reached."""

    def __init__(self, session: Session) -> None:
        self.one = session

    def session(self, name: str) -> Session:
        return self.one


def _session(**cfg: object) -> tuple[Session, RecordingCdp]:
    driver = RecordingCdp()
    return Session("default", Config(**cfg), driver), driver


def _through_the_tool(url: str, **cfg: object) -> tuple[str, list[str]]:
    """`browser_tabs(action="new")`, and what it asked the browser for."""
    session, driver = _session(**cfg)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "MANAGER", StubManager(session))
        patch.setattr(server, "CONFIG", session.cfg)
        message = server.browser_tabs(action="new", url=url)
    return message, driver.created()


def _through_the_tab_op(url: str, **cfg: object) -> tuple[Step, list[str]]:
    """The same thing through `browser_act`'s `tab` op."""
    session, driver = _session(**cfg)
    step = session._run_op({"op": "tab", "action": "new", "url": url}, dry_run=False)
    return step, driver.created()


@pytest.mark.parametrize("url,refused", CASES)
def test_the_two_doors_reach_the_same_verdict(url: str, refused: bool) -> None:
    message, tool_opened = _through_the_tool(url, **ENVELOPE)
    step, op_opened = _through_the_tab_op(url, **ENVELOPE)

    assert tool_opened == op_opened, f"{url}: the two doors disagree about opening"
    if refused:
        assert tool_opened == [], f"{url}: opened outside the envelope"
        assert message.startswith("blocked_by_policy:"), message
        assert step.ok is False and step.error == "blocked_by_policy"
    else:
        assert tool_opened == [url], f"{url}: the envelope refused an allowed host"
        assert step.ok is True, step.detail


def test_a_refusal_is_reported_rather_than_raised() -> None:
    """An uncaught `SafetyError` reaches the host as a server bug."""
    message, opened = _through_the_tool("https://evil.test/", **ENVELOPE)
    assert opened == []
    assert "outside JEVMCP_ALLOW_DOMAINS" in message


def test_an_opened_tab_is_the_sessions_own_kind_of_tab() -> None:
    """`background` is the session's, not a second reading of the config.

    The deleted copy passed `not CONFIG.foreground`; the value agreed, which is
    why nothing caught it. Asserted on the session field so a session built with
    a different `background` from the global config stays consistent with
    `_attach_page`.
    """
    session, driver = _session(**ENVELOPE)
    session.background = False
    session.open_tab("https://example.com/")
    params = [p for method, p in driver.calls if method == "Target.createTarget"]
    assert params == [{"url": "https://example.com/", "background": False}]


def test_the_server_layer_never_talks_cdp_to_choose_a_destination() -> None:
    """`server.py` drives a session; the guarded door lives in `browser.py`.

    This is the shape the bug had: the tool reached past `Session` to
    `tab.cdp.call("Target.createTarget", ...)`, and with it went the guard.
    """
    source = (PACKAGE / "server.py").read_text(encoding="utf-8")
    for method in NAVIGATION_METHODS:
        assert method not in source, (
            f"server.py issues {method} itself; navigation belongs behind "
            "Session.open_tab / Session.navigate, which enforce the envelope"
        )


def test_one_guarded_door_creates_a_target_and_one_navigates() -> None:
    """Exactly one method per way of changing the address, and each checks."""
    source = (PACKAGE / "browser.py").read_text(encoding="utf-8")
    starts = [(match.start(), match.group(1))
              for match in re.finditer(r"^    def (\w+)\(", source, re.M)]
    bodies: dict[str, str] = {}
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(source)
        bodies[name] = source[start:end]

    for method in NAVIGATION_METHODS:
        callers = {name for name, body in bodies.items() if method in body}
        assert len(callers) == 1, f"{method} is issued from {sorted(callers)}"
        door = callers.pop()
        assert "check_url(self.cfg" in bodies[door], (
            f"{door} issues {method} without enforcing the domain envelope"
        )
