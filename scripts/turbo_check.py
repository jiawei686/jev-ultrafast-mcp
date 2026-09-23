#!/usr/bin/env python
"""Prove turbo mode works, against a real page and the real decision model.

Every other check in this repo stops short of `browser_goal`: `smoke.py` drives
the browser directly, `mcp_check.py` speaks the protocol with the loop never
entered, and the unit tests fake the provider. That left the one path that
spends money as the one path nothing ran end to end — which is how a
`KeyError` on the model's own correct answer survived a green test suite.

This script closes that gap. It serves the same fixture `smoke.py` uses, points
a real Chrome at it, and lets the decision model drive: fill a dropdown, tick a
checkbox, submit. Then it verifies the page the model left behind with code
rather than trusting the model's claim of success.

    python scripts/turbo_check.py                 # needs a decision-model key
    python scripts/turbo_check.py --headed        # watch it happen
    python scripts/turbo_check.py --model jev-1.13
    python scripts/turbo_check.py --goal "..."    # any goal the fixture can satisfy

Deliberately not in CI: it needs a key, the network, and a fraction of a cent.
Without a key it says so and exits 0, so a pipeline run cannot mistake
"skipped" for "passed" -- the exit code and the word `skipped` both say which.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from smoke import serve  # noqa: E402

from jev_ultrafast_mcp import policy  # noqa: E402
from jev_ultrafast_mcp.config import Config  # noqa: E402

# Selecting 3 adults, ticking Nonstop and submitting is enough to exercise
# SELECT, TOGGLE and CLICK in one goal, and it needs no field values -- so the
# check depends on the decision model alone. Typing would drag in the text
# helper, and a check that fails because of the second model tells you nothing
# about the first.
DEFAULT_GOAL = (
    "On this flight search form: set Passengers to 3 adults, tick the "
    "'Nonstop only' checkbox, then submit the search. Do not type into any "
    "city field."
)

# Only the post-submit result rows contain this, so it cannot pass on a stale
# page or on the dropdown's own option labels.
VERIFY = [
    {"type": "text_contains", "text": "3 adults \u00b7 nonstop"},
    {"type": "url_contains", "text": "fixture.html"},
]


def _report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail else ""), flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--headed", action="store_true", help="run with a visible window")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--model", default="", help="override TYPESAFE_MODEL")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()

    if args.headed:
        os.environ["JEVMCP_HEADLESS"] = "0"
    if args.model:
        os.environ["TYPESAFE_MODEL"] = args.model

    # Something in the MCP stack configures logging at INFO, which turns every
    # decision-model request into two lines of output before the verdict. The
    # verdict is what this script is for.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = Config.from_env()
    if not policy.available(cfg):
        print("skipped: no decision-model key.")
        print("  set TYPESAFE_API_KEY, or JEV_PROVIDER=openrouter with OPENROUTER_API_KEY")
        print("  (equivalently, TYPESAFE_BASE_URL=https://openrouter.ai/api/alpha/decisions)")
        return 0

    # Imported here, not at the top: the server reads its configuration once, at
    # import, so `--headed` and `--model` have to be in the environment first or
    # they would be silently ignored.
    from jev_ultrafast_mcp.server import MANAGER, browser_goal, browser_open

    print(f"decision model: {cfg.typesafe_model} via {cfg.provider} ({cfg.typesafe_endpoint})")
    if cfg.text_model_key:
        print(f"text helper   : {cfg.text_model} at {cfg.text_model_base}")
    else:
        print("text helper   : not configured (goal needs no typing)")
    print(f"goal          : {args.goal}\n")

    httpd, base = serve(ROOT / "tests")
    page = f"{base}/fixture.html"
    print(f"fixture       : {page}\n")
    failures = 0
    try:
        # turbo mode drives whatever page the session is already on, so the
        # navigation has to happen first -- `about:blank` is a page too, and a
        # goal aimed at it blocks immediately (which is how this check found
        # itself verifying nothing at all).
        opened = browser_open(page, hint=args.goal)
        if not opened.startswith("opened"):
            print(opened)
            _report("the fixture page loaded", False, opened.splitlines()[0])
            return 1

        out = browser_goal(args.goal, max_steps=args.max_steps, verify=VERIFY, verbose=True)
        print(out)
        print()

        failures += not _report("the goal reached DONE", "status: done" in out)
        failures += not _report("the page proves it, not the model",
                                "verified: PASS" in out)
        failures += not _report(
            "no failure status leaked into a completed run",
            "failed:" not in out and "turbo_unavailable" not in out,
            _first_status(out),
        )
    finally:
        httpd.shutdown()
        try:
            MANAGER.shutdown()
        except Exception:  # noqa: BLE001 - teardown must not mask the result
            pass

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("turbo mode drove a real browser through a real goal")
    return 0


def _first_status(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("status:"):
            return line
    return "no status line"


if __name__ == "__main__":
    sys.exit(main())
