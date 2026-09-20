"""Tests for the execution-layer port: the op dispatcher the extension clicks with.

`tests/test_macro_port.py` covers the half of a replay that *decides*, and this covers the half that
*does*. `lib/session.js` is a port of `browser.py`'s op dispatcher, so `test/act-parity.mjs` runs the
same operations through both and compares two things: the step report, field for field, and the CDP
commands each one issued. No browser is involved — both sides get a scripted driver answering the
same expressions — which is the point, because a comparison that needs a browser is a comparison
nobody runs.

Five things are asserted here that the parity run alone cannot:

- the fixtures still hold the decisions that are easy to get wrong and impossible to notice, so a
  future edit cannot quietly reduce the suite to a comparison of the easy paths;
- the divergences from the server are enumerated, because a difference nobody wrote down is exactly
  what this file exists to prevent;
- the dead branch the port reproduces on purpose is pinned to the server's own code, so the day
  somebody makes it reachable, this fails and they read why;
- one refusal the extension makes — `upload` — is the extension's own, and is asserted to be a
  refusal rather than a silent no-op;
- the one number in a report that JSON flattens is asserted to be covered by a case, because that
  flattening is invisible from this side and a live replay is the only other thing that sees it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_ultrafast_mcp import browser  # noqa: E402
from jev_ultrafast_mcp.config import Config  # noqa: E402

EXT = ROOT / "chrome-extension"
TEST = EXT / "test"
FIXTURES = TEST / "act-fixtures.json"


# --- the port against the server -----------------------------------------------------------------


def test_the_dispatcher_port_agrees_with_python():
    """Run the Node parity check: 31 operations through both dispatchers, reports and commands.

    Node is not a dependency of this package and is not on PATH in every sandbox, so this skips with
    a message that says what to set rather than passing quietly. A test that becomes a no-op when its
    tool is missing is worse than no test — the whole point is that someone notices the day it stops
    running.
    """
    node = _node()
    if node is None:
        pytest.skip(
            "node was not found; set JEVMCP_NODE=/path/to/node to run the dispatcher parity test "
            "(the extension itself does not need node, only this check does)")

    result = subprocess.run(
        [node, str(TEST / "act-parity.mjs")],
        capture_output=True, text=True, cwd=str(EXT), check=False,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_committed_fixtures_are_what_the_generator_produces():
    """The fixtures are the ground truth, so they have to be regenerated, not hand-edited.

    Without this, someone changes `browser.py`, the fixtures keep describing the old dispatcher, and
    the parity test cheerfully certifies that the port matches a version of Python that no longer
    exists. That is not a hypothetical: a stale macro fixture let a mutation survive once already.
    """
    generator = _generator()
    committed = FIXTURES.read_text(encoding="utf-8")
    produced = json.dumps(generator.build(), indent=2, ensure_ascii=False) + "\n"

    assert committed == produced, (
        "chrome-extension/test/act-fixtures.json is stale; regenerate it with "
        "`.venv/bin/python chrome-extension/test/make_act_fixtures.py`")


def test_every_field_the_dispatcher_reads_is_covered_by_a_scenario():
    """The scenarios have to exercise the decisions, not just the ops.

    A dispatcher test that only asserts "the op ran" passes for a dispatcher that ignores every
    argument. These are the fields a scenario has to vary for the comparison to have teeth, each one
    a place where reading the argument differently changes what happens to the page with no error to
    show for it.

    `js` and `paths` are varied by the divergence entries rather than by a compared scenario: the
    extension refuses both, so there is nothing for the two sides to agree about beyond the refusal
    itself, which is what those entries assert.
    """
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    ops = [case["op"] for case in payload["scenarios"] if isinstance(case["op"], dict)]
    ops += [item["op"] for item in payload["divergences"]]
    fields = {key for op in ops for key in op}

    for field in ("ref", "text", "value", "label", "keys", "key", "state", "dir", "amount", "ms",
                  "js", "url", "confirm", "submit", "clear", "slow", "paths"):
        assert field in fields, f"no scenario varies {field!r}, so nothing checks it is read"

    # The two refusals that must be exercised rather than assumed: a step that stops for
    # confirmation, and a step that stops because the page moved on.
    errors = [case["expected"]["step"].get("error") for case in payload["scenarios"]]
    assert "needs_confirmation" in errors
    assert "detached" in errors, "no scenario covers a ref the page has moved on from"


def test_no_fixture_carries_a_stopwatch():
    """`ms` is a measurement, and a fixture that contains one is a fixture that never matches.

    This cost a debugging round: the expected reports carried `ms`, so the committed file differed
    from what its own generator produced on every run and the "committed == produced" assertion read
    as a stale fixture. The port's `ms` is asserted present by `act-parity.mjs` instead, which is
    where it belongs — the shape is a contract, the number is not.
    """
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))

    for case in payload["scenarios"]:
        assert "ms" not in case["expected"]["step"], (
            f"{case['why']!r} carries a stopwatch reading, which cannot be regenerated")


def test_the_float_family_keeps_the_score_that_json_would_flatten():
    """The report has exactly one float in it, and JSON is where it loses that.

    `_render_act` prints `ms` and `steps`, both of which the server builds with `int(...)`, and the
    replay header prints a resolve score, which `round(score, 3)` leaves a float however integral it
    looks. So the header writes `(1.0)` for a perfect match. JSON keeps no trace of that — it hands
    the extension the number `1` — and the extension printed `(1)` until a live replay of a macro
    that matched perfectly put the two side by side.

    Nothing in this file or in the parity run could have caught it before that: every score the
    fixtures happened to carry was non-integral, where `0.9` and `0.9` agree. So the integral case is
    asserted to still be *in* the family, not merely to be handled, because dropping it would return
    the suite to the state that let the bug through.
    """
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    by_value = {case["value"]: case["expected"] for case in payload["helpers"]["float"]}

    assert 1.0 in by_value, (
        "the integral score is gone from the float family; it is the one a perfect match produces, "
        "and the one JSON flattens to `1`, so nothing would notice the report regressing to `(1)`")
    assert by_value[1.0] == "1.0", by_value[1.0]

    # Every score `_score` can return, and the file has to keep the value as JSON writes it so the
    # lossy step stays visible in the fixture itself.
    reachable = {0.55, 0.6, 0.65, 0.7, 0.75, 0.95, 1.0}
    missing = sorted(reachable - set(by_value))
    assert not missing, f"a score the matcher can return is missing from the family: {missing}"
    assert all(isinstance(case["expected"], str) for case in payload["helpers"]["float"])


def test_a_score_is_the_only_float_either_report_carries():
    """Why the float family has one member and not two: `ms` and `steps` are ints on the server.

    If that ever stops being true, the family needs a second case and this test is where the reason
    is written down. `browser.py` builds `ms` with `int(...)` on both the success and the failure
    path, and `steps` is a counter, so the score is the only number that crosses JSON as a float.
    """
    source = (ROOT / "jev_ultrafast_mcp" / "browser.py").read_text(encoding="utf-8")

    assert source.count("ms=int((time.monotonic() - started) * 1000)") == 2, (
        "`ms` is no longer built with `int(...)` on both paths; if it became a float it needs a "
        "case in FLOAT_CASES too")
    assert "_render_act" in (ROOT / "jev_ultrafast_mcp" / "server.py").read_text(encoding="utf-8")


# --- what the port reproduces on purpose ----------------------------------------------------------


def test_a_scroll_op_discards_the_ref_it_was_given():
    """The dead branch, pinned. `scroll` is not in `REQUIRES_REF`, so nothing ever reads its ref.

    `browser.py` reads a ref into a local only for the ops in `REQUIRES_REF`, and the scroll branch
    that tests `if ref is not None` sits in a place where that local is still `None`. So the branch is
    unreachable, and the documented `scroll [dir], [amount], [ref]` — the op list in
    `server.py::browser_act` says exactly that — does not do what it says: the ref is dropped and the
    viewport centre is scrolled instead of the element being brought into view.

    That matters because of who reads the op list. An agent that follows it and scrolls a ref into
    view gets a page that scrolled somewhere else, a step that reports `ok`, and no reason to look
    again. The port reproduces this rather than fixing it, because a port that quietly improved on
    the original would be a second dispatcher — and this test is the thing that fails the day the
    branch becomes reachable, which is the day somebody has to decide what it should do.
    """
    session, cdp = _scripted_session()
    step = session._run_op({"op": "scroll", "ref": "e5", "amount": 100}, dry_run=False)

    assert step.ok
    assert step.ref is None, "the ref is being read now; the dead branch is reachable"
    assert "ref" not in step.to_dict(), "a dropped ref must not be reported as used"

    methods = [call["method"] for call in cdp.calls]
    assert methods == ["Input.dispatchMouseEvent"], methods
    assert cdp.calls[0]["params"]["type"] == "mouseWheel"
    assert cdp.calls[0]["params"]["deltaY"] == 100  # down
    assert not any("scrollTo" in expression for expression in cdp.expressions), (
        "nothing should be asking the page to scroll the element into view")


def test_the_branch_would_need_the_op_in_the_set_that_reads_a_ref():
    """The precondition of the bug, asserted, so it cannot be fixed by accident in one place.

    `scroll` is absent from `REQUIRES_REF` and `scroll_to` is present without an implementation —
    it demands a ref, runs the guard, and then falls through to "unknown op". So the set has a
    member nobody implements and is missing the member that would make the ref readable, and both
    halves are recorded here rather than in a comment.
    """
    assert "scroll" not in browser.REQUIRES_REF
    assert "scroll_to" in browser.REQUIRES_REF

    session, _ = _scripted_session()
    step = session._run_op({"op": "scroll_to", "ref": "e5"}, dry_run=False)
    assert not step.ok and "unknown op" in (step.detail or ""), step.to_dict()


def test_a_click_does_read_the_ref_it_was_given():
    """The contrast that gives the scroll test its meaning."""
    session, cdp = _scripted_session(guard={"ok": False, "reason": "detached"})
    step = session._run_op({"op": "click", "ref": "e5"}, dry_run=False)

    assert not step.ok and step.error == "detached"
    assert any("verify(" in expression for expression in cdp.expressions)


# --- the divergences ------------------------------------------------------------------------------


def test_the_divergences_from_the_server_are_enumerated():
    """Four places where the extension is deliberately not the server, and no more.

    Each one is a case where matching the server would mean doing something to the user's browser
    that the extension has no business doing: resizing their window, reading a path off their disk,
    reaching a tab they did not point it at, or evaluating JavaScript with no way to check it. If a
    fifth appears it belongs in this list with a reason, and the parity run asserts the behaviour of
    each of these four.
    """
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    divergences = payload["divergences"]

    assert {item["op"]["op"] for item in divergences} == {"scroll", "upload", "tab", "eval"}
    for item in divergences:
        for field in ("field", "python", "extension", "why"):
            assert item.get(field), f"{item['op']['op']} divergence is missing {field!r}"


def test_the_extension_refuses_an_upload_rather_than_pretending():
    """`DOM.setFileInputFiles` takes a path on disk, and an extension cannot read one.

    The server checks the file exists and reports `file not found`. The extension cannot check, so
    it has to say so — the failure mode to avoid is a step that reports success while having attached
    nothing, which is what a silent no-op would look like in a report.
    """
    source = (EXT / "lib" / "session.js").read_text(encoding="utf-8")

    assert "cannot read" in source
    assert "upload needs a path on disk" in source
    # And the port must not have grown a second copy of any of the server's tables.
    assert not (EXT / "lib" / "dispatcher.js").exists()
    assert not (EXT / "lib" / "browser.js").exists()


# --- helpers ---------------------------------------------------------------------------------------


def _scripted_session(**overrides):
    """A `Session` over the fixture generator's scripted CDP, so the dispatcher runs with no browser."""
    cdp = _generator().ScriptedCdp()
    for key, value in overrides.items():
        setattr(cdp, key, value)
    return browser.Session(name="fixture", cfg=Config(), cdp=cdp), cdp


def _generator():
    return _load(TEST / "make_act_fixtures.py", "_jev_act_fixtures")


def _node() -> str | None:
    """Reuse the lookup `test_extension.py` already needed.

    Which node a machine has, and where a managed one hides, is a fact about the environment rather
    than about dispatchers, and the render port had to answer it first.
    """
    return _load(Path(__file__).with_name("test_extension.py"), "_jev_extension_tests")._node()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
