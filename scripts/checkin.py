#!/usr/bin/env python
"""Daily check-in, driven by this MCP: cheapest stage first, a model only once.

A check-in is what a macro is for. It is the same two or three clicks every
day, on a page whose shape barely moves -- so the interesting question is not
"can an agent click the button" but "why would you pay an agent to click the
button four hundred times". This runs three stages, in increasing cost:

  1. already done  Read the page. If today's check-in is already on it, stop.
                   No click, no model call, nothing to undo.
  2. replay        Run the saved macro. Zero model calls, a few hundred
                   milliseconds, and it refuses rather than guessing when the
                   page has moved on.
  3. explore       Only when there is no macro, or the saved one no longer
                   matches: hand the goal to the decision model, let it work
                   the page out, and record what it did as a macro so stage 2
                   can take over tomorrow.

Stage 3 is the only one that spends money, and it runs at most once per site.

    python scripts/checkin.py                        # the bundled demo page
    python scripts/checkin.py --headed               # watch it happen
    python scripts/checkin.py --url https://...      # a real site
    python scripts/checkin.py --url https://... --expect "已签到" --goal "..."
    python scripts/checkin.py --url https://... --replay-only   # never spend
    python scripts/checkin.py --url https://... --record        # re-learn

Real sites usually want a login. The browser profile is persistent, so sign in
once and later runs reuse it -- including unattended ones:

    python scripts/checkin.py --url https://... --headed --wait 120

Not in CI: stage 3 needs a key and the network. With no key the first two
stages still run, and the script says plainly which one it skipped.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# The goal and the proof are deliberately separate. The goal is what the model
# is asked to do; `--expect` is what the page must show afterwards, checked by
# code. A run is judged by the second, never by the model's summary of the
# first.
DEFAULT_GOAL = ("Complete today's check-in on this page: find the daily "
                "check-in button and click it once.")
DEFAULT_EXPECT = "Checked in today"


def _with_param(url: str, param: str) -> str:
    parts = urlparse(url)
    query = f"{parts.query}&{param}" if parts.query else param
    return urlunparse(parts._replace(query=query))


# A port is an ephemeral detail, not part of a site's identity -- and on a
# development machine it changes on every run. Keying the macro name on the
# whole netloc meant the demo learned a path and then could never find it
# again, because `127.0.0.1:53426` and `127.0.0.1:53427` are different strings
# and the same check-in.
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _macro_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if not host or host in _LOOPBACK:
        return "checkin-demo"
    return "checkin-" + re.sub(r"[^a-z0-9]+", "-", host).strip("-")


def _report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail else ""),
          flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="", help="check-in page (default: the bundled demo)")
    parser.add_argument("--goal", default="", help="what checking in means, in words")
    parser.add_argument("--expect", default="", help="text that appears once checked in")
    parser.add_argument("--name", default="", help="macro name (default: derived from the URL)")
    parser.add_argument("--record", action="store_true", help="re-learn, ignoring the saved macro")
    parser.add_argument("--replay-only", action="store_true",
                        help="use the saved macro or fail -- never call the model")
    parser.add_argument("--headed", action="store_true", help="run with a visible window")
    parser.add_argument("--wait", type=float, default=0.0,
                        help="seconds to wait after opening, to sign in by hand")
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--port", type=int, default=0,
                        help="pin the demo server's port (a page's origin includes its port, "
                             "so only a pinned port lets a second run see the first one's state)")
    parser.add_argument("--reset", action="store_true",
                        help="clear the demo page's stored state, so it can be run twice a day")
    args = parser.parse_args()

    if args.headed:
        os.environ["JEVMCP_HEADLESS"] = "0"

    # Something in the stack logs at INFO, which turns each decision-model
    # request into two lines before the verdict this script exists to print.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    httpd = None
    if not args.url:
        from smoke import serve  # only the demo needs the fixture server

        httpd, base = serve(ROOT / "examples", port=args.port)
        args.url = f"{base}/checkin.html"

    # Imported after the environment is set: the server reads its configuration
    # once, at import, so --headed would be silently ignored otherwise.
    from jev_ultrafast_mcp import macros as macros_mod
    from jev_ultrafast_mcp import policy
    from jev_ultrafast_mcp import server as mcp

    name = args.name or _macro_name(args.url)
    goal = args.goal or DEFAULT_GOAL
    expect = args.expect or DEFAULT_EXPECT
    proof = [{"type": "text_contains", "text": expect}]

    print(f"page   : {args.url}")
    print(f"macro  : {name}")
    print(f"proves : page text contains {expect!r}\n")

    try:
        if args.reset:
            mcp.browser_open(_with_param(args.url, "reset=1"))

        opened = mcp.browser_open(args.url)
        if not opened.startswith("opened"):
            print(opened)
            return 1

        if args.wait:
            print(f"waiting {args.wait:g}s — sign in now if this site needs it", flush=True)
            time.sleep(args.wait)

        # ---- stage 1: is today already done? --------------------------------
        page = mcp.browser_observe(mode="full")
        if expect.lower() in page.lower():
            print(f"already checked in today — {expect!r} is on the page")
            print("nothing to do: no click was made and no model was called")
            return 0
        print("not checked in yet today")

        saved = any(item["name"] == name for item in macros_mod.listing(mcp.CONFIG))

        # ---- stage 2: replay, which costs nothing ---------------------------
        if saved and not args.record:
            print(f"replaying {name!r} (no model calls)")
            replay = mcp.browser_macro("run", name=name, start_url=args.url)
            print(replay)
            if replay.startswith("replayed"):
                verdict = mcp.browser_assert(proof)
                print(verdict)
                if verdict.startswith("PASS"):
                    print("\nchecked in by replay")
                    return 0
            print("the saved macro no longer matches this page")
        elif not saved:
            print("no saved macro for this page yet")

        if args.replay_only:
            print("nothing more to try: --replay-only was given and no macro worked")
            return 1

        # ---- stage 3: explore, the only stage that costs money --------------
        if not policy.available(mcp.CONFIG):
            print("\ncannot explore this page: no decision-model key.")
            print("  set TYPESAFE_API_KEY, or OPENROUTER_API_KEY with")
            print("  TYPESAFE_BASE_URL=https://openrouter.ai/api/alpha/decisions")
            return 1

        print(f"\nexploring with {mcp.CONFIG.typesafe_model} — this is the stage that costs money")
        mcp.browser_macro("record_start")
        out = mcp.browser_goal(goal, max_steps=args.max_steps, verify=proof, verbose=True)
        print(out)
        print()

        learned = "verified: PASS" in out
        if learned:
            print(mcp.browser_macro("record_stop", name=name, goal=goal))
            print("next run will replay this instead of asking the model")
        else:
            # Do not leave a path that did not work lying around under the name
            # the replay stage trusts. A failed exploration is discarded, not
            # saved for tomorrow to trip over.
            discarded = f"{name}-failed"
            mcp.browser_macro("record_stop", name=discarded, goal=goal)
            mcp.browser_macro("delete", name=discarded)

        problems = 0
        problems += not _report("the check-in is visible on the page now", learned)
        problems += not _report(
            "no failure status leaked into the run",
            "failed:" not in out and "turbo_unavailable" not in out,
        )
        print()
        if problems:
            print(f"{problems} check(s) failed")
            return 1
        print("checked in, and the page proves it")
        return 0
    finally:
        try:
            mcp.browser_close(shutdown_browser=True)
        except Exception:  # noqa: BLE001 - teardown must not mask the result
            pass
        try:
            mcp.MANAGER.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if httpd is not None:
            httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())
