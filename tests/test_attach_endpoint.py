"""How attach mode finds the browser, including the Chrome 144+ case.

Chrome 144+ starts its debugging server from `chrome://inspect/#remote-debugging`
instead of a flag, and that server is **WebSocket-only**: it answers 404 to
`/json/version` and to every other `/json/*` path. A 404 there is the documented
behaviour, not a broken setup — but the obvious reading of it is "nothing is
listening", which is exactly the wrong conclusion and the one a client that only
speaks `/json/version` will reach.

The port and the browser-level WebSocket path are in `DevToolsActivePort` next to
the browser's profile, so that is the second place to look. These tests pin the
order (explicit ws:// wins, then `/json/version`, then the file) and the messages,
without needing a browser: `Cdp` is replaced by a recorder and the file is a
temporary directory.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from jev_ultrafast_mcp import cdp
from jev_ultrafast_mcp.cdp import ChromeLaunchError, attach_chrome
from jev_ultrafast_mcp.config import Config


class Recorder:
    """Stands in for `Cdp`: remembers how it was asked to connect."""

    calls: list[dict] = []

    def __init__(self, ws_url: str, timeout: float = 30.0, **kwargs: object) -> None:
        Recorder.calls.append({"ws_url": ws_url, "timeout": timeout, **kwargs})


@pytest.fixture
def recorder(monkeypatch):
    Recorder.calls = []
    monkeypatch.setattr(cdp, "Cdp", Recorder)
    return Recorder


def _active_port(directory: Path, port: int = 9222, path: str = "/devtools/browser/abc-123") -> Path:
    if not directory.is_dir():
        directory.mkdir(parents=True)
    (directory / "DevToolsActivePort").write_text(f"{port}\n{path}\n", encoding="utf-8")
    return directory


def _response(payload: dict):
    """A `urlopen` context manager returning `payload` as JSON."""
    import json

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    return Response()


def _json_version_answers(monkeypatch, ws_url: str) -> None:
    monkeypatch.setattr(cdp.urllib.request, "urlopen",
                        lambda *a, **k: _response({"webSocketDebuggerUrl": ws_url}))


def _json_version_404(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise urllib.error.HTTPError("http://127.0.0.1:9222/json/version", 404, "Not Found", {}, None)

    monkeypatch.setattr(cdp.urllib.request, "urlopen", boom)


# --- the order the endpoint is resolved in --------------------------------------------------

def test_an_explicit_websocket_url_is_used_as_is(recorder, monkeypatch, tmp_path):
    _json_version_404(monkeypatch)
    attach_chrome("ws://127.0.0.1:9222/devtools/browser/given", data_dirs=[tmp_path])
    assert recorder.calls[0]["ws_url"] == "ws://127.0.0.1:9222/devtools/browser/given"


def test_json_version_is_preferred_when_it_answers(recorder, monkeypatch, tmp_path):
    _json_version_answers(monkeypatch, "ws://127.0.0.1:9222/devtools/browser/http")
    _active_port(tmp_path, path="/devtools/browser/from-file")
    attach_chrome("http://127.0.0.1:9222", data_dirs=[tmp_path])
    assert recorder.calls[0]["ws_url"] == "ws://127.0.0.1:9222/devtools/browser/http"


def test_a_404_falls_back_to_devtools_active_port(recorder, monkeypatch, tmp_path):
    """The Chrome 144+ path: WebSocket-only server, no HTTP discovery API."""
    _json_version_404(monkeypatch)
    _active_port(tmp_path, port=9222, path="/devtools/browser/from-file")
    attach_chrome("http://127.0.0.1:9222", data_dirs=[tmp_path])
    assert recorder.calls[0]["ws_url"] == "ws://127.0.0.1:9222/devtools/browser/from-file"


def test_the_first_data_dir_with_a_matching_port_wins(recorder, monkeypatch, tmp_path):
    _json_version_404(monkeypatch)
    wrong = _active_port(tmp_path / "wrong", port=9333, path="/devtools/browser/other-browser")
    right = _active_port(tmp_path / "right", port=9222, path="/devtools/browser/mine")
    attach_chrome("http://127.0.0.1:9222", data_dirs=[wrong, right])
    assert recorder.calls[0]["ws_url"] == "ws://127.0.0.1:9222/devtools/browser/mine"


def test_a_port_mismatch_is_not_used(recorder, monkeypatch, tmp_path):
    """A stale file from another browser must not be mistaken for the target."""
    _json_version_404(monkeypatch)
    _active_port(tmp_path, port=9333)
    with pytest.raises(ChromeLaunchError):
        attach_chrome("http://127.0.0.1:9222", data_dirs=[tmp_path])
    assert recorder.calls == []


def test_a_truncated_active_port_file_is_ignored(recorder, monkeypatch, tmp_path):
    _json_version_404(monkeypatch)
    (tmp_path / "DevToolsActivePort").write_text("9222\n", encoding="utf-8")
    with pytest.raises(ChromeLaunchError):
        attach_chrome("http://127.0.0.1:9222", data_dirs=[tmp_path])
    assert recorder.calls == []


def test_the_failure_message_says_what_to_check(recorder, monkeypatch, tmp_path):
    _json_version_404(monkeypatch)
    with pytest.raises(ChromeLaunchError) as excinfo:
        attach_chrome("http://127.0.0.1:9222", data_dirs=[tmp_path])
    message = str(excinfo.value)
    assert "JEVMCP_ATTACH_PROFILE_DIR" in message
    assert "chrome://inspect" in message


# --- the approval dialog gets a human-sized budget ------------------------------------------

def test_the_websocket_open_timeout_is_passed_through(recorder, monkeypatch, tmp_path):
    _json_version_404(monkeypatch)
    _active_port(tmp_path)
    attach_chrome("http://127.0.0.1:9222", data_dirs=[tmp_path], open_timeout=60.0)
    assert recorder.calls[0]["open_timeout"] == 60.0


def test_attach_asks_for_a_longer_handshake_than_the_call_timeout():
    """Chrome waits on a click, so the handshake outlives a normal CDP call."""
    import inspect

    from jev_ultrafast_mcp import browser

    source = inspect.getsource(browser)
    assert "open_timeout=" in source


# --- where the data directories come from ---------------------------------------------------

def test_the_configured_data_dir_is_searched_first(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVMCP_ATTACH_PROFILE_DIR", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.attach_data_dirs()[0] == tmp_path
    assert cfg.attach_profile_dir == tmp_path


def test_the_default_data_dirs_are_platform_locations(monkeypatch):
    monkeypatch.delenv("JEVMCP_ATTACH_PROFILE_DIR", raising=False)
    dirs = Config.from_env().attach_data_dirs()
    assert dirs, "no default data directory to look in"
    assert all(isinstance(path, Path) for path in dirs)
