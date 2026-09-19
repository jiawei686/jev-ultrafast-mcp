#!/usr/bin/env python
"""Drive the server over real stdio MCP against real websites.

`smoke.py` proves the library against a fixture we control. `mcp_check.py`
proves the MCP surface against that same fixture. Neither can tell you whether
this works on the open web, where pages are megabytes of JavaScript, render
after `readyState` already says "complete", and come wrapped in cookie banners.

This does. It is deliberately **not** part of CI: it needs the network and
third-party sites, and a red build caused by somebody else's rate limiter is
worse than no build at all. Run it by hand when you want the real answer.

    python scripts/live_check.py                 # headless, all targets
    python scripts/live_check.py --headed        # watch the browser work
    python scripts/live_check.py --target bing   # one target only

Sites that cannot be reached are reported as skipped, not failed -- but the
count is printed at the end so a run where everything was skipped cannot be
mistaken for a run where everything passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

# --------------------------------------------------------------------- report

RESULTS: list[tuple[str, str, str]] = []   # (status, name, detail)
PASSED = "ok"
FAILED = "FAIL"
SKIPPED = "skip"
NETWORK_SKIP = "offline"


def report(status: str, name: str, detail: str = "") -> bool:
    RESULTS.append((status, name, detail))
    tag = {PASSED: "[ok  ]", FAILED: "[FAIL]", SKIPPED: "[skip]", NETWORK_SKIP: "[skip]"}[status]
    print(f"  {tag} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return status == PASSED


class Offline(Exception):
    """The network, or the site, said no. Not a defect in this project."""


def unreachable(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in text for hint in (
        "timeout", "timed out", "err_name_not_resolved", "err_connection",
        "err_internet_disconnected", "err_proxy", "err_network", "err_tunnel",
        "dns", "name or service not known", "connection refused", "getaddrinfo",
    ))


# ------------------------------------------------------------------- plumbing

REACHABLE = re.compile(r"reachable=(\d+)/(\d+)")
ROW = re.compile(r"^(e\d+)\s+(\S+)\s+(.*)$", re.MULTILINE)


def reachable_count(view: str) -> tuple[int, int]:
    match = REACHABLE.search(view)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def rows(view: str) -> list[tuple[str, str, str]]:
    """(ref, role-with-flags, label) for every element row in a view."""
    found = []
    for ref, role, label in ROW.findall(view):
        found.append((ref, role.rstrip("*»⊘▾✓·"), label.strip()))
    return found


def ref_of(view: str, name: str, *roles: str) -> str | None:
    """Find a ref by label. Roles are the short codes the table prints."""
    needle = name.lower()
    wanted = set(roles)
    for ref, row_role, label in rows(view):
        if wanted and row_role not in wanted:
            continue
        if needle in label.lower():
            return ref
    return None


def find_search_box(view: str) -> str | None:
    """A search field, whichever of the three shapes the site chose.

    The element table prints role codes (cmb, inp, srch), not ARIA names.
    """
    return (ref_of(view, "search", "cmb")
            or ref_of(view, "search", "srch")
            or ref_of(view, "search", "inp"))


def count_roles(view: str, role: str) -> int:
    return sum(1 for _, row_role, _ in rows(view) if row_role == role)


def child_env(state: Path, headless: bool) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY"):
        env.pop(key, None)
    env.update({
        "JEVMCP_HEADLESS": "1" if headless else "0",
        "JEVMCP_MODE": "launch",
        "JEVMCP_ALLOW_JS": "0",
        "JEVMCP_STATE_DIR": str(state),
        "JEVMCP_PROFILE_DIR": str(state / "profile"),
    })
    return env


# ---------------------------------------------------------------------- checks


async def csr_check(call, target: str) -> int:
    """A client-rendered home page must be readable on the first read.

    This is the real-web version of the regression the fixture covers: the page
    reports `readyState == complete` while its body is still an empty shell, so
    a reader that trusts readyState announces an empty page.
    """
    print(f"\n\033[1m[{target}] client-rendered home page — first read is not empty\033[0m")
    url = {"bing": "https://www.bing.com/", "ddg": "https://duckduckgo.com/"}[target]
    started = time.monotonic()
    try:
        view = await call("browser_open", url=url, hint="find the search box")
    except AssertionError as exc:
        raise Offline(str(exc)) from exc
    took = time.monotonic() - started

    reachable, total = reachable_count(view)
    report(PASSED if reachable else FAILED,
           f"{target}: first read sees actionable elements",
           f"{reachable}/{total} reachable, {took:.2f}s")
    report(PASSED if "no actionable elements visible" not in view else FAILED,
           f"{target}: does not claim an empty page", view.splitlines()[0][:88])

    box = find_search_box(view)
    report(PASSED if box else FAILED, f"{target}: the search box is addressable",
           f"{box} in {len(rows(view))} rows")
    return 0


async def search_check(call) -> int:
    """Type into a real search box, press Enter, and read real results."""
    print("\n\033[1m[ddg] real form — type, submit, read the results\033[0m")
    view = await call("browser_open", url="https://duckduckgo.com/", hint="search")
    box = find_search_box(view)
    if not box:
        report(SKIPPED, "ddg: search box located", "the home page layout changed")
        return 0
    report(PASSED, "ddg: search box located", box)

    query = "python asyncio tutorial"
    acted = await call("browser_act", ops=[
        {"op": "type", "ref": box, "text": query},
        {"op": "keys", "key": "Enter"},
    ])
    report(PASSED if "2/2 ops ok" in acted else FAILED,
           "ddg: type and submit in one round trip", acted.splitlines()[0][:80])

    results = await call("browser_observe", mode="full")
    url_line = results.splitlines()[0] if results.splitlines() else ""
    links = count_roles(results, "lnk")
    report(PASSED if "q=" in url_line and "asyncio" in url_line else FAILED,
           "ddg: navigated to a result page", url_line.split()[1][:90] if " " in url_line else "")
    report(PASSED if links >= 5 else FAILED, "ddg: result links are indexed",
           f"{links} links in {len(rows(results))} rows")

    verdict = await call("browser_assert", checks=[
        {"type": "url_contains", "text": "q="},
        {"type": "count_at_least", "role": "link", "min": 5},
    ])
    report(PASSED if verdict.startswith("PASS") else FAILED,
           "ddg: deterministic assertion on a live page", verdict.splitlines()[0][:80])
    return 0


async def macro_check(call) -> int:
    """Record a real flow, then replay it without a model in the loop."""
    print("\n\033[1m[ddg] macro — record a real search, replay it\033[0m")
    view = await call("browser_open", url="https://duckduckgo.com/", hint="search")
    box = find_search_box(view)
    if not box:
        report(SKIPPED, "ddg: macro recording", "no search box on the home page")
        return 0

    await call("browser_macro", action="record_start")
    await call("browser_act", ops=[
        {"op": "type", "ref": box, "text": "{{query}}"},
        {"op": "keys", "key": "Enter"},
    ])
    saved = await call("browser_macro", action="record_stop", name="live-search",
                       goal="search the web for {{query}}")
    report(PASSED if "saved macro" in saved else FAILED, "ddg: flow recorded",
           saved.splitlines()[0][:80])

    # No navigation here on purpose. The macro recorded where the task *began*,
    # so replay has to take the browser back there by itself and re-resolve
    # every step against the fresh page. That is the whole point of a macro.
    replayed = await call("browser_macro", action="run", name="live-search",
                          params={"query": "python dataclasses"})
    report(PASSED if replayed.startswith("replayed") else FAILED,
           "ddg: replay re-resolved against a fresh page", replayed.splitlines()[0][:80])
    report(PASSED if "resolved 2 steps" in replayed else FAILED,
           "ddg: every step re-resolved (no model calls)", replayed.splitlines()[0][:80])

    after = await call("browser_observe", mode="full")
    first = after.splitlines()[0] if after.splitlines() else ""
    report(PASSED if "dataclasses" in first else FAILED,
           "ddg: the parameterised query reached the page",
           first.split()[1][:90] if " " in first else "")

    listed = await call("browser_macro", action="list")
    report(PASSED if "live-search" in listed else FAILED, "ddg: macro is on disk",
           listed.strip().splitlines()[0][:70])
    await call("browser_macro", action="delete", name="live-search")
    return 0


async def tabs_and_shot_check(call, state: Path) -> int:
    """Tabs, a screenshot, and the doctor — on a live browser."""
    print("\n\033[1m[live] tabs, screenshot, doctor\033[0m")
    await call("browser_open", url="https://example.com/", hint="baseline")

    opened = await call("browser_tabs", action="new", url="https://example.com/")
    report(PASSED if "example.com" in opened else FAILED, "a second tab is addressable",
           opened.strip().splitlines()[0][:70])

    listed = await call("browser_tabs", action="list")
    report(PASSED if listed.count("example.com") >= 1 else FAILED, "tabs are listable",
           f"{len(listed.strip().splitlines())} line(s)")

    payload = await call("browser_act", ops=[{"op": "screenshot", "format": "png"}])
    shots = sorted((state / "shots").glob("*.png")) if (state / "shots").exists() else []
    report(PASSED if shots and shots[-1].stat().st_size > 1000 else FAILED,
           "screenshot lands on disk, not in the context",
           f"{shots[-1].name} {shots[-1].stat().st_size // 1024}KB" if shots else payload[:70])

    doctor = json.loads(await call("browser_doctor"))
    report(PASSED if doctor.get("chrome") else FAILED, "doctor reports the browser",
           str(doctor.get("chrome"))[:70])
    return 0


# ------------------------------------------------------------------------ main


async def run(headless: bool, targets: list[str]) -> int:
    state = Path(tempfile.mkdtemp(prefix="jev-live-"))
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jev_ultrafast_mcp"],
        cwd=str(ROOT),
        env=child_env(state, headless),
    )

    observed = 0

    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"\033[2mserver: {init.server_info.name} {init.server_info.version}"
                  f" | browser: {'headless' if headless else 'visible'}\033[0m")

            async def call(tool: str, **arguments) -> str:
                nonlocal observed
                result = await session.call_tool(tool, arguments)
                text = "".join(
                    block.text for block in result.content if getattr(block, "type", "") == "text"
                )
                observed += len(text)
                if result.is_error:
                    raise AssertionError(text)
                return text

            sections = []
            for target in targets:
                if target in ("bing", "ddg"):
                    sections.append((target, csr_check(call, target)))
            if "ddg" in targets:
                sections.append(("ddg-search", search_check(call)))
                sections.append(("ddg-macro", macro_check(call)))
            sections.append(("live", tabs_and_shot_check(call, state)))

            for name, coro in sections:
                try:
                    await coro
                except (AssertionError, Offline) as exc:
                    where = "network" if isinstance(exc, Offline) or unreachable(exc) else "page"
                    report(NETWORK_SKIP if where == "network" else FAILED,
                           f"[{name}] section aborted", f"{type(exc).__name__}: {str(exc)[:110]}")
                except Exception as exc:  # noqa: BLE001 - a live run must not die mid-way
                    if unreachable(exc):
                        report(NETWORK_SKIP, f"[{name}] unreachable",
                               f"{type(exc).__name__}: {str(exc)[:110]}")
                    else:
                        report(FAILED, f"[{name}] crashed", f"{type(exc).__name__}: {str(exc)[:140]}")

            try:
                await call("browser_close", session="default", shutdown_browser=True)
            except Exception:  # noqa: BLE001 - teardown failures are not findings
                pass

    passed = sum(1 for status, _, _ in RESULTS if status == PASSED)
    failed = [name for status, name, _ in RESULTS if status == FAILED]
    skipped = [name for status, name, _ in RESULTS if status in (SKIPPED, NETWORK_SKIP)]

    print("\n\033[1mSummary\033[0m")
    print(f"  checks passed     : {passed}/{len(RESULTS)}")
    print(f"  skipped           : {len(skipped)}"
          + ("  (offline — nothing was proven)" if skipped else ""))
    print(f"  bytes to the agent: {observed:,} (~{observed // 4:,} tokens)")
    print(f"  state dir         : {state}")
    if failed:
        print("  failures:")
        for name in failed:
            print(f"    - {name}")
    if skipped and not passed:
        print("\n  \033[33mEvery check was skipped. This run proves nothing — check the network.\033[0m")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--headed", action="store_true",
                        help="run with a visible browser window")
    parser.add_argument("--target", action="append", default=None,
                        choices=["bing", "ddg", "live"],
                        help="limit to one or more sections (default: all)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary state dir (screenshots, profile)")
    args = parser.parse_args()
    targets = args.target or ["bing", "ddg", "live"]

    try:
        code = asyncio.run(run(not args.headed, targets))
    except KeyboardInterrupt:
        return 130
    if not args.keep:
        for path in Path(tempfile.gettempdir()).glob("jev-live-*"):
            if path.is_dir() and (path / "profile").exists():
                shutil.rmtree(path, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
