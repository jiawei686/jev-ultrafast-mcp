"""A small synchronous Chrome DevTools Protocol client.

Self-contained on purpose: the only runtime dependency is a websocket client.
Sessions are created with `Target.attachToTarget(flatten=True)` so many tabs
share one socket, and every call is a plain blocking round trip — an MCP tool
call has no concurrency to exploit and blocking keeps failures obvious.
"""

from __future__ import annotations

import itertools
import json
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from websockets.protocol import State
from websockets.sync.client import connect

from .config import Config, find_chrome


class CdpError(RuntimeError):
    """A CDP method returned an error, or the transport failed."""


class ChromeLaunchError(RuntimeError):
    """Chrome could not be started or did not expose a debugging endpoint."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _read_active_port(directory: Path) -> tuple[int, str] | None:
    """`DevToolsActivePort` as (port, websocket path), or None if unusable.

    The file is two lines: the port, then the browser endpoint path. Both the
    attach path and the reconnect path need exactly this, and both need to be
    strict about it -- a half-written file (Chrome is mid-startup) must read as
    "not ready" rather than as a port number with a garbage path.
    """
    try:
        lines = (directory / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 2 or not lines[1].startswith("/devtools/"):
        return None
    try:
        return int(lines[0].strip()), lines[1].strip()
    except ValueError:
        return None


class Cdp:
    """One websocket to a browser or page endpoint."""

    def __init__(self, ws_url: str, timeout: float = 30.0, max_size: int = 128 * 1024 * 1024,
                 open_timeout: float | None = None):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ids = itertools.count(1)
        # `open_timeout` is separate because Chrome gates each debugging client
        # behind a user-approval dialog: the handshake waits on a person, not on
        # the network, so it gets more room than the calls that follow it.
        self._ws = connect(ws_url, max_size=max_size, open_timeout=open_timeout or timeout,
                           close_timeout=5, max_queue=64)
        self.events: deque[dict] = deque(maxlen=400)

    @property
    def alive(self) -> bool:
        """Whether this socket can still carry a command.

        `websockets` never reconnects, so a browser that quit, a machine that
        slept, or a keepalive ping that went unanswered leaves this object
        holding a socket that still *looks* present and answers nothing. A
        server that keeps one of these for the life of the process therefore
        cannot use "the object exists" as a stand-in for "the browser is
        reachable" -- that is the difference between one failed call and every
        call failing forever.
        """
        return self._ws.state is State.OPEN

    def call(self, method: str, session_id: str | None = None, timeout: float | None = None, **params):
        """Issue a CDP command and return its `result` object."""
        message_id = next(self._ids)
        payload = {"id": message_id, "method": method, "params": params}
        if session_id:
            payload["sessionId"] = session_id
        try:
            self._ws.send(json.dumps(payload))
        except Exception as exc:  # transport died
            raise CdpError(f"{method}: transport closed ({exc})") from None
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpError(f"{method}: timed out after {timeout or self.timeout:.1f}s")
            try:
                raw = self._ws.recv(timeout=remaining)
            except TimeoutError:
                raise CdpError(f"{method}: timed out after {timeout or self.timeout:.1f}s") from None
            except Exception as exc:
                raise CdpError(f"{method}: transport closed ({exc})") from None
            message = json.loads(raw)
            if message.get("id") != message_id:
                if "method" in message:
                    self.events.append(message)
                continue
            if "error" in message:
                error = message["error"]
                raise CdpError(f"{method}: {error.get('message')} ({error.get('code')})")
            return message.get("result", {})

    def evaluate(self, expression: str, session_id: str, *,
                 await_promise: bool = False, timeout: float | None = None):
        """Evaluate JS and return its value. JS exceptions surface as CdpError."""
        result = self.call(
            "Runtime.evaluate",
            session_id=session_id,
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
            userGesture=True,
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            description = (
                (details.get("exception") or {}).get("description")
                or details.get("text")
                or "javascript error"
            )
            raise CdpError(str(description).splitlines()[0][:300])
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------- launch


def _await_endpoint(profile: Path, process: subprocess.Popen, timeout: float) -> tuple[int, str]:
    """Wait for `DevToolsActivePort`, but give up immediately if Chrome dies.

    Chrome aborts fast when it cannot initialise its own sandbox, and waiting
    out the full timeout on a corpse makes `sandbox="auto"` feel broken.
    """
    path = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ChromeLaunchError(f"Chrome exited during startup with code {process.returncode}")
        if path.exists():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            if len(lines) >= 2 and lines[0].strip().isdigit():
                return int(lines[0].strip()), lines[1].strip()
        time.sleep(0.05)
    raise ChromeLaunchError(f"Chrome never wrote {path}")


def _confirm_alive(port: int, process: subprocess.Popen, probes: int = 4, gap: float = 0.3) -> None:
    """Chrome writes DevToolsActivePort *before* it is out of the woods.

    Without a sandbox it can publish the port, accept a websocket, and then
    abort a moment later — which shows up much further down the stack as a
    mystery "transport closed". Confirm it is still serving before trusting it.
    """
    for attempt in range(probes):
        if process.poll() is not None:
            raise ChromeLaunchError(f"Chrome exited during startup (code {process.returncode})")
        if attempt:
            time.sleep(gap)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
                json.load(response)
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
            raise ChromeLaunchError(f"Chrome debugging endpoint stopped answering: {exc}") from None


def launch_chrome(cfg: Config, *, headless: bool | None = None,
                  allow_extensions: bool = False) -> tuple[Cdp, subprocess.Popen, Path]:
    """Start a Chromium-family browser with remote debugging and connect to it.

    `sandbox="auto"` retries with `--no-sandbox` when the browser aborts on
    startup, which is what happens inside most CI/container runtimes.

    `allow_extensions` is off by default because `--disable-extensions` is what keeps a
    developer's own extensions out of a run, and a run that reads pages should not be at the mercy
    of whatever else is installed. Turning it on is for the one caller that deliberately loads an
    extension: the flag does not merely deprioritise extensions, it blocks their pages outright, so
    a browser started with it answers a navigation to `chrome-extension://.../popup.html` with
    `ERR_BLOCKED_BY_CLIENT` and no explanation of why.
    """
    binary = find_chrome(cfg.chrome)
    headless = cfg.headless if headless is None else headless
    profile = cfg.resolved_profile()
    width, height = cfg.window

    base = [
        binary,
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
        "--disable-component-update", "--disable-sync",
    ]
    if not allow_extensions:
        base.append("--disable-extensions")
    base += [
        "--disable-features=Translate,OptimizationHints,MediaRouter",
        "--metrics-recording-only", "--password-store=basic", "--use-mock-keychain",
        "about:blank",
    ]
    if headless:
        base[1:1] = ["--headless=new", "--disable-gpu", "--disable-dev-shm-usage",
                     "--hide-scrollbars", "--mute-audio"]

    flag_sets = {
        "on": [[]],
        "off": [["--no-sandbox"]],
        "auto": [[], ["--no-sandbox"]],
    }.get(cfg.sandbox, [[], ["--no-sandbox"]])
    # Each flag set gets two tries: startup can lose a race with a busy machine,
    # and a wasted retry costs far less than a spurious "browser unavailable".
    # `auto` therefore runs plain, plain, --no-sandbox, --no-sandbox.
    attempts = [flags for flags in flag_sets for _ in range(2)]

    last_error: Exception | None = None
    for extra in attempts:
        port_file = profile / "DevToolsActivePort"
        if port_file.exists():
            port_file.unlink()
        process = subprocess.Popen(
            base + extra,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            port, ws_path = _await_endpoint(profile, process, 25.0)
            _confirm_alive(port, process)
            cdp = Cdp(f"ws://127.0.0.1:{port}{ws_path}", timeout=cfg.call_timeout)
            cdp.call("Target.setDiscoverTargets", discover=True)
            return cdp, process, profile
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next flag set"
            last_error = exc
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                process.kill()
            if extra:
                break
    raise ChromeLaunchError(f"Chrome failed to start: {last_error}")


def attach_chrome(
    url: str,
    timeout: float = 30.0,
    data_dirs: "list[Path] | None" = None,
    open_timeout: float | None = None,
) -> Cdp:
    """Connect to an already-running browser.

    Three shapes are accepted, in the order the caller is likely to have them:

      * `ws://…/devtools/browser/<id>` -- used as-is.
      * `http://127.0.0.1:9222` -- the classic `--remote-debugging-port` case,
        resolved through `/json/version`.
      * `http://127.0.0.1:9222` where `/json/version` answers 404 -- what Chrome
        144+ looks like. The debugging server started by
        `chrome://inspect/#remote-debugging` is WebSocket-only and deliberately
        serves no HTTP discovery endpoints, so a 404 there does not mean
        "nothing is listening". The port and the browser WebSocket path are in
        `DevToolsActivePort` inside the browser's data directory, which is where
        the endpoint is read from instead.

    Chrome asks the user to approve each new debugging client, so the socket is
    opened with a generous `open_timeout`: the handshake sits there until the
    approval dialog is answered rather than failing.
    """
    endpoint = url.rstrip("/")
    if endpoint.startswith(("ws://", "wss://")):
        return Cdp(endpoint, timeout=timeout, open_timeout=open_timeout)
    if not endpoint.startswith("http"):
        return Cdp(endpoint, timeout=timeout, open_timeout=open_timeout)

    ws_url = _from_json_version(endpoint, timeout)
    if ws_url is None:
        ws_url = _from_active_port(endpoint, data_dirs or [])
    if ws_url is None:
        raise ChromeLaunchError(
            f"Cannot reach a CDP endpoint at {url}. Nothing answered /json/version, and no "
            "DevToolsActivePort file was found in the usual browser data directories — so the "
            "browser either is not running with debugging enabled, or keeps its data directory "
            "somewhere else (set JEVMCP_ATTACH_PROFILE_DIR to it). If you enabled debugging with "
            "the chrome://inspect toggle, check that it still says 'Server running at'."
        )
    return Cdp(ws_url, timeout=timeout, open_timeout=open_timeout)


def _from_json_version(endpoint: str, timeout: float) -> str | None:
    """The classic discovery path. Returns None when the server has no HTTP API."""
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=timeout) as response:
            return json.load(response)["webSocketDebuggerUrl"]
    except (urllib.error.URLError, KeyError, OSError, ValueError):
        return None


def _from_active_port(endpoint: str, data_dirs: list[Path]) -> str | None:
    """Resolve the WebSocket endpoint from `DevToolsActivePort`.

    Its port has to be the one we were pointed at; otherwise it belongs to some
    other browser that happens to have run on this machine.
    """
    wanted = urllib.parse.urlparse(endpoint).port or 9222
    for directory in data_dirs:
        found = _read_active_port(directory)
        if found is None or found[0] != wanted:
            continue
        return f"ws://127.0.0.1:{found[0]}{found[1]}"
    return None


def reattach_chrome(profile: Path, timeout: float = 30.0,
                    open_timeout: float | None = None) -> Cdp:
    """Reconnect to a browser we launched that is still running.

    A socket to a browser is not the browser. A dropped connection, a slept
    machine or an unanswered keepalive leaves the process alive with its
    `DevToolsActivePort` still accurate, so the recovery is a new socket -- not
    a second browser, which would leave the first one orphaned and holding the
    profile directory.
    """
    found = _read_active_port(profile)
    if found is None:
        raise ChromeLaunchError(
            f"No running browser is recorded in {profile / 'DevToolsActivePort'}"
        )
    port, ws_path = found
    try:
        return Cdp(f"ws://127.0.0.1:{port}{ws_path}", timeout=timeout, open_timeout=open_timeout)
    except Exception as exc:  # noqa: BLE001 - any failure means "cannot reconnect"
        raise ChromeLaunchError(
            f"Cannot reconnect to the running browser on port {port}: {exc}"
        ) from None
