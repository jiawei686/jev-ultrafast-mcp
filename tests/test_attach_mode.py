"""What attach mode must never do to the browser it did not start.

`launch` and `attach` look like a one-line config difference, but they differ in
who the browser belongs to. In `launch` the browser is ours: it starts on the
first `browser_open`, and stopping it on the way out is housekeeping.

In `attach` the browser is the user's own -- their tabs, their logins, possibly
their work. `Browser.close` is not housekeeping there, it is quitting every
window they have open, and the caller reached it by a call that reads like
"close this session's tab". `shutdown` is also wired to `atexit`, so the same
mistake fires when the server exits for any reason.

So the property is: in attach mode, `shutdown()` detaches and never stops the
browser, and `browser_close` says so instead of claiming "browser stopped".
"""

from __future__ import annotations

from jev_ultrafast_mcp import server
from jev_ultrafast_mcp.browser import BrowserManager
from jev_ultrafast_mcp.config import Config


class FakeCdp:
    """Records the CDP methods the manager issues, without a browser."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    def call(self, method: str, timeout: float | None = None, **params: object) -> dict:
        self.calls.append(method)
        return {}

    def close(self) -> None:
        self.closed = True


def _manager(mode: str, cdp_url: str | None = None) -> tuple[BrowserManager, FakeCdp]:
    manager = BrowserManager(Config(mode=mode, cdp_url=cdp_url))
    fake = FakeCdp()
    manager._cdp = fake  # noqa: SLF001 - the point is to exercise shutdown without a browser
    return manager, fake


def test_attach_mode_does_not_own_the_browser() -> None:
    manager, _ = _manager("attach", "http://127.0.0.1:9222")
    assert manager.owns_browser is False


def test_launch_mode_owns_the_browser() -> None:
    manager, _ = _manager("launch")
    assert manager.owns_browser is True


def test_attach_shutdown_detaches_instead_of_quitting_the_users_browser() -> None:
    manager, fake = _manager("attach", "http://127.0.0.1:9222")
    manager.shutdown()
    assert "Browser.close" not in fake.calls, "attach mode quit the user's browser"
    assert fake.closed is True, "the socket to the user's browser was left open"
    assert manager._cdp is None


def test_launch_shutdown_still_stops_the_browser_it_started() -> None:
    manager, fake = _manager("launch")
    manager.shutdown()
    assert "Browser.close" in fake.calls


def test_attach_shutdown_is_safe_to_run_twice() -> None:
    """`atexit` and an explicit `browser_close` can both land here."""
    manager, fake = _manager("attach", "http://127.0.0.1:9222")
    manager.shutdown()
    manager.shutdown()
    assert "Browser.close" not in fake.calls


def test_browser_close_reports_detaching_when_it_does_not_own_the_browser(
    monkeypatch,
) -> None:
    manager, _ = _manager("attach", "http://127.0.0.1:9222")
    monkeypatch.setattr(server, "MANAGER", manager)
    monkeypatch.setattr(server, "CONFIG", manager.cfg)
    message = server.browser_close(session="default", shutdown_browser=True)
    assert "detached" in message
    assert "still running" in message
    assert "browser stopped" not in message, "attach mode claimed it stopped the browser"


def test_browser_close_still_reports_stopping_what_it_started(monkeypatch) -> None:
    manager, _ = _manager("launch")
    monkeypatch.setattr(server, "MANAGER", manager)
    monkeypatch.setattr(server, "CONFIG", manager.cfg)
    assert server.browser_close(session="default", shutdown_browser=True).endswith(
        "browser stopped"
    )


def test_doctor_hint_does_not_promise_to_launch_in_attach_mode(monkeypatch) -> None:
    manager = BrowserManager(Config(mode="attach", cdp_url="http://127.0.0.1:9222"))
    monkeypatch.setattr(server, "MANAGER", manager)
    monkeypatch.setattr(server, "CONFIG", manager.cfg)
    report = server.browser_doctor()
    assert "JEVMCP_CDP_URL" in report
    assert "launches on the first browser_open" not in report
