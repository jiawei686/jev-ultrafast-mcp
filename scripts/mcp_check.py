#!/usr/bin/env python
"""Talk to the server the way an MCP client does: real stdio, real tool calls.

`scripts/smoke.py` exercises the library. This exercises the *MCP surface* —
handshake, tool discovery, argument marshalling, and the text an agent actually
receives — because a server that works as a library and not over stdio is not a
working MCP server.

    python scripts/mcp_check.py
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return bool(ok)


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *args, **kwargs: None  # signature is http.server's
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


def _child_env(state: Path) -> dict[str, str]:
    """Environment for the server subprocess.

    Inherit the real environment so JEVMCP_CHROME (and a proxy, on machines that
    need one) reach the child, then override the keys this check controls and
    drop the ones that would make it non-hermetic.
    """
    env = dict(os.environ)
    for key in ("TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY"):
        env.pop(key, None)
    env.update({
        "JEVMCP_HEADLESS": "1",
        "JEVMCP_MODE": "launch",
        "JEVMCP_ALLOW_JS": "0",
        "JEVMCP_STATE_DIR": str(state),
        "JEVMCP_PROFILE_DIR": str(state / "profile"),
    })
    return env


async def run(base: str) -> int:
    """`_run`, with the throwaway profile removed whether or not the checks pass.

    The server this talks to launches its own Chrome under `JEVMCP_PROFILE_DIR`,
    so the directory is not merely scratch: it is where a browser keeps its
    state. Leaving it behind leaves a profile per run on disk.
    """
    state = Path(tempfile.mkdtemp(prefix="jev-mcp-check-"))
    try:
        return await _run(base, state)
    finally:
        shutil.rmtree(state, ignore_errors=True)


async def _run(base: str, state: Path) -> int:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jev_ultrafast_mcp"],
        cwd=str(ROOT),
        env=_child_env(state),
    )

    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"\033[2mserver: {init.server_info.name} {init.server_info.version} "
                  f"| protocol {init.protocol_version}\033[0m")
            check("initialize handshake", init.server_info.name == "jev-ultrafast-mcp",
                  init.server_info.name)
            check("server instructions handed to the agent",
                  bool(init.instructions) and "element table" in (init.instructions or ""),
                  f"{(init.instructions or '')[:60]}...")

            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            expected = {
                "browser_open", "browser_observe", "browser_act", "browser_assert",
                "browser_macro", "browser_goal", "browser_tabs", "browser_sessions",
                "browser_close", "browser_doctor",
            }
            check("all tools exposed", expected.issubset(set(names)), ", ".join(names))
            check("no raw javascript escape hatch in the default surface",
                  "javascript" not in " ".join(names),
                  "act's eval op is gated by JEVMCP_ALLOW_JS")

            async def call(tool: str, **arguments) -> str:
                result = await session.call_tool(tool, arguments)
                text = "".join(
                    block.text for block in result.content if getattr(block, "type", "") == "text"
                )
                if result.is_error:
                    raise AssertionError(f"{tool} returned an error: {text}")
                return text

            doctor = await call("browser_doctor")
            report = json.loads(doctor)
            check("doctor reports the browser binary", bool(report.get("chrome")),
                  str(report.get("chrome")))
            check("doctor reports policy envelope", "deny_domains" in report,
                  f"js_eval={report.get('js_eval')} uploads={report.get('uploads')}")

            opened = await call("browser_open", url=f"{base}/fixture.html",
                                hint="fill the search form")
            check("open returns the element table", "Where from?" in opened and "e" in opened,
                  opened.splitlines()[0][:90])
            check("open echoes the goal", "goal: fill the search form" in opened)

            def ref_for(label: str) -> str | None:
                for line in opened.splitlines():
                    if label in line and line.split()[0].startswith("e"):
                        return line.split()[0]
                return None

            ref = ref_for("Where from?")
            pax = ref_for("Passengers")
            check("refs are addressable from the table text",
                  ref is not None and pax is not None, f"from={ref} passengers={pax}")

            acted = await call("browser_act", ops=[
                {"op": "type", "ref": ref, "text": "Zurich"},
                {"op": "select", "ref": pax, "value": "2 adults"},
            ])
            check("batched act executes and reports per-op status", "2/2 ops ok" in acted,
                  acted.splitlines()[0])

            observed = await call("browser_observe", mode="full")
            check("observe reflects the typed value", '"Zurich"' in observed,
                  next((line.strip() for line in observed.splitlines() if "Where from?" in line), ""))

            quiet = await call("browser_observe")
            check("a no-op observe collapses to one line", "= no change" in quiet,
                  quiet.strip().splitlines()[-1][:70])

            verdict = await call("browser_assert", checks=[
                {"type": "url_contains", "text": "fixture.html"},
                {"type": "value_equals", "ref": ref, "value": "Zurich"},
                {"type": "count_at_least", "role": "button", "name": "Search", "min": 1},
            ])
            check("assert returns a machine verdict", verdict.startswith("PASS"),
                  verdict.splitlines()[0])

            gated = await call("browser_act", ops=[{"op": "eval", "js": "1+1"}])
            line = next((row for row in gated.splitlines() if "eval" in row), "")
            check("eval is refused while JS is disabled", "eval is disabled" in gated, line.strip()[:90])

            listed_tabs = await call("browser_tabs", action="list")
            check("tabs are listable over MCP", "fixture.html" in listed_tabs,
                  listed_tabs.strip().splitlines()[-1][:80])

            closed = await call("browser_close", session="default")
            check("session closes cleanly", "closed" in closed, closed.strip())

    # A second server with an explicit allowlist: prove the envelope is enforced
    # by the server, not by prompt text.
    guarded = dict(parameters.env or {})
    guarded["JEVMCP_ALLOW_DOMAINS"] = "127.0.0.1"
    # Its own profile dir: a second Chrome must not fight the first one for a
    # singleton profile lock.
    guarded["JEVMCP_PROFILE_DIR"] = str(state / "profile-guarded")
    async with stdio_client(StdioServerParameters(
        command=parameters.command, args=list(parameters.args or []),
        cwd=parameters.cwd, env=guarded,
    )) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("browser_open", {"url": "https://example.com/"})
            text = "".join(b.text for b in result.content if getattr(b, "type", "") == "text")
            check("allowlist blocks an out-of-envelope navigation over MCP",
                  "blocked_by_policy" in text, text.strip().splitlines()[0][:100])

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} MCP checks passed")
    for name in failed:
        print(f"    - {name}")
    return 1 if failed else 0


def main() -> int:
    httpd, base = serve(ROOT / "tests")
    print(f"fixture server: {base}/fixture.html")
    try:
        return asyncio.run(run(base))
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())
