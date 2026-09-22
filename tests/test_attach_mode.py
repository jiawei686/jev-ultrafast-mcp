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

import os
import shutil
import subprocess
import time

import pytest

from jev_ultrafast_mcp import cdp, server
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
    manager._cdp = fake  # the point is to exercise shutdown without a browser
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


# ------------------------------------------------- stopping a browser we started
#
# The other half of the property above. In `launch` mode the browser is ours, and
# stopping it means stopping all of it: `launch_chrome` passes
# `start_new_session=True`, so Chrome leads its own process group and its
# renderers, GPU process and utility processes are in that group with it.
# Signalling only the leader leaves the rest running. Measured after a session of
# extension checks: 49 browsers and 392 processes still alive, each holding a
# temporary profile open, none of them reachable from the handle that started it.


def _group_members(group: int) -> list[int]:
    """Every live process in `group`. `ps` is not permitted in this sandbox."""
    result = subprocess.run(["pgrep", "-g", str(group)], capture_output=True, text=True)
    return [int(line) for line in result.stdout.split()]


requires_pgrep = pytest.mark.skipif(
    shutil.which("pgrep") is None, reason="needs pgrep to observe the process group")


@requires_pgrep
def test_stopping_a_browser_reclaims_the_whole_process_group() -> None:
    """The leader is not the browser. Its children have to go too.

    `sh -c "sleep 300 & sleep 300"` stands in for the shape of a real browser: one
    process that forks others and waits. `terminate()` on the leader -- what this
    used to do -- leaves the forked processes running, which is how a run that
    looked like it had cleaned up left 392 processes behind.
    """
    process = subprocess.Popen(
        ["sh", "-c", "sleep 300 & sleep 300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    group = os.getpgid(process.pid)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(_group_members(group)) < 2:
            time.sleep(0.05)
        assert len(_group_members(group)) >= 2, "the fixture never forked a child"

        cdp.stop_chrome(process)

        assert process.poll() is not None, "the leader outlived the stop"
        assert _group_members(group) == [], "a process in the browser's group survived"
    finally:
        if process.poll() is None:
            os.killpg(group, 9)


def test_stopping_a_browser_that_already_exited_is_a_no_op() -> None:
    """`atexit` and an explicit `browser_close` can both land here."""
    process = subprocess.Popen(["true"])
    process.wait(timeout=5)
    cdp.stop_chrome(process)  # must not raise


def test_stop_chrome_never_signals_its_own_process_group() -> None:
    """The guard that stops a caller from killing itself.

    `stop_chrome` signals the browser's process *group*. If a caller ever launches
    without `start_new_session`, that group is the caller's own -- and the SIGTERM
    meant for a browser would take down the process that sent it. A child left
    deliberately in our group pins the guard: reaching the end of this test at all
    is the proof that the group was not signalled.
    """
    process = subprocess.Popen(["sleep", "300"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    assert os.getpgid(process.pid) == os.getpgid(0), "the fixture is not in our group"

    cdp.stop_chrome(process)

    assert process.poll() is not None, "the child was left running"
