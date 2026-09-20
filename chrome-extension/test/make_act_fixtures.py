"""Produce the extension's execution-layer fixtures with the real Python code.

`lib/session.js` is the third port in this extension: `browser.py`'s op dispatcher, which is where a
replay's clicks are actually made. Three kinds of thing in it are worth holding to Python by fixture
rather than by eye.

**The tables.** `MODIFIERS` and `KEY_SPECS` turn `ctrl+shift+k` into a virtual key code. A wrong
entry is not an exception, it is the wrong keystroke: the page receives something, nothing fails,
and the report says `ok`. Nothing else in the system can notice that.

**The two refusal rules.** `confirm_reason` is what stops an unattended replay at "Buy now", and
`is_secret` is what stops it typing a value into a password field. A port that got either of these
subtly wrong would not report a problem — it would click, or type, and look exactly like success.
So both are compared against Python for a spread of names and roles, along with `check_url`, which
is the third one: the domain envelope that decides whether a `nav` step is allowed to run at all.

**The report's floats.** A resolve score crosses JSON, which does not remember that the server held
it as a float, so `1.0` arrives here as `1`. `FLOAT_CASES` freezes Python's `str(round(v, 3))` for
every score the matcher can return, integral ones included. This family exists because a live replay
once printed `(1)` where the tool printed `(1.0)`.

Regenerate after touching `browser.py`, `safety.py`, `config.py` or the port:

    .venv/bin/python chrome-extension/test/make_act_fixtures.py

`tests/test_act_port.py` fails if the committed file is not what this script produces.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from jev_ultrafast_mcp import browser, server  # noqa: E402
from jev_ultrafast_mcp.config import DEFAULT_DENY_PATTERNS, DEFAULT_SECRET_PATTERNS, Config  # noqa: E402
from jev_ultrafast_mcp.macros import DEFAULT_THRESHOLD  # noqa: E402
from jev_ultrafast_mcp.safety import SafetyError, check_url, confirm_reason, is_secret  # noqa: E402

OUT = HERE.with_name("act-fixtures.json")

# Names and roles a replay really meets. The interesting ones are the near misses: a field called
# "Passenger" is not "Password", and a link called "Unsubscribe from newsletter" is exactly the kind
# of thing a macro recorded before a redesign might aim at.
GUARD_CASES = [
    ("Where from?", "textbox"),
    ("Passengers", "listbox"),
    ("Search", "button"),
    ("Sign in", "button"),
    ("Password", "textbox"),
    ("password", "textbox"),
    ("Card number", "textbox"),
    ("CVV", "textbox"),
    ("OTP", "textbox"),
    ("Verification code", "textbox"),
    ("API key", "textbox"),
    ("Passenger", "textbox"),
    ("Passcode again", "textbox"),
    ("Delete account", "button"),
    ("Buy now", "button"),
    ("Place order", "button"),
    ("Unsubscribe from newsletter", "link"),
    ("Withdraw funds", "menuitem"),
    ("Cancel order", "tab"),
    ("Send money", "button"),
    # The role gate: a confirmation rule on something that is not a button/link/menuitem/tab is not
    # a click this needs to stop, so the role is the thing that decides it.
    ("Buy now", "textbox"),
    ("Delete account", "checkbox"),
    ("Buy now", ""),
]

URL_CASES = [
    ("http://127.0.0.1:8000/fixture.html", [], []),
    ("https://example.com/a?b=c", [], []),
    ("https://sub.example.com/a", ["example.com"], []),
    ("https://example.com/a", ["*.example.com"], []),
    ("https://example.com/a", ["other.com"], []),
    ("https://example.com/a", [], ["example.com"]),
    ("https://sub.example.com/a", [], ["*.example.com"]),
    ("https://example.com.evil.net/a", ["example.com"], []),
    # The suffix has to be a label boundary, not a string ending. `notexample.com` ends with
    # `example.com` and is a different site, in both directions: it must not sneak into an allow
    # list, and it must not be caught by a deny list either.
    ("https://notexample.com/a", ["example.com"], []),
    ("https://notexample.com/a", [], ["example.com"]),
    ("https://sub.example.com/a", ["example.com"], []),
    ("about:blank", [], ["example.com"]),
    ("data:text/html,hi", [], []),
    ("not a url at all", [], []),
]

INT_CASES = [600, 0, "600", "-25", 6.9, True, False, "", "6.5", "abc", None, 15000.4]

REF_CASES = ["e12", "12", "e12:3", "12:3", "bogus", "e0", "e7:x"]


def guard_case(name: str, role: str) -> dict:
    cfg = Config()
    reason = confirm_reason(cfg, name, role)
    return {
        "name": name,
        "role": role,
        "confirm": reason,
        "secret": is_secret(cfg, name, role),
    }


def url_case(url: str, allow: list[str], deny: list[str]) -> dict:
    cfg = Config()
    cfg.allow_domains = list(allow)
    cfg.deny_domains = list(deny)
    try:
        check_url(cfg, url)
    except SafetyError as exc:
        return {"url": url, "allow": allow, "deny": deny, "error": str(exc)}
    return {"url": url, "allow": allow, "deny": deny, "ok": True}


def int_case(value) -> dict:
    try:
        return {"value": value, "int": int(value)}
    except (TypeError, ValueError) as exc:
        return {"value": value, "error": str(exc)}


def ref_case(ref: str) -> dict:
    # `_resolve_ref` reads nothing but its argument, so it is called unbound rather than built into a
    # Session: the port has to agree about the ref string, not about anything a session does.
    element, option = browser.Session._resolve_ref(None, ref)
    return {"ref": ref, "element": element, "option": option}


# ------------------------------------------------------------------ the op dispatcher


class ScriptedCdp:
    """A CDP that answers `Session`'s questions from a script instead of a browser.

    The point is that the same script runs on both sides: this class here, and a driver with the
    same reply rules in `act-parity.mjs`. That makes the *op dispatcher* comparable — which op reads
    a ref, which one demands confirmation, which one parses its argument as an int — without a
    browser, and therefore without the comparison being a live check nobody runs.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.expressions: list[str] = []
        self.events: list[dict] = []
        self.label = "Search"
        self.guard: dict = {"ok": True}
        self.select: dict = {"ok": True, "value": "3", "label": "3 adults"}

    # `Session._call` and `Session._safe_eval` are the only two entry points used.
    def call(self, method, session_id=None, timeout=None, **params):
        self.calls.append({"method": method, "params": params})
        if method == "Target.getTargets":
            return {"targetInfos": []}
        return {}

    def evaluate(self, expression, session_id=None, await_promise=False, timeout=None):
        self.expressions.append(expression)
        return reply_for(self, expression)


def reply_for(cdp: ScriptedCdp, expression: str):
    """The reply rules. Mirrored exactly in act-parity.mjs's `scriptedDriver`."""
    if "verify(" in expression or "reinspect(" in expression:
        return cdp.guard
    if "resolve(" in expression:
        return {"ok": True, "x": 10, "y": 20}
    if "label(" in expression:
        return cdp.label
    if "selectOption(" in expression:
        return cdp.select
    if "__jevRefs.nodes.get(" in expression:
        return False
    if "readyState" in expression:
        return "complete"
    if "innerText" in expression:
        return "some body text"
    if "settle(" in expression:
        return None
    return None


# Each scenario is one op plus whatever the scripted page has to say about it. The list is chosen
# for the decisions, not for the coverage: which ops read a ref, which demand confirmation, how an
# argument is parsed, and which refusals are refusals rather than errors.
SCENARIOS: list[dict] = [
    {"why": "a plain click", "op": {"op": "click", "ref": "e5"}},
    {"why": "a click on a confirmation rule", "op": {"op": "click", "ref": "e5"},
     "label": "Buy now"},
    {"why": "the same click, confirmed", "op": {"op": "click", "ref": "e5", "confirm": True},
     "label": "Buy now"},
    {"why": "a click with no ref", "op": {"op": "click"}},
    {"why": "a click on a ref the page has moved on from", "op": {"op": "click", "ref": "e5"},
     "guard": {"ok": False, "reason": "detached"}},
    {"why": "typing into an ordinary field, clearing first",
     "op": {"op": "type", "ref": "e5", "text": "Zurich"}, "label": "Where from?"},
    {"why": "the same on a keyboard where select-all is Ctrl rather than Meta", "platform": "other",
     "op": {"op": "type", "ref": "e5", "text": "Zurich"}, "label": "Where from?"},
    {"why": "typing into a password field", "op": {"op": "type", "ref": "e5", "text": "hunter2"},
     "label": "Password"},
    {"why": "typing without clearing first, one key at a time",
     "op": {"op": "type", "ref": "e5", "text": "ab", "clear": False, "slow": True}},
    {"why": "typing and submitting", "op": {"op": "type", "ref": "e5", "text": "ab", "submit": True}},
    {"why": "selecting by value", "op": {"op": "select", "ref": "e5", "value": "3"}},
    {"why": "selecting by label", "op": {"op": "select", "ref": "e5", "label": "3 adults"}},
    {"why": "a select with nothing to select", "op": {"op": "select", "ref": "e5"}},
    {"why": "selecting the empty string", "op": {"op": "select", "ref": "e5", "value": ""}},
    {"why": "selecting zero", "op": {"op": "select", "ref": "e5", "value": 0}},
    {"why": "a select the page cannot honour",
     "op": {"op": "select", "ref": "e5", "value": "9"},
     "select": {"ok": False, "reason": "no_such_option"}},
    {"why": "toggling on", "op": {"op": "toggle", "ref": "e5", "state": True}},
    {"why": "a toggle already in the requested state",
     "op": {"op": "toggle", "ref": "e5", "state": False}},
    {"why": "hovering", "op": {"op": "hover", "ref": "e5"}},
    {"why": "a key combination", "op": {"op": "keys", "keys": "ctrl+shift+k"}},
    {"why": "a named key", "op": {"op": "keys", "key": "Enter"}},
    {"why": "keys with nothing to press", "op": {"op": "keys"}},
    {"why": "scrolling with a ref it will not read", "op": {"op": "scroll", "ref": "e5", "amount": 100}},
    {"why": "scrolling up", "op": {"op": "scroll", "dir": "up", "amount": 250}},
    {"why": "a wait of zero ms", "op": {"op": "wait", "ms": 0}},
    {"why": "a wait with no argument", "op": {"op": "wait"}},
    {"why": "waiting for text that is already there",
     "op": {"op": "wait_for_text", "text": "body"}},
    {"why": "a dry run", "op": {"op": "click", "ref": "e5"}, "dry_run": True},
    {"why": "nav outside the envelope", "op": {"op": "nav", "url": "javascript:alert(1)"}},
    {"why": "nav with no url", "op": {"op": "nav"}},
    {"why": "an op nobody knows", "op": {"op": "broadcast"}},
    {"why": "an op that is not an object", "op": "not an op"},
]

# Scenarios where the two sides deliberately differ, with the extension's side written down. These
# are not "not compared"; they are compared against an expectation of their own, because a
# divergence that nobody wrote down is the failure this whole file exists to prevent.
DIVERGENCES: list[dict] = [
    {
        "op": {"op": "scroll", "ref": "e5", "amount": 100},
        "field": "the wheel coordinates",
        "python": "the configured window, 1280x860, so 640,430",
        "extension": "the tab's own viewport, so 400,300",
        "why": ("the server drives a tab it owns and can size; the extension drives the tab the user "
                "is looking at, and resizing somebody's window to take a reading of it is not a "
                "trade a tool gets to make silently"),
    },
    {
        "op": {"op": "upload", "ref": "e5", "paths": ["/tmp/does-not-exist.pdf"]},
        "field": "what it says",
        "python": "invalid_request, \"file not found: /tmp/does-not-exist.pdf\"",
        "extension": "invalid_request, \"upload needs a path on disk, which an extension cannot read\"",
        "why": ("DOM.setFileInputFiles takes absolute paths and an extension cannot read one, so the "
                "extension refuses in words instead of reporting a missing file it never looked for"),
    },
    {
        "op": {"op": "tab", "action": "list"},
        "field": "whether it runs at all",
        "python": "ok, \"list tab current\"",
        "extension": "invalid_request, tab actions are not available",
        "why": ("the extension drives the one tab it was pointed at and cannot see the others, which "
                "is the same activeTab grant that keeps it from reading the rest of your browsing"),
    },
    {
        "op": {"op": "eval", "js": "1"},
        "field": "what it says",
        "python": "invalid_request, \"eval is disabled; set JEVMCP_ALLOW_JS=1 to enable it\"",
        "extension": "invalid_request, \"eval is disabled in the extension; it has no setting\"",
        "why": ("the server has a switch to turn eval on and the extension deliberately has none, so "
                "the sentence points somewhere different rather than pretending otherwise"),
    },
]


# The one op that reads `sys.platform` is `_select_all`, which sends Meta (4) on a Mac and Ctrl (2)
# everywhere else -- so `modifiers` lands in this file, read off the machine that generated it. That
# made the fixture machine-dependent: CI on Linux regenerated it as `2` where the committed file says
# `4`, and `test_the_committed_fixtures_are_what_the_generator_produces` refused a file that was
# perfectly correct on the machine that wrote it. A fixture whose bytes depend on where it was
# generated is not a fixture, so the platform is an input per case rather than an inherited fact, and
# the clear path is generated once for each value. That branch is a real decision about the user's
# keyboard and it had no coverage at all before -- the two-platform pair is the fix and the coverage.
PLATFORMS = {"mac": "darwin", "other": "linux"}


@contextlib.contextmanager
def platform_as(name: str):
    """Run the real dispatcher as if it were on `name`'s platform.

    `browser.py::_select_all` does its own `import sys`, so this patches the stdlib global rather
    than anything local to the module.
    """
    original = sys.platform
    sys.platform = PLATFORMS[name]
    try:
        yield
    finally:
        sys.platform = original


def scenario_case(scenario: dict) -> dict:
    cdp = ScriptedCdp()
    if "label" in scenario:
        cdp.label = scenario["label"]
    if "guard" in scenario:
        cdp.guard = scenario["guard"]
    if "select" in scenario:
        cdp.select = scenario["select"]
    platform = scenario.get("platform", "mac")
    session = browser.Session(name="fixture", cfg=Config(), cdp=cdp)
    with platform_as(platform):
        step = session._run_op(scenario["op"], dry_run=scenario.get("dry_run", False), strict=True)
    report = step.to_dict()
    # A fixture cannot contain a stopwatch. `ms` is a real reading of how long the step took, so
    # leaving it in makes this file differ from its own generator on every run and turns the
    # "committed == produced" assertion into noise. It is asserted to be present and to be an integer
    # by act-parity.mjs instead, which is the part of the report that is actually a contract.
    report.pop("ms", None)
    return {
        "why": scenario["why"],
        # Which platform the real dispatcher was told it was running on. Recorded rather than left
        # implicit, because the machine that generated this file is not the machine that checks it.
        "platform": platform,
        "op": scenario["op"],
        "dry_run": scenario.get("dry_run", False),
        # What the scripted page had to say, so the other side can set up the same page.
        "script": {key: scenario[key] for key in ("label", "guard", "select") if key in scenario},
        "expected": {
            "step": report,
            # The commands themselves, minus the two coordinates the extension sources differently.
            "calls": [{"method": call["method"],
                       "params": {key: value for key, value in call["params"].items()
                                  if key not in {"x", "y", "session_id", "timeout"}}}
                      for call in cdp.calls],
        },
    }


# `_render_act` shapes. The header `browser_macro` prints above this is not here, because it is an
# f-string inside the tool rather than a function: reproducing it would be writing the expectation
# twice. It is covered by `scripts/extension_check.py`, which calls the real tool and compares its
# whole reply with the one the extension produced.
#
# That arrangement is what let one bug through to a live run, and the shape of it is worth keeping.
# A resolve score is a float in Python however integral it looks, so the tool writes `(1.0)` for a
# perfect match; JSON keeps no trace of that and the extension printed `(1)`. Neither this fixture
# nor the port's own tests could see it, because every recorded score they carried happened to be
# non-integral, where `1.5` and `1` agree. The real replay of a macro that matched perfectly is what
# printed the two side by side. So the fix is pinned twice over: `FLOAT_CASES` below holds the
# formatting primitive to Python, and `test/act-parity.mjs` asserts the header actually reaches for
# it. What is still owed to the live check is the header's *wording*, not its arithmetic.
RENDER_CASES = [
    {
        "why": "two steps that worked",
        "payload": {"ok": True, "steps": 2, "ops": [
            {"op": "type", "ref": "e5", "target": "Where from?", "ok": True, "ms": 312},
            {"op": "click", "ref": "e11", "target": "Search", "ok": True, "ms": 580},
        ]},
    },
    {
        "why": "a step that failed and stopped the run",
        "payload": {"ok": False, "steps": 2, "ops": [
            {"op": "click", "ref": "e11", "target": "Buy now", "ok": False,
             "error": "needs_confirmation",
             "detail": "matches confirmation rule '\\bbuy\\s+now\\b'; re-send with "
                       "\"confirm\": true to proceed", "ms": 4},
        ]},
    },
    {
        "why": "an op with no ref, no target and no detail",
        "payload": {"ok": True, "steps": 1, "ops": [{"op": "wait", "ok": True, "ms": 500}]},
    },
    {
        "why": "a step whose ms the report never got",
        "payload": {"ok": True, "steps": 1, "ops": [{"op": "reload", "ok": True}]},
    },
    {
        "why": "a step whose detail the report is showing",
        "payload": {"ok": True, "steps": 1, "ops": [
            {"op": "toggle", "ref": "e3", "ok": True, "detail": "already in requested state"},
        ]},
    },
    {
        "why": "an op that failed with no detail to give",
        "payload": {"ok": False, "steps": 1, "ops": [
            {"op": "select", "ref": "e7", "ok": False, "error": "invalid_request"},
        ]},
    },
    {
        "why": "a run that got stuck",
        "payload": {"ok": True, "steps": 3, "ops": [{"op": "click", "ref": "e1", "ok": True}],
                    "stuck": "Three consecutive actions changed nothing."},
    },
    {
        "why": "a delta view under the steps",
        "payload": {"ok": True, "steps": 1, "ops": [{"op": "click", "ref": "e9", "ok": True}],
                    "page_changed": True,
                    "view": "[delta#2] http://x/fixture.html  \"T\"\n  + e10  button  \"Close\""},
    },
    {
        "why": "nothing to report at all",
        "payload": {"ok": False, "steps": 0, "ops": []},
    },
]


def render_case(case: dict) -> dict:
    return {**case, "expected": server._render_act(case["payload"])}


# The scores a replay header can print. The base is 0.55 or 0.6 and the addends come from
# {0.1, 0.4, 0.2, 0.2*overlap, 0.15}, so `1.0` is reachable -- `0.6 + 0.4`, a perfect match -- and it
# is the only integral sum there is. Two entries are not scores at all but inputs chosen for what
# `round(v, 3)` does to them, because that rounding is what stands between a score and a threshold.
# `expected` is Python's own `str(round(v, 3))`, the expression the tool's f-string applies. The file
# keeps the value as JSON writes it (`1.0`) so the lossy step is visible in the fixture itself: the
# reader here is handed `1`.
FLOAT_CASES = [1.0, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.95, 0.9, 1.05, 1.15, 0.925,
               0.6999999999, 0.8500000000000001]


def float_case(value: float) -> dict:
    return {"value": value, "expected": str(round(value, 3))}


def build() -> dict:
    return {
        "generator": "chrome-extension/test/make_act_fixtures.py",
        "note": ("Every `expected` block is produced by the real Python code the extension's "
                 "lib/session.js ports: browser.py for the op tables, the op dispatcher and "
                 "_resolve_ref, safety.py for the two refusal rules, config.py for the patterns "
                 "themselves, and server.py for the two report writers. The refusals are compared "
                 "in full, including the sentence, because that sentence is what a person reads to "
                 "fix the macro."),
        "helpers": {
            "confirm": [guard_case(name, role) for name, role in GUARD_CASES],
            "url": [url_case(*case) for case in URL_CASES],
            "int": [int_case(value) for value in INT_CASES],
            "ref": [ref_case(ref) for ref in REF_CASES],
            "float": [float_case(value) for value in FLOAT_CASES],
        },
        "scenarios": [scenario_case(scenario) for scenario in SCENARIOS],
        "renders": [render_case(case) for case in RENDER_CASES],
        "divergences": DIVERGENCES,
        # The tables, frozen as well as exercised. A constant is the cheapest thing to drift and the
        # hardest to notice, and these three are turned into keystrokes with no error path.
        "tables": {
            "helper_version": browser.HELPER_VERSION,
            "threshold": DEFAULT_THRESHOLD,
            "modifiers": browser.MODIFIERS,
            "keys": {name: list(spec) for name, spec in browser.KEY_SPECS.items()},
            "clickable_kinds": sorted(browser.CLICKABLE_KINDS),
            "nav_kinds": sorted(browser.NAV_KINDS),
            "requires_ref": sorted(browser.REQUIRES_REF),
            "scrollable_reasons": sorted(browser.SCROLLABLE_REASONS),
            "confirm_patterns": list(DEFAULT_DENY_PATTERNS),
            "secret_patterns": list(DEFAULT_SECRET_PATTERNS),
        },
    }


def main() -> int:
    payload = build()
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    helpers = payload["helpers"]
    counts = "  ".join(f"{name}={len(items)}" for name, items in helpers.items())
    print(f"wrote {OUT.relative_to(OUT.parents[2])} — {counts}  "
          f"scenarios={len(payload['scenarios'])}  renders={len(payload['renders'])}  "
          f"divergences={len(payload['divergences'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
