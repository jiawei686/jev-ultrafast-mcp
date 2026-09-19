"""Load the extension into a real Chrome and check its table against the server's.

`tests/test_extension.py` proves the port matches Python, but it cannot prove the parts that only
exist inside a browser: that the manifest is accepted, that the vendored observer installs in a real
page, that `popup.js` runs and reaches `chrome.scripting`, or that the popup's three steps produce
the same string the server would send. Those are exactly the claims the extension's README makes, so
they get driven for real:

    .venv/bin/python scripts/extension_check.py [--headed] [--screenshot popup.png]

What it does:

  1. launches Chrome through the project's own `BrowserManager`, so `sandbox="auto"` still applies;
  2. loads `chrome-extension/` with `Extensions.loadUnpacked` and checks that its popup runs and
     refuses to read a page it has no access to, rather than dying;
  3. points the server's own `Session.observe()` at `fixture.html` and keeps that table;
  4. loads a **throwaway copy** of the extension with one added host permission, opens its popup in
     the background, and compares its table with the server's, character for character;
  5. does that again after opening the fixture's modal, which exercises the delta path, the overlay
     warning and the occlusion flags.

Two things are deliberate and worth knowing before reading a failure:

**`Extensions.loadUnpacked`, not `--load-extension`.** Chrome no longer honours the flag for this
kind of run, and it fails silently -- the browser starts, the flag is accepted, and the extension is
simply not there. The CDP command also returns the id, which is the only reliable way to know it:
the id is derived from the absolute path, so it changes with the checkout.

**The copy in step 4.** `activeTab` is granted by a real invocation of the extension -- a toolbar
click, a context-menu item, a keyboard command -- and no automation can produce one. So a popup
opened as a background tab has no access to the page, and the comparison in step 4 cannot run against
the shipped manifest. The copy is byte-identical apart from one line of `host_permissions`, so
everything under test is the real thing; what is *not* covered is the grant itself, and the script
says so in its output rather than leaving it implied. Step 2 is what keeps that honest: the shipped
extension is loaded too, and the check is that it degrades the way its README says it does.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_ultrafast_mcp.browser import BrowserManager  # noqa: E402
from jev_ultrafast_mcp.config import Config  # noqa: E402

EXTENSION = ROOT / "chrome-extension"
FIXTURE = "fixture.html"

# The popup numbers its observations from one, the server from its own session, so the header is
# compared with the number removed. Everything after it has to match exactly.
SEQUENCE = re.compile(r"\[(?:obs|delta)#\d+\]")

# What the popup says when it cannot read the page. Asserted rather than paraphrased, because the
# point of the check is that a refusal is reported in words rather than as a stack trace.
REFUSAL = "does not allow extensions to read it"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return bool(ok)


def note(text: str) -> None:
    print(f"  \033[2m{text}\033[0m", flush=True)


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m", flush=True)


def wait_until(pred, timeout: float = 20.0, interval: float = 0.15):
    """Poll for the thing that is actually being waited on, never a fixed sleep."""
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def normalise(table: str) -> str:
    return SEQUENCE.sub("[obs#]", table)


# ------------------------------------------------------------------ fixture server


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # noqa: D102 - silence the per-request log
        pass


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


def load_extension(cdp, path: Path) -> str:
    return cdp.call("Extensions.loadUnpacked", path=str(path), timeout=30).get("id") or ""


def with_host_permission(destination: Path, origin: str) -> Path:
    """A copy of the extension that can read the fixture without a toolbar click.

    One line differs. Everything the extension *does* is the shipped code, which is the point; the
    one thing this cannot test is the `activeTab` grant, because that needs a real click.
    """
    shutil.copytree(EXTENSION, destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["host_permissions"] = [origin]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination


# ------------------------------------------------------------------ the popup


class Popup:
    """The extension's popup, opened as a background tab and read over CDP."""

    def __init__(self, cdp, extension_id: str, activate_target: str):
        self.cdp = cdp
        self.target_id = cdp.call(
            "Target.createTarget",
            url=f"chrome-extension://{extension_id}/popup.html",
            background=True,
        )["targetId"]
        self.session = cdp.call(
            "Target.attachToTarget", targetId=self.target_id, flatten=True)["sessionId"]
        # Creating the popup must not take the active tab away from the page under test: the popup
        # asks for `{active: true, currentWindow: true}` and would otherwise read itself.
        cdp.call("Target.activateTarget", targetId=activate_target)

    def read(self, expression: str):
        return self.cdp.call(
            "Runtime.evaluate", session_id=self.session, expression=expression,
            returnByValue=True, awaitPromise=False,
        )["result"].get("value")

    def table(self) -> str | None:
        """The rendered table, or None while the popup is still working."""
        try:
            value = self.read("(document.getElementById('table')||{}).textContent || ''")
        except Exception:  # noqa: BLE001 - the context is not up yet, which is not a failure
            return None
        return value if "reachable=" in value else None

    def observe(self, previous: str | None) -> str | None:
        """Press Observe and wait for the table to change."""
        self.read("document.getElementById('observe').click(), true")
        return wait_until(lambda: (lambda t: t if t and t != previous else None)(self.table()))

    def note(self) -> str:
        return self.read(
            "((document.getElementById('note')||{}).textContent || '').trim()") or ""

    def stats(self) -> str:
        return self.read("((document.getElementById('stats')||{}).textContent || '').trim()") or ""

    def page(self) -> str:
        return self.read("((document.getElementById('page')||{}).textContent || '').trim()") or ""

    def title(self) -> str:
        return self.read("document.title") or ""

    def screenshot(self, path: Path) -> int:
        data = self.cdp.call("Page.captureScreenshot", session_id=self.session, format="png")
        path.write_bytes(base64.b64decode(data["data"]))
        return path.stat().st_size


# ------------------------------------------------------------------ flow


def run(base: str, headed: bool, screenshot: Path | None) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="jev-extension-"))
    cfg = Config.from_env()
    cfg.headless = not headed
    cfg.profile_dir = workdir / "profile"
    cfg.state_dir = workdir / "state"
    cfg.window = (1100, 820)

    # `allow_extensions` drops the `--disable-extensions` every other run relies on: that flag blocks
    # the extension's own pages outright (`ERR_BLOCKED_BY_CLIENT`) rather than merely leaving them out.
    manager = BrowserManager(cfg, allow_extensions=True)
    cdp = manager.cdp
    print(f"\033[2mchrome: {cdp.call('Browser.getVersion').get('product')}\033[0m")

    section("1. The server's own view of the fixture")
    session = manager.session("extension")
    session.navigate(f"{base}/{FIXTURE}")
    observation = session.observe()
    # Exactly what `server.py::_view` sends on the first read of a page.
    server_first = observation.render(None, mode="auto", include_text=True, max_text=cfg.max_text)
    check("the fixture has a table worth comparing", len(observation.elements) >= 12,
          f"{len(observation.elements)} elements")
    check("the password field is in the table", "Password" in server_first)

    fixture_target = _target_for(cdp, f"{base}/{FIXTURE}")
    if not check("the fixture tab is findable", bool(fixture_target), fixture_target or "not found"):
        return 1

    section("2. The extension Chrome would load, with the manifest it ships")
    try:
        shipped_id = load_extension(cdp, EXTENSION)
    except Exception as exc:  # noqa: BLE001 - an older browser is a skip, not a failure
        print(f"  [skip] Extensions.loadUnpacked is unavailable: {exc}")
        return 2
    check("Chrome accepted the manifest and assigned an id", bool(shipped_id), shipped_id)

    shipped = Popup(cdp, shipped_id, fixture_target)
    check("its popup page loads",
          wait_until(lambda: shipped.title() == "Element table", timeout=10) is not None,
          shipped.title())
    # It has `activeTab` and nothing else, so it cannot read this page -- and the check is that it
    # says so. A popup that reported a blank table, or threw, would be the same failure to a user.
    settled = wait_until(lambda: shipped.note() or None, timeout=15)
    check("it declines the page it has no access to, in words",
          bool(settled) and REFUSAL in settled, settled or "(no note)")
    check("it rendered no table rather than a wrong one", shipped.table() is None)

    section("3. The same popup, with the one permission a click would grant it")
    patched_id = load_extension(
        cdp, with_host_permission(workdir / "extension", "http://127.0.0.1:*/*"))
    popup = Popup(cdp, patched_id, fixture_target)
    first = wait_until(popup.table)
    if not first:
        check("the popup rendered a table without being touched", False,
              popup.note() or "no table after 20s")
        return 1
    check("the popup rendered a table without being touched", True)
    check("the popup read the page, not itself", f"{base}/{FIXTURE}" in first,
          first.splitlines()[0][:110])
    check("the popup's title line names the fixture", "Ultrafast Fixture" in first)

    latest = observation
    if normalise(first).strip() == normalise(server_first).strip():
        check("the popup's table is the server's table, character for character", True)
    else:
        check("the popup's table is the server's table, character for character", False)
        _print_diff(normalise(server_first), normalise(first))

    section("4. The same page, after it changed")
    opener = next((element.ref for element in observation.elements
                   if element.name.strip().lower() == "open modal"), None)
    if check("the fixture's modal opener was found", bool(opener), opener or "not found"):
        session.act([{"op": "click", "ref": opener}])
        changed = popup.observe(first)
        if check("the popup rendered a second table", bool(changed)):
            latest = session.observe()
            # `session.act()` observes on the way out, so the session's own `previous` is already
            # the post-click page and `latest.previous` would render "no change". The popup, which
            # was never told about the click, compares against its pre-click read -- so the server's
            # delta is rendered against the same before-state the popup used. Comparing the two
            # without doing this is comparing two different questions.
            server_second = latest.render(
                observation, mode="delta", include_text=True, max_text=cfg.max_text)
            check("the second read is a delta", changed.lstrip().startswith("[delta#"),
                  changed.splitlines()[0][:110])
            check("the popup reports the overlay the server reports",
                  "dialog open" in changed and "dialog open" in server_second)
            if normalise(changed).strip() == normalise(server_second).strip():
                check("the popup's delta is the server's delta, character for character", True)
            else:
                check("the popup's delta is the server's delta, character for character", False)
                _print_diff(normalise(server_second), normalise(changed))

    section("5. The popup's own chrome")
    check("it names the page it read", popup.page() == "Ultrafast Fixture", popup.page())
    check("its counters match the server's",
          f"reachable={latest.reachable}/{len(latest.elements)}" in popup.stats(), popup.stats())

    if screenshot is not None:
        size = popup.screenshot(screenshot)
        note(f"screenshot: {screenshot} ({size // 1024} KB)")

    note("not covered here: the `activeTab` grant itself, which only a real toolbar click produces.")
    note("`tests/test_extension.py` covers the permission list; section 2 covers the refusal path.")
    return 0


def _target_for(cdp, url: str) -> str:
    for target in cdp.call("Target.getTargets").get("targetInfos", []):
        if target.get("url") == url and target.get("type") == "page":
            return target["targetId"]
    return ""


def _print_diff(expected: str, actual: str) -> None:
    left, right = expected.split("\n"), actual.split("\n")
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else "<missing>"
        b = right[index] if index < len(right) else "<missing>"
        if a != b:
            print(f"    first difference, line {index + 1}:")
            print(f"      server: {a!r}")
            print(f"      popup:  {b!r}")
            return
    print("    (no differing line — check the trailing newline)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--screenshot", type=Path, default=None,
                        help="write a PNG of the popup to this path")
    args = parser.parse_args()

    httpd, base = serve(ROOT / "tests")
    try:
        code = run(base, args.headed, args.screenshot)
    finally:
        httpd.shutdown()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    if code == 2:
        print("skipped: this browser has no Extensions.loadUnpacked")
        return 0
    if failed:
        print(f"{len(failed)} of {len(RESULTS)} checks failed")
        return 1
    print(f"all {len(RESULTS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
