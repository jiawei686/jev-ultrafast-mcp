"""There are two ways to end up driving a page, and they should not drift apart.

A `Session` reaches a page either by attaching a session to a target it just
created (`_attach_page`, reached through `browser_open`) or by attaching a
session to a tab that already exists (`switch_tab`). CDP documents the settings
they share -- `Emulation.setDeviceMetricsOverride`,
`Emulation.setFocusEmulationEnabled`,
`Page.addScriptToEvaluateOnNewDocument` -- as scoped to the session that asked
for them, which makes the second route look like it must re-issue them.

Measured on this build, it does not have to. With `switch_tab` skipping the block
entirely, a switched-to tab still reported `window.innerWidth` as `cfg.window`
(1280, against `launch_chrome`'s own `--window-size`), still answered
`document.hasFocus()` true, still ran rAF, and still fired a document-start
script after navigating itself. Chrome applies these to the *target*, so a
re-attach inherits them.

So these tests do not guard a bug that exists. They guard the shape of the code
that would hide one: the two routes share `_prepare_page`, and the day somebody
adds a fourth setting to it, this file is what says the sharing is deliberate
rather than incidental. The property asserted is therefore about the commands
issued, not about pixels -- pixels already agree, which is exactly why a
regression here would be silent.

No browser is involved: the property is which CDP commands a session issues, and
a test that needs a browser stops running the moment nobody has one.
"""

from __future__ import annotations

from jev_ultrafast_mcp.browser import HELPER_VERSION, Session
from jev_ultrafast_mcp.config import Config

# The commands that describe how a session sees and renders a page. Compared as
# a list, in order, with their parameters: the same set in a different order is
# still a different session, and an argument that quietly changed from
# `cfg.window` to a constant is the kind of edit this file exists to catch.
SETUP_METHODS = {
    "Page.enable",
    "Runtime.enable",
    "Emulation.setDeviceMetricsOverride",
    "Emulation.setFocusEmulationEnabled",
    "Page.addScriptToEvaluateOnNewDocument",
}


class RecordingCdp:
    """Answers the handful of CDP calls a session makes, and remembers them."""

    def __init__(self, tabs: list[str]) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._tabs = tabs
        self._sessions = 0

    def call(self, method: str, timeout: float | None = None, **params: object) -> dict:
        self.calls.append((method, params))
        if method == "Target.createTarget":
            return {"targetId": self._tabs[0]}
        if method == "Target.attachToTarget":
            self._sessions += 1
            return {"sessionId": f"session-{self._sessions}"}
        if method == "Target.getTargets":
            return {"targetInfos": [
                {"targetId": name, "type": "page", "url": "about:blank", "title": name}
                for name in self._tabs
            ]}
        return {}

    def evaluate(self, expression: str, session_id: str, **kwargs: object):
        # `_ensure_helper` compares this against HELPER_VERSION to decide whether
        # the page still needs the script; answering with the current version
        # keeps the recorded call list to the setup commands under test.
        self.calls.append(("Runtime.evaluate", {"session_id": session_id}))
        return {"result": {"value": HELPER_VERSION}}

    def close(self) -> None:
        self.calls.append(("close", {}))

    def setup_calls(self) -> list[tuple[str, dict]]:
        return [call for call in self.calls if call[0] in SETUP_METHODS]


def _retargeted(calls: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """The setup commands with the session id blanked out.

    A switch attaches a *new* session, so the id is expected to differ and is not
    part of the comparison. Which session the commands name is asserted
    separately, below -- configuring the session we just left would leave the
    live one exactly as unconfigured as before.
    """
    return [
        (method, {key: value for key, value in params.items() if key != "session_id"})
        for method, params in calls
    ]


def _session(tabs: list[str] | None = None, **cfg: object) -> tuple[Session, RecordingCdp]:
    driver = RecordingCdp(tabs or ["tab-a", "tab-b"])
    return Session("test", Config(**cfg), driver), driver


def test_a_switched_to_tab_is_prepared_exactly_like_a_new_one():
    """The two routes into a page must issue the same setup, in the same order."""
    session, driver = _session()
    session.start()
    attached = driver.setup_calls()

    driver.calls.clear()
    session.switch_tab(target_id="tab-b")
    switched = driver.setup_calls()

    assert attached, "the attach path issued no setup commands; the fixture is wrong"
    assert _retargeted(switched) == _retargeted(attached)
    assert {params["session_id"] for _, params in switched} == {session.page_session}


def test_the_setup_is_applied_at_the_configured_window_size():
    """The one parameter worth naming: the viewport is read from the config.

    Not a consequence test -- the viewport is right either way, because
    `launch_chrome` passes `--window-size`. It is here so that a future edit
    which hardcodes a size, or reads the browser's own, fails on a line that
    says which number it should have been.
    """
    session, driver = _session(window=(1024, 768))
    session.start()
    driver.calls.clear()
    session.switch_tab(target_id="tab-b")

    metrics = [p for m, p in driver.setup_calls() if m == "Emulation.setDeviceMetricsOverride"]
    assert len(metrics) == 1
    assert (metrics[0]["width"], metrics[0]["height"]) == (1024, 768)


def test_switching_tabs_does_not_bring_a_window_to_the_front():
    """The one setup step that must *not* be shared.

    `_attach_page` activates its target only when `background` is false, because
    creating a tab is our own doing and we may owe it the foreground. A switch is
    not: in attach mode the tabs belong to the user, and reaching for one through
    `browser_open` must not rearrange their desktop. Pinned here so that an edit
    which "completes" the symmetry by moving activation into the shared block
    fails loudly instead of quietly stealing focus.
    """
    session, driver = _session()
    session.start()
    driver.calls.clear()
    session.switch_tab(target_id="tab-b")

    assert [m for m, _ in driver.calls if m == "Target.activateTarget"] == []
