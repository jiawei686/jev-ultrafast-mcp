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
