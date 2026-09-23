"""Every check type, and the malformed checks that must not pass.

`browser_assert` is the one part of the product that judges a run without asking the
model, and `browser_goal`'s `verify` is evaluated by the same code -- so a passing
assertion is what overrules the model's own claim of success. That makes the interesting
cases here the ones where the check was never given what it needs to match, because
those are the ones that used to pass by accident: `"" in s` is true for every string, so
a missing key read as an empty needle matched everything.

Five of these eleven types had never been executed by any test or by `smoke.py` --
`url_contains`, `title_matches`, `value_equals`, `checked` and `js` -- which is where
both defects were.
"""

from __future__ import annotations

import pytest

from jev_ultrafast_mcp import assertions
from jev_ultrafast_mcp.observe import Element, Observation

# Every name in `assertions.CHECK_TYPES`, listed once so that adding a type without a
# case here fails `test_the_cases_here_cover_the_whole_vocabulary` rather than quietly
# shipping an untested check.
EXERCISED = frozenset({
    "url_matches", "url_contains", "title_matches", "text_contains", "text_absent",
    "element_exists", "element_gone", "value_equals", "checked", "count_at_least", "js",
})

ELEMENTS = [
    Element(ref="e1", role="button", name="Continue"),
    Element(ref="e2", role="textbox", name="City", value="Zurich"),
    Element(ref="e3", role="checkbox", name="I agree", checked=True),
    Element(ref="e4", role="link", name="Home"),
    Element(ref="e5", role="link", name="Orders"),
]


def observation(**over) -> Observation:
    fields = dict(
        url="https://example.com/dashboard", title="Dashboard", text="Order confirmed",
        elements=list(ELEMENTS), digest="d", text_digest="t", page_key="k", scroll={},
        reachable=len(ELEMENTS), omitted=0, overlays=[], cross_frames=0, cross_frame_srcs=[],
    )
    fields.update(over)
    return Observation(**fields)


def one(check: dict, **over) -> dict:
    return assertions.run([check], observation(**over))["checks"][0]


def passed(check: dict, **over) -> bool:
    return one(check, **over)["ok"]


def test_the_cases_here_cover_the_whole_vocabulary():
    assert EXERCISED == frozenset(assertions.CHECK_TYPES)


def test_a_verdict_is_the_whole_list_and_not_the_last_check():
    """`pass` is false if any check failed, and false for an empty list."""
    both = assertions.run(
        [{"type": "text_contains", "text": "Order confirmed"},
         {"type": "text_contains", "text": "refunded"}],
        observation())
    assert both["pass"] is False
    assert [check["ok"] for check in both["checks"]] == [True, False]
    assert assertions.run([], observation())["pass"] is False


# --------------------------------------------------------------- the eleven types

def test_url_matches_globs_the_url():
    assert passed({"type": "url_matches", "pattern": "*/dashboard"})
    assert not passed({"type": "url_matches", "pattern": "*/settings"})


def test_url_contains_is_a_substring_test():
    assert passed({"type": "url_contains", "text": "dashboard"})
    assert passed({"type": "url_contains", "text": "DASHBOARD"})
    assert not passed({"type": "url_contains", "text": "settings"})


def test_title_matches_takes_a_glob_or_a_substring():
    assert passed({"type": "title_matches", "pattern": "Dash*"})
    assert passed({"type": "title_matches", "pattern": "board"})
    assert not passed({"type": "title_matches", "pattern": "Checkout"})


def test_text_contains_and_text_absent_are_opposites():
    assert passed({"type": "text_contains", "text": "confirmed"})
    assert not passed({"type": "text_contains", "text": "refunded"})
    assert passed({"type": "text_absent", "text": "refunded"})
    assert not passed({"type": "text_absent", "text": "confirmed"})


def test_text_contains_can_be_a_regex():
    assert passed({"type": "text_contains", "text": r"Order\s+confirmed", "regex": True})
    assert not passed({"type": "text_contains", "text": r"^refunded", "regex": True})


def test_element_exists_and_element_gone_by_ref():
    assert passed({"type": "element_exists", "ref": "e1"})
    assert not passed({"type": "element_exists", "ref": "e99"})
    assert passed({"type": "element_gone", "ref": "e99"})
    assert not passed({"type": "element_gone", "ref": "e1"})


def test_element_exists_by_role_and_name():
    assert passed({"type": "element_exists", "role": "button", "name": "Continue"})
    assert not passed({"type": "element_exists", "role": "button", "name": "Absent"})
    assert not passed({"type": "element_exists", "role": "spinbutton", "name": "Continue"})


def test_value_equals_reads_the_field():
    assert passed({"type": "value_equals", "ref": "e2", "value": "Zurich"})
    assert not passed({"type": "value_equals", "ref": "e2", "value": "Bern"})
    assert not passed({"type": "value_equals", "ref": "e99", "value": "Zurich"})


def test_checked_reads_the_box():
    assert passed({"type": "checked", "ref": "e3", "state": True})
    assert not passed({"type": "checked", "ref": "e3", "state": False})
    assert not passed({"type": "checked", "ref": "e99", "state": True})


def test_count_at_least_counts_matches():
    assert passed({"type": "count_at_least", "role": "link", "min": 2})
    assert not passed({"type": "count_at_least", "role": "link", "min": 5})
    assert passed({"type": "count_at_least", "role": "button", "name": "Cont", "min": 1})


def test_js_is_refused_unless_it_is_switched_on():
    check = {"type": "js", "expr": "document.title.length > 3"}
    refused = one(check)
    assert refused["ok"] is False
    assert "disabled" in refused["detail"]
    assert assertions.run([check], observation(), allow_js=True,
                          eval_js=lambda expr: True)["checks"][0]["ok"] is True
    assert assertions.run([check], observation(), allow_js=True,
                          eval_js=lambda expr: False)["checks"][0]["ok"] is False


def test_js_needs_an_expression_even_when_it_is_allowed():
    result = assertions.run([{"type": "js"}], observation(), allow_js=True,
                            eval_js=lambda expr: True)["checks"][0]
    assert result["ok"] is False
    assert "needs 'expr'" in result["detail"]


def test_an_unknown_type_fails_and_says_what_is_supported():
    result = one({"type": "url_equals"})
    assert result["ok"] is False
    assert "unknown check type" in result["detail"]
    assert "url_matches" in result["detail"]


def test_a_check_that_raises_is_a_failed_check_not_a_crash():
    """A bad check must not take the whole verdict down with it."""
    result = one({"type": "count_at_least", "role": "link", "min": "not a number"})
    assert result["ok"] is False
    assert "check raised ValueError" in result["detail"]


# ------------------------------------------------ the checks that matched everything

@pytest.mark.parametrize("kind,keys", [
    ("url_contains", ("text",)),
    ("title_matches", ("pattern", "text")),
    ("text_contains", ("text",)),
    ("text_absent", ("text",)),
])
def test_a_missing_needle_fails_instead_of_matching_every_string(kind, keys):
    """`"" in s` is true for every `s`, so an empty needle must be refused.

    This is the failure an assertion cannot have. A caller who writes the wrong key --
    `{"type": "url_contains", "value": "dashboard"}` -- got `contains '': True`, and
    `verify` then reported the handoff as verified without having looked at anything.
    """
    for check in ({**{"type": kind}}, {"type": kind, "value": "dashboard"},
                  {"type": kind, keys[0]: ""}):
        result = one(check)
        assert result["ok"] is False, f"{check} matched everything"
        assert "needs a non-empty" in result["detail"], result["detail"]


def test_checked_refuses_to_guess_which_state_was_meant():
    """The argument used to default to True, so saying nothing asserted "checked"."""
    result = one({"type": "checked", "ref": "e3"})
    assert result["ok"] is False
    assert "needs 'state'" in result["detail"]


def test_checked_accepts_the_key_its_own_type_name_suggests():
    """The type is `checked` and the argument is `state`, so `checked` is the guess."""
    assert passed({"type": "checked", "ref": "e3", "checked": True})
    assert not passed({"type": "checked", "ref": "e3", "checked": False})


@pytest.mark.parametrize("raw,expected", [
    ("false", False), ("FALSE", False), (" false ", False), ("0", False),
    ("no", False), ("off", False), ("", False),
    ("true", True), ("1", True), (True, True), (False, False),
])
def test_a_string_state_is_read_as_a_bool(raw, expected):
    """`bool("false")` is True, and a model writing JSON by hand sends strings."""
    assert passed({"type": "checked", "ref": "e3", "state": raw}) is expected


def test_the_state_key_wins_when_both_are_given():
    assert passed({"type": "checked", "ref": "e3", "state": True, "checked": False})
