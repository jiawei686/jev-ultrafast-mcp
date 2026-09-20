"""A socket is not a browser: what happens when the one we hold goes dead.

`websockets` never reconnects. So when Chrome quits, the machine sleeps, or a
keepalive ping goes unanswered, the `Cdp` object the server has been holding
goes on existing while answering nothing. That makes "the object is not None" a
lie about reachability, and it is the expensive kind: the first dropped socket
becomes a permanently wedged server, every later call fails with a bare
`transport closed`, and the only way out is restarting the process.

The property these tests pin is that a dead socket is dropped and replaced, and
that replacing it does not cost the user anything -- in attach mode their
browser is never touched, and in launch mode the browser we started is adopted
rather than a second one started beside it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from websockets.protocol import State

from jev_ultrafast_mcp import browser as browser_module
from jev_ultrafast_mcp import cdp as cdp_module
from jev_ultrafast_mcp import server
from jev_ultrafast_mcp.browser import BrowserManager
from jev_ultrafast_mcp.cdp import Cdp, ChromeLaunchError, _read_active_port, reattach_chrome
from jev_ultrafast_mcp.config import Config


class Socket:
    """A `Cdp` stand-in whose liveness and recorded calls we control."""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.closed = False
        self.calls: list[str] = []

    def call(self, method: str, timeout: float | None = None, **params: object) -> dict:
        self.calls.append(method)
        return {"product": "Chrome/153.0.8010.53", "protocolVersion": "1.3"}

    def close(self) -> None:
        self.closed = True


class Recorder:
    """Stands in for `Cdp` while it is being constructed."""

    calls: list[dict] = []

    def __init__(self, ws_url: str, timeout: float = 30.0, **kwargs: object) -> None:
        Recorder.calls.append({"ws_url": ws_url, "timeout": timeout, **kwargs})


class LiveProcess:
    def poll(self) -> None:
        return None


class ExitedProcess:
    def poll(self) -> int:
        return 1


def _profile_with_active_port(directory: Path, port: int = 9222,
                              path: str = "/devtools/browser/from-file") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "DevToolsActivePort").write_text(f"{port}\n{path}\n", encoding="utf-8")
    return directory


def _attach_manager(monkeypatch, tmp_path: Path) -> BrowserManager:
    """An attach-mode manager whose browser is found through `tmp_path` only.

    `/json/version` is made to answer 404 so the resolution lands on the
    `DevToolsActivePort` file, which keeps the test off the machine's real
    Chrome -- and reproduces the Chrome 144+ shape while it is there.
    """
    import urllib.error

    def _404(*_args: object, **_kwargs: object):
        raise urllib.error.HTTPError("http://127.0.0.1:9222/json/version", 404, "Not Found", {}, None)

    monkeypatch.setattr(cdp_module.urllib.request, "urlopen", _404)
    return BrowserManager(Config(mode="attach", cdp_url="http://127.0.0.1:9222",
                                 attach_profile_dir=tmp_path))


@pytest.fixture
def fresh_socket(monkeypatch) -> Socket:
    """Every construction of a `Cdp` yields one controllable stand-in."""
    socket = Socket()
    monkeypatch.setattr(cdp_module, "Cdp", lambda *a, **k: socket)
    return socket


# --- liveness is a property of the socket, not of the object existing ---------

def test_alive_tracks_the_websocket_state() -> None:
    cdp = Cdp.__new__(Cdp)  # no connection made: this is about the state check

    class Ws:
        state = State.OPEN

    cdp._ws = Ws()
    assert cdp.alive is True

    Ws.state = State.CLOSED
    assert cdp.alive is False, "a closed socket still reported itself usable"


def test_a_live_socket_is_reused_rather_than_rebuilt(monkeypatch, tmp_path) -> None:
    manager = _attach_manager(monkeypatch, tmp_path)
    live = Socket()
    manager._cdp = live
    monkeypatch.setattr(cdp_module, "Cdp", lambda *a, **k: pytest.fail("reconnected anyway"))
    assert manager.cdp is live


# --- a dead socket is dropped and replaced -----------------------------------

def test_the_manager_rebuilds_a_socket_that_died(fresh_socket, monkeypatch, tmp_path) -> None:
    manager = _attach_manager(monkeypatch, tmp_path)
    _profile_with_active_port(tmp_path)
    dead = Socket(alive=False)
    manager._cdp = dead

    assert manager.cdp is fresh_socket
    assert dead.closed is True, "the dead socket was left open"


def test_the_old_sessions_do_not_survive_the_socket(fresh_socket, monkeypatch, tmp_path) -> None:
    """A session holds target ids that only exist on the socket it came from."""
    manager = _attach_manager(monkeypatch, tmp_path)
    _profile_with_active_port(tmp_path)
    manager._cdp = Socket(alive=False)
    manager._sessions["default"] = object()  # any session at all

    manager.cdp
    assert manager._sessions == {}, "a session outlived the socket it was bound to"


def test_the_rebuild_does_not_cost_a_second_handshake_when_one_is_unnecessary() -> None:
    """`open_timeout` exists because Chrome's approval dialog waits on a person.

    A reattach after a dropped socket is the same shape of wait (Chrome asks
    again), so the budget has to be the human-sized one and not the call
    timeout -- otherwise the reconnect times out on the very dialog it needs
    answered. Same source-level check as the initial attach, pinned separately
    so a refactor of one does not silently relax the other.
    """
    import inspect

    source = inspect.getsource(browser_module.BrowserManager._connect)
    assert "open_timeout=" in source


# --- launch mode adopts its own browser instead of starting another ----------

def test_launch_mode_adopts_the_browser_it_already_started(monkeypatch, tmp_path) -> None:
    manager = BrowserManager(Config(mode="launch"))
    manager._cdp = Socket(alive=False)
    manager._profile = tmp_path
    manager._process = LiveProcess()
    adopted = Socket()

    monkeypatch.setattr(browser_module, "reattach_chrome", lambda *a, **k: adopted)
    monkeypatch.setattr(browser_module, "launch_chrome",
                        lambda *a, **k: pytest.fail("started a second browser"))

    assert manager.cdp is adopted


def test_a_browser_that_exited_is_not_adopted(monkeypatch, tmp_path) -> None:
    manager = BrowserManager(Config(mode="launch"))
    manager._cdp = Socket(alive=False)
    manager._profile = tmp_path
    manager._process = ExitedProcess()
    launched = Socket()

    monkeypatch.setattr(browser_module, "reattach_chrome",
                        lambda *a, **k: pytest.fail("adopted a browser that had exited"))
    monkeypatch.setattr(browser_module, "launch_chrome",
                        lambda *a, **k: (launched, LiveProcess(), tmp_path))

    assert manager.cdp is launched


# --- doctor says which of the three states it is in --------------------------

def test_doctor_calls_a_dropped_socket_dropped(monkeypatch, tmp_path) -> None:
    manager = _attach_manager(monkeypatch, tmp_path)
    manager._cdp = Socket(alive=False)
    monkeypatch.setattr(cdp_module, "Cdp", lambda *a, **k: pytest.fail("doctor connected"))

    report = manager.doctor()
    assert report["connected"] is False
    assert report["connection"] == "dropped"
    assert "reconnect" in report["browser_error"]


def test_doctor_calls_a_manager_that_never_connected_idle() -> None:
    report = BrowserManager(Config(mode="attach", cdp_url="http://127.0.0.1:9222")).doctor()
    assert report["connected"] is False
    assert report["connection"] == "idle"
    assert "browser_error" not in report


def test_doctor_still_names_the_browser_when_the_socket_is_healthy() -> None:
    manager = BrowserManager(Config(mode="attach", cdp_url="http://127.0.0.1:9222"))
    manager._cdp = Socket()
    report = manager.doctor()
    assert report["connected"] is True
    assert report["connection"] == "attached"
    assert report["browser"] == "Chrome/153.0.8010.53"


def test_the_dropped_socket_hint_does_not_send_you_to_reconfigure(monkeypatch) -> None:
    """The setup is fine, so the "point me at a browser" hint is the wrong one."""
    manager = BrowserManager(Config(mode="attach", cdp_url="http://127.0.0.1:9222"))
    manager._cdp = Socket(alive=False)
    monkeypatch.setattr(server, "MANAGER", manager)
    monkeypatch.setattr(server, "CONFIG", manager.cfg)

    hints = " ".join(json.loads(server.browser_doctor())["hints"])
    assert "JEVMCP_CDP_URL" not in hints, "sent the caller to fix a config that was never wrong"
    assert "fresh socket" in hints
    assert "second one" in hints, "the reconnect has to promise it will not start a second browser"


# --- reading the recorded endpoint -------------------------------------------

def test_a_half_written_active_port_file_reads_as_not_ready(tmp_path) -> None:
    """Chrome writes the port first; the path arrives a moment later."""
    (tmp_path / "DevToolsActivePort").write_text("9222\n", encoding="utf-8")
    assert _read_active_port(tmp_path) is None


def test_an_active_port_file_with_a_non_numeric_port_is_ignored(tmp_path) -> None:
    (tmp_path / "DevToolsActivePort").write_text("pórt\n/devtools/browser/x\n", encoding="utf-8")
    assert _read_active_port(tmp_path) is None


def test_reattach_uses_the_port_the_browser_recorded(monkeypatch, tmp_path) -> None:
    Recorder.calls = []
    monkeypatch.setattr(cdp_module, "Cdp", Recorder)
    _profile_with_active_port(tmp_path, port=9333, path="/devtools/browser/xyz")
    reattach_chrome(tmp_path)
    assert Recorder.calls[0]["ws_url"] == "ws://127.0.0.1:9333/devtools/browser/xyz"


def test_reattach_refuses_a_profile_with_no_running_browser(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cdp_module, "Cdp", Recorder)
    with pytest.raises(ChromeLaunchError) as excinfo:
        reattach_chrome(tmp_path)
    assert "DevToolsActivePort" in str(excinfo.value)


def test_reattach_does_not_hold_a_socket_to_a_dead_port(monkeypatch, tmp_path) -> None:
    """A port the browser has since released is a connection error, not a socket."""
    def refuse(*_args: object, **_kwargs: object):
        raise ConnectionRefusedError("nobody listening")

    monkeypatch.setattr(cdp_module, "Cdp", refuse)
    _profile_with_active_port(tmp_path, port=9222)
    with pytest.raises(ChromeLaunchError) as excinfo:
        reattach_chrome(tmp_path)
    assert "9222" in str(excinfo.value)
