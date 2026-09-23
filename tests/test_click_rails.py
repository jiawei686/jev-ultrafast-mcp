"""Two ops click, and the confirmation rail was written against one of them.

`_do_click` has exactly two callers: the `click` op, which consults
`confirm_reason`, and the `toggle` op, which did not. So `{"op": "toggle", "ref":
<a button named "Delete account">}` pressed it with no question asked. This is the
`keys` finding one level down: there, a text rail was attached to the op that is
*spelled* `type` rather than to the act of typing; here a click rail is attached
to the op spelled `click` rather than to the act of clicking. A guard with two
call sites is a guard with one.

The same branches had two more holes with the same cause, and one repair closes
all three -- both rails now read the element the *server* observed.

**The op's `role` was what the rails read.** `confirm_reason` uses the role to
decide whether the name is an action name at all, and it was being handed the
caller's claim about the target rather than a fact about it: `role: "checkbox"`
on a button named "Delete account" returned None and lifted the rail outright.
`is_secret` only ever *adds* strictness for a role, so there the same claim
produced the opposite error -- a refusal nobody asked for, over an ordinary
field. Nothing in this repo ever sent one, which is the only reason it was
latent rather than live; the role now comes from the observation, which the
guard has already re-checked against the page.

**The `type` rail read only the label.** A password field with no accessible
name -- the ordinary shape of a login form -- was typed into with no
confirmation while `observe.py` still masked its value. The mask was wider than
the rail that is supposed to stop and ask, so the property this file pins is
that the two are the same width, measured through the renderer the agent
actually reads.

`chrome-extension/test/act-fixtures.json` covers the name-and-role half on both
sides of the port. It cannot cover the element-derived half, because its
scripted page carries no actions at all -- deliberately, so that both sides are
in the same state. That half is here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jev_ultrafast_mcp.browser import Session
from jev_ultrafast_mcp.config import Config
from jev_ultrafast_mcp.observe import Observation
from jev_ultrafast_mcp.safety import is_secret

REPO = Path(__file__).resolve().parents[1]
BROWSER_PY = REPO / "jev_ultrafast_mcp" / "browser.py"
SESSION_JS = REPO / "chrome-extension" / "lib" / "session.js"

PASSWORD = "hunter2"

# A settings row: the same name on a button, and on a checkbox. The button is a
# click this has to stop for; the checkbox is not, which is `confirm_reason`'s
# own gate and the reason it takes a role at all.
BUTTON = {"ref": "e5", "role": "button", "name": "Delete account"}
CHECKBOX = {"ref": "e5", "role": "checkbox", "name": "Delete account"}

# A login form as a page really reports it. `e9` is `<input type=password>` with
# no label, no placeholder and no aria-label; `e10` is the same field with a
# name; `e11` is the near miss the word-bounded detector exists for.
LOGIN = [
    {"ref": "e9", "role": "textbox", "name": "", "value": PASSWORD,
     "editable": True, "secret": True},
    {"ref": "e10", "role": "textbox", "name": "Password", "value": PASSWORD, "editable": True},
    {"ref": "e11", "role": "textbox", "name": "Passenger", "value": "1 adult", "editable": True},
    {"ref": "e12", "role": "textbox", "name": "Where from?", "value": "Zurich", "editable": True},
]
LOGIN_LABELS = {"e9": "", "e10": "Password", "e11": "Passenger", "e12": "Where from?"}


def _raw(*actions: dict) -> dict:
    return {"url": "https://example.com/settings", "actions": list(actions)}


class ScriptedPage:
    """Answers `label(ref)` per ref, and records everything that reached the page.

    Per ref, because `label(ref)` is a live query about *that* element. A fake
    with one label for every ref is the kind that cannot disagree with the code
    it is testing, and the first version of this probe was exactly that: it
    reported the named password field as ungated when the field was gated and
    the fake was answering `""` for every ref.
    """

    def __init__(self, labels: dict[str, str]) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.expressions: list[str] = []
        # `_after_input` clears this to count in-flight requests, so a fake
        # without it fails on any op that reaches the end of its branch.
        self.events: list[dict] = []
        self.labels = labels

    def call(self, method: str, session_id: str | None = None,
             timeout: float | None = None, **params: object) -> dict:
        self.calls.append((method, params))
        if method == "Target.getTargets":
            return {"targetInfos": []}
        return {}

    def evaluate(self, expression: str, session_id: str | None = None,
                 await_promise: bool = False, timeout: float | None = None):
        self.expressions.append(expression)
        if "verify(" in expression or "reinspect(" in expression:
            return {"ok": True}
        if "resolve(" in expression:
            return {"ok": True, "x": 10, "y": 20}
        if "label(" in expression:
            for ref, label in self.labels.items():
                if expression.endswith(f'"{ref}")'):
                    return label
            return ""
        # `_after_input` reads this to decide whether it must wait for a
        # navigation, and a page that never answers "complete" spends
        # `cfg.nav_timeout` per op -- twenty seconds a test.
        if "readyState" in expression:
            return "complete"
        if "settle(" in expression:
            return None
        if "__jevRefs.nodes.get(" in expression:
            return False
        return None

    def clicks(self) -> int:
        return sum(1 for method, params in self.calls
                   if method == "Input.dispatchMouseEvent"
                   and params.get("type") == "mousePressed")

    def typed(self) -> list[str]:
        return [params["text"] for method, params in self.calls
                if method == "Input.insertText"]

    def asked_for_a_label(self) -> bool:
        return any("label(" in expression for expression in self.expressions)


def _session(raw: dict | None = None,
             labels: dict[str, str] | None = None) -> tuple[Session, ScriptedPage]:
    driver = ScriptedPage(labels or {})
    session = Session("test", Config(), driver)
    session.page_session = "session-1"
    if raw is not None:
        # The same construction `Session.observe` uses, so the element table
        # these rails read is the one a live run builds.
        session.last = Observation.from_raw(
            raw, mask_secrets=lambda name, role: is_secret(session.cfg, name, role))
    return session, driver


def _run(session: Session, op: dict, **kwargs):
    return session._run_op(op, dry_run=False, **kwargs)


# --- the rail that was missing: `toggle` clicks too ----------------------------------------


def test_toggle_is_refused_on_a_confirmation_rule() -> None:
    session, driver = _session(_raw(BUTTON), {"e5": "Delete account"})
    step = _run(session, {"op": "toggle", "ref": "e5", "state": True})
    assert step.ok is False
    assert step.error == "needs_confirmation"
    assert driver.clicks() == 0, "the click happened anyway"


def test_the_toggle_refusal_is_liftable_by_confirm() -> None:
    """A rail, not a wall: `confirm` is the way through, and it is one word."""
    session, driver = _session(_raw(BUTTON), {"e5": "Delete account"})
    step = _run(session, {"op": "toggle", "ref": "e5", "state": True, "confirm": True})
    assert step.ok is True, step.detail
    assert driver.clicks() == 1


def test_the_toggle_refusal_names_the_way_out() -> None:
    session, _ = _session(_raw(BUTTON), {"e5": "Delete account"})
    step = _run(session, {"op": "toggle", "ref": "e5", "state": True})
    assert '"confirm": true' in step.detail
    assert "delete" in step.detail.lower(), "the reason is not in the message"


def test_a_toggle_already_in_the_requested_state_asks_for_no_label() -> None:
    """The one path in this op that clicks nothing, and so needs no rail.

    Pinned because the rail was added *after* this early return rather than
    before it: a refusal costs no page work, and a step that is not going to
    click should not start reading the page to decide whether it may.
    """
    session, driver = _session(_raw(BUTTON), {"e5": "Delete account"})
    step = _run(session, {"op": "toggle", "ref": "e5", "state": False})
    assert step.detail == "already in requested state"
    assert driver.asked_for_a_label() is False
    assert driver.clicks() == 0


@pytest.mark.parametrize("name, role, refused", [
    ("Delete account", "button", True),
    ("Buy now", "button", True),
    ("Search", "button", False),
    # `confirm_reason`'s own gate, unchanged and now actually reachable: a
    # checkbox named "Delete account" is not a click this has to stop for.
    ("Delete account", "checkbox", False),
])
def test_the_two_ops_agree_across_names_and_roles(name: str, role: str, refused: bool) -> None:
    seen = {}
    for op in ("click", "toggle"):
        element = {"ref": "e5", "role": role, "name": name}
        session, driver = _session(_raw(element), {"e5": name})
        step = _run(session, {"op": op, "ref": "e5", "state": True})
        seen[op] = step.error == "needs_confirmation"
        assert driver.clicks() == (0 if refused else 1), f"{op} on {name!r} ({role})"
    assert seen == {"click": refused, "toggle": refused}, seen


# --- the op's `role` is a claim, not a fact -------------------------------------------------


@pytest.mark.parametrize("given", [{}, {"role": "checkbox"}, {"role": "textbox"},
                                   {"role": "password"}])
def test_a_role_on_the_op_does_not_lift_the_click_rail(given: dict) -> None:
    """Every one of these roles is outside `confirm_reason`'s allowlist.

    Supplied by the caller they all returned None -- no confirmation -- for a
    button whose name matches a destructive rule. The rail reads the observed
    element's role now, so the claim in the request decides nothing.
    """
    session, driver = _session(_raw(BUTTON), {"e5": "Delete account"})
    step = _run(session, {"op": "click", "ref": "e5", **given})
    assert step.error == "needs_confirmation"
    assert driver.clicks() == 0


def test_the_observed_role_is_what_decides_the_click_rail() -> None:
    """Both directions, from the same name and the same op."""
    session, _ = _session(_raw(BUTTON), {"e5": "Delete account"})
    assert _run(session, {"op": "click", "ref": "e5"}).error == "needs_confirmation"

    session, driver = _session(_raw(CHECKBOX), {"e5": "Delete account"})
    step = _run(session, {"op": "click", "ref": "e5"})
    assert step.ok is True, step.detail
    assert driver.clicks() == 1


def test_a_role_on_the_op_does_not_refuse_an_ordinary_field() -> None:
    """The other direction of the same mistake: `is_secret` only adds strictness.

    So an op claiming `"password"` refused a field called "Where from?" -- a
    denial nobody asked for, from a word in the request.
    """
    session, driver = _session(_raw(*LOGIN), LOGIN_LABELS)
    step = _run(session, {"op": "type", "ref": "e12", "text": "Basel", "role": "password"})
    assert step.ok is True, step.detail
    assert driver.typed() == ["Basel"]


# --- the rail that was too narrow: the page's own flag ---------------------------------------


def test_an_unlabelled_secret_field_is_gated() -> None:
    """`e9` has no accessible name at all, which is the ordinary login form.

    The observer flagged it `secret` and the table masked its value, while this
    rail -- which read the label and nothing else -- saw an empty string and let
    the value through with no confirmation.
    """
    session, driver = _session(_raw(*LOGIN), LOGIN_LABELS)
    step = _run(session, {"op": "type", "ref": "e9", "text": PASSWORD})
    assert step.error == "needs_confirmation", step.detail
    assert driver.typed() == []


def test_the_typing_refusal_is_liftable_by_confirm() -> None:
    session, driver = _session(_raw(*LOGIN), LOGIN_LABELS)
    step = _run(session, {"op": "type", "ref": "e9", "text": PASSWORD, "confirm": True})
    assert step.ok is True, step.detail
    assert driver.typed() == [PASSWORD]


@pytest.mark.parametrize("element", LOGIN, ids=lambda item: item["name"] or "(no name)")
def test_the_rail_is_as_wide_as_the_mask(element: dict) -> None:
    """The property, measured through the renderer the agent reads.

    `Element.render` writes `«hidden»` over an editable field's value when the
    element is secret. That is the disclosure the mask promises, and this rail
    is the other half of the same promise -- so the two must switch on together,
    for every field, including the near miss "Passenger" that must not match.
    """
    session, driver = _session(_raw(*LOGIN), LOGIN_LABELS)
    observed = session.last.by_ref[element["ref"]]
    masked = "\u00abhidden\u00bb" in observed.render()

    step = _run(session, {"op": "type", "ref": element["ref"], "text": "x"})
    refused = step.error == "needs_confirmation"
    assert refused is masked, (
        f"{element['ref']} ({element['name']!r}) is "
        f"{'masked' if masked else 'shown'} in the table but the rail "
        f"{'refused' if refused else 'allowed'} it"
    )
    assert driver.typed() == ([] if masked else ["x"])


def test_the_near_miss_is_not_caught() -> None:
    """`Passenger` is not `Password`, and the rail has to keep that distinction.

    Cheap to state and the whole reason the detector is word-bounded; a rail
    that refused it would be the same bug in the other direction.
    """
    session, driver = _session(_raw(*LOGIN), LOGIN_LABELS)
    step = _run(session, {"op": "type", "ref": "e11", "text": "1 adult"})
    assert step.ok is True, step.detail
    assert driver.typed() == ["1 adult"]


# --- the mechanism, at the source, so a third clicking op cannot be added quietly -------------

BRANCH = re.compile(r'\n            (?:el)?if op == "([a-z_]+)":')


def _branches() -> dict[str, str]:
    """The op dispatcher's branches, keyed by the op they handle."""
    source = BROWSER_PY.read_text(encoding="utf-8")
    marks = list(BRANCH.finditer(source))
    return {
        mark.group(1): source[mark.end(): marks[index + 1].start()
                              if index + 1 < len(marks) else len(source)]
        for index, mark in enumerate(marks)
    }


def test_every_call_site_of_a_click_consults_the_rail() -> None:
    """The guard is attached to clicking, and this is what says so.

    `_do_click` is where a click happens. If a third op ever clicks, it has to
    consult `_click_refusal` on the way -- and if it does not, this fails and
    names the branch, rather than shipping a second unguarded door.
    """
    clicking = {name: body for name, body in _branches().items() if "self._do_click(" in body}
    assert set(clicking) == {"click", "toggle"}, (
        f"the ops that click have changed: {sorted(clicking)}. A new one needs the rail too.")
    for name, body in clicking.items():
        assert "self._click_refusal(" in body, (
            f"the {name!r} branch clicks without consulting the confirmation rail")


def test_the_extension_does_not_read_a_role_from_the_op_either() -> None:
    """Two implementations disagreeing about one op is the failure this guards.

    The extension is the third port and its fixtures compare it to Python field
    for field, so it has to stop reading the same claim in the same commit.
    """
    source = SESSION_JS.read_text(encoding="utf-8")
    assert "rawOp.role" not in source, "the extension still trusts a role on the op"
    # Two *call* sites, not two mentions: the definition line carries the same
    # text, which is what this assertion counted the first time it ran.
    assert source.count("const blocked = clickRefusal(ref, target);") == 2, (
        "the extension has one clicking op guarded and one not")
    assert "typingRefusal(ref, target)" in source
