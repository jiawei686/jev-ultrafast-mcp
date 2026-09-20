"""Tests for the operation vocabulary shared by the decision model and the executor.

The decision model is told which operations it may choose from, and whatever it
chooses comes back to be executed. Those are two ends of one contract, and the
failure mode when they disagree is unlike the usual ones: nothing is
mis-configured, no request fails, the model answers correctly, and the server
then raises `KeyError` on the model's own answer.

That is what happened. `Element.target_kinds()` offered `TYPE` while the
dispatch table was keyed on `TYPE_TEXT` -- the name upstream uses and our own
label table already used. It went unseen because turbo mode had never run, and
it could not be seen by any offline test that did not compare the two ends.

These tests compare the two ends directly. No network, no browser, no key.
"""

from __future__ import annotations

import re
from pathlib import Path

from jev_ultrafast_mcp import policy, server
from jev_ultrafast_mcp.browser import CLICKABLE_KINDS
from jev_ultrafast_mcp.observe import Element, Observation

OBSERVER_JS = Path(__file__).resolve().parents[1] / "jev_ultrafast_mcp" / "js" / "observer.js"

# Guard reasons that are not about stale refs. The element is where it was
# reported to be; it just cannot be acted on right now. Re-observing changes
# nothing, so the goal ends and says which one it was.
NOT_STALE_REASONS = {
    "disabled", "hidden", "no_geometry", "occluded", "out_of_viewport",
    "frame_hidden", "frame_out_of_viewport", "frame_occluded",
    "scroll_failed", "not_a_select", "no_such_option",
}


def _observation(elements: list[Element]) -> Observation:
    return Observation(
        url="https://example.test/",
        title="Example",
        text="Example page",
        elements=elements,
        digest="d",
        text_digest="t",
        page_key="k",
        scroll={"y": 0},
        reachable=len(elements),
        omitted=0,
        overlays=[],
        cross_frames=0,
        cross_frame_srcs=[],
    )


def test_every_offered_operation_maps_to_a_real_act_verb():
    """`OPERATION_TO_ACT` must only name verbs the browser layer dispatches on."""
    # browser.py handles these through an elif chain; CLICKABLE_KINDS is its own
    # declaration of the ref-taking half, so it is the honest source to check
    # against rather than a third copy of the list.
    known = set(CLICKABLE_KINDS) | {"scroll", "wait"}
    assert set(policy.OPERATION_TO_ACT.values()) <= known, (
        f"{set(policy.OPERATION_TO_ACT.values()) - known} would reach no handler"
    )


def test_every_executable_operation_has_a_label():
    """A missing label is not cosmetic: the model sees the bare name as the criterion."""
    assert set(policy.OPERATION_LABELS) == set(policy.OPERATION_TO_ACT), (
        "labels and executable operations must cover the same names, or the model "
        "is offered an option described only by its internal spelling"
    )


def test_an_editable_field_is_offered_as_type_text():
    """The exact regression: the offered name must be the one the executor knows."""
    observation = _observation([Element(ref="e1", role="searchbox", name="Search", editable=True)])
    operations, heads = policy._operation_heads(observation)

    assert "TYPE_TEXT" in operations
    assert "TYPE" not in operations, "the bare name is what raised KeyError"
    assert "e1" == heads["TYPE_TEXT"][0].ref


def test_an_upload_field_is_not_offered():
    """A decision model cannot name a file, so UPLOAD is not put to it.

    `target_kinds()` reports *only* UPLOAD for `<input type=file>` -- its
    `elif` deliberately withholds CLICK, because clicking one opens a native
    picker. So in turbo mode the field is not actionable at all, and that is
    the intent: offering an operation that cannot be carried out is worse than
    offering none, and the element stays available to `browser_act`, where the
    caller supplies the path.
    """
    observation = _observation([Element(ref="e1", role="file", name="Attach a résumé")])
    operations, heads = policy._operation_heads(observation)

    assert operations == set()
    assert "e1" not in {element.ref for group in heads.values() for element in group}


def test_offered_operations_are_always_executable():
    """Whatever the page offers, the model's answer must have a handler to land on."""
    page = [
        Element(ref="e1", role="searchbox", name="Search", editable=True),
        Element(ref="e2", role="checkbox", name="Remember me"),
        Element(ref="e3", role="combobox", name="Country", options=[{"value": "SG"}]),
        Element(ref="e4", role="file", name="Upload"),
        Element(ref="e5", role="link", name="Next", occluded=True),
    ]
    operations, heads = policy._operation_heads(_observation(page))

    assert operations <= set(policy.OPERATION_TO_ACT)
    assert operations == {"TYPE_TEXT", "TOGGLE", "SELECT", "CLICK", "SCROLL", "WAIT"}
    targets = {element.ref for group in heads.values() for element in group}
    assert targets == {"e1", "e2", "e3"}, "occluded elements and text-less file inputs stay out"


def test_a_page_with_nothing_actionable_offers_nothing():
    """No operations means BLOCKED, not an empty choice for the model to answer."""
    operations, _ = policy._operation_heads(_observation([]))

    assert operations == set()


def test_every_guard_reason_is_classified():
    """A reason added in the observer must be decided on, not silently fatal.

    The goal loop recovers from stale refs and ends on everything else, so each
    reason the observer can return falls into one of two buckets. Adding a third
    kind of reason in JavaScript would otherwise land in the "ends the goal"
    bucket by default -- which is exactly how `detached` stopped a goal
    mid-run while looking, from Python, like an ordinary failure.
    """
    reasons = set(re.findall(r"reason: '([a-z_]+)'", OBSERVER_JS.read_text()))

    assert reasons, "the observer's reason vocabulary moved; guard this differently"
    unclassified = reasons - server.STALE_REF_REASONS - NOT_STALE_REASONS
    assert not unclassified, (
        f"{sorted(unclassified)} need a decision: are the refs stale (re-observe) "
        f"or is the action impossible (end the goal)?"
    )
    assert not (server.STALE_REF_REASONS & NOT_STALE_REASONS)


def test_a_menu_trigger_is_offered_hover_beside_click():
    """HOVER is added to a trigger, never swapped in for CLICK.

    A trigger is usually both: a mouse user clicks it, and it is also the only
    way into the menu. Offering just one of the two would make the other
    unreachable, so the observer's flag appends a kind instead of replacing one.
    """
    trigger = Element(ref="e1", role="button", name="今日任务", hoverable=True)

    assert set(trigger.target_kinds()) == {"CLICK", "HOVER"}
    operations, heads = policy._operation_heads(_observation([trigger]))

    assert "HOVER" in operations
    assert "e1" == heads["HOVER"][0].ref
    assert "e1" == heads["CLICK"][0].ref
    assert set(policy.OPERATION_TO_ACT) >= operations


def test_hover_is_absent_without_the_trigger_flag():
    """Nothing earns HOVER by accident: the flag is the only source."""
    plain = Element(ref="e1", role="button", name="Save")

    assert "HOVER" not in plain.target_kinds()
    operations, heads = policy._operation_heads(_observation([plain]))

    assert "HOVER" not in operations
    assert "HOVER" not in heads


def test_the_observer_decides_hoverable_from_aria_alone():
    """The rule that grants HOVER lives in JavaScript, so its inputs are pinned here.

    `hoverable` decides whether the model is offered an operation at all, and
    every offer travels with each step. A looser rule -- a class name, or "it
    looks clickable" -- would buy a case it cannot verify at the price of a
    candidate slot on every page. Adding a third signal is a decision, and one
    worth failing a test over.
    """
    source = OBSERVER_JS.read_text()
    assert "const hoverable" in source, "the observer no longer computes hoverable"

    start = source.index("const expanded = e.getAttribute")
    rule = source[start:][:400]
    assert "const hoverable" in rule, "the window no longer reaches the rule; widen it"
    assert "aria-haspopup" in rule and "aria-expanded" in rule
    assert "hover:" not in rule, (
        "a Tailwind `hover:` variant is usually a colour change, not a menu: "
        "matching it would offer HOVER on nearly every button on the page"
    )


def test_the_observer_and_the_reader_agree_on_the_field_name():
    """A field the observer emits and Python never reads fails silently.

    The flag crosses a language boundary with no type checker on either side, so
    it is asserted from both ends: the observer writes `hoverable`, `from_raw`
    reads it, and an absent key means False rather than an exception.
    """
    marked = Element.from_raw(
        {"ref": "e1", "role": "button", "name": "Tasks", "hoverable": True}
    )
    unmarked = Element.from_raw({"ref": "e2", "role": "button", "name": "Save"})

    assert marked.hoverable and "HOVER" in marked.target_kinds()
    assert not unmarked.hoverable and "HOVER" not in unmarked.target_kinds()


def test_a_retried_target_stops_being_offered_for_that_operation():
    """The loop eight real steps went into, pinned as a test.

    A hover-only trigger answers a click by flipping its own state, so the
    transcript shows a change while the goal stands still, and the model reads
    its own past click as evidence for the next one. Past the threshold the
    target is withdrawn for the operation that is looping -- while the others,
    HOVER most of all, stay on offer, which is what leaves a way out.
    """
    trigger = Element(ref="e8", role="button", name="今日任务", hoverable=True)
    history = [{"op": "click", "ref": "e8", "ok": True}] * 3

    assert policy.stalled_targets(history) == {"CLICK": {"e8"}}

    _, heads = policy._operation_heads(_observation([trigger]))
    policy.withdraw_stalled(heads, history)

    assert [element.ref for element in heads["HOVER"]] == ["e8"], "the way in stays on offer"
    assert [element.ref for element in heads["CLICK"]] == ["e8"], (
        "dropping the only CLICK candidate would turn a loop into a dead end")


def test_a_retried_target_is_dropped_when_another_one_can_take_its_place():
    """With somewhere else to go, the looping target goes."""
    looping = Element(ref="e1", role="button", name="Next")
    other = Element(ref="e2", role="button", name="Back")
    history = [{"op": "click", "ref": "e1", "ok": True}] * 3

    _, heads = policy._operation_heads(_observation([looping, other]))
    policy.withdraw_stalled(heads, history)

    assert [element.ref for element in heads["CLICK"]] == ["e2"]


def test_a_retry_below_the_threshold_is_left_alone():
    """Two repeats can be an ordinary two-step control; three are a loop.

    `WAIT` and `SCROLL` carry no ref, and a history entry without one cannot say
    which target stalled, so neither is ever withdrawn.
    """
    history = [{"op": "click", "ref": "e8", "ok": True}] * 2

    assert policy.stalled_targets(history) == {}
    assert policy.stalled_targets([]) == {}
    assert policy.stalled_targets([{"op": "click"}] * 5) == {}
    assert policy.stalled_targets([{"op": "wait"}] * 5) == {}


def test_what_the_page_just_added_is_not_the_first_thing_cut():
    """The truncation that hid the target of a real task, pinned as a test.

    A menu opens *after* the element table was built, so its items sort last in
    document order while the trigger that opened them sorts first. Cutting the
    candidate list at the limit in document order therefore drops the answer and
    keeps the step already taken -- on a real check-in that entry was candidate
    189 of 195, in the viewport, unoccluded, and never offered.
    """
    trigger = Element(ref="e1", role="button", name="Tasks", hoverable=True)
    offscreen = [Element(ref=f"e{i}", role="link", name=f"Post {i}", in_viewport=False)
                 for i in range(2, 200)]
    appeared = Element(ref="e200", role="menuitem", name="Check in")
    candidates = [trigger, *offscreen, appeared]

    kept = policy.reachable_first(candidates, limit=20)
    refs = [element.ref for element in kept]

    assert "e200" in refs, "the element that just appeared lost its place to off-screen links"
    assert len(refs) == 20
    assert refs == sorted(refs, key=lambda ref: int(ref[1:])), (
        "the kept set is still shown in document order; only the choice of what to "
        "keep is allowed to change")

    short = candidates[:5]
    assert policy.reachable_first(short) == short, "a list under the limit is returned as it is"
