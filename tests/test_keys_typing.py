"""`keys` is a second way to type, and every text rail was written against `type` alone.

`_dispatch_keys` sends a bare single character with `Input.insertText` rather than as
a key press. That is a reasonable thing for it to do and it is documented nowhere, so
`keys` was a way of putting text on the page that no other part of the server knew
about. Two consequences, both from one op:

- `type` demands `confirm` before it will write into a field whose value must not leave
  the page, and `keys` did not. A password could be filled a character at a time with
  no confirmation at all -- `{"op": "keys", "keys": ["h", "u", ...]}`.
- `_record` replaces a secret `type`'s text with `{{secret}}` before it reaches disk,
  and it keys off `op == "type"`. A `keys` op was recorded verbatim, so a recording
  that filled a password wrote the password into the macro file.

`docs/DESIGN.md` claimed both rails held for password fields. They held for one of the
two ops that can fill one. The op is refused now rather than confirmed, and the tests
below pin the refusal, its narrowness, and the thing that makes it checkable at all:
`_inserts_text` and `_dispatch_keys` have to answer the same question the same way.

Two further shapes came out of the same branch. `{"keys": 5}` raised `TypeError`
*through* `_run_op`'s except clause -- the fourth refusal in this codebase to escape as
a protocol-level crash, and a divergence from the extension, which reports it as
`invalid_request`. And `{"keys": {"a": 1}}` was worse than either: a dict iterates, so
it pressed "a" and reported success.
"""

from __future__ import annotations

import pytest

from jev_ultrafast_mcp.browser import Session, _inserts_text, _key_parts
from jev_ultrafast_mcp.config import Config
from jev_ultrafast_mcp.observe import Observation

PASSWORD = "hunter2"

# What the page says has focus, in each of the three shapes `active()` answers with.
ORDINARY = {"focused": True, "name": "Search", "role": "searchbox", "secret": False}
SECRET_BY_FLAG = {"focused": True, "name": "Sign in", "role": "textbox", "secret": True}
# The page's flag is false and the server's own list is the one that matches: the
# table masks on the union of the two, so this rail has to read the union as well.
SECRET_BY_NAME = {"focused": True, "name": "API key", "role": "textbox", "secret": False}
NOTHING_FOCUSED = {"focused": False}
WILL_NOT_SAY = {"unknown": True}


class RecordingCdp:
    """Records what reached the page, and answers the focus question from a script."""

    def __init__(self, focused: dict) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.focused = focused
        self.events: list[dict] = []

    def call(self, method: str, session_id: str | None = None,
             timeout: float | None = None, **params: object) -> dict:
        self.calls.append((method, params))
        return {}

    def evaluate(self, expression: str, session_id: str | None = None,
                 await_promise: bool = False, timeout: float | None = None):
        if "active(" in expression:
            return self.focused
        # `_after_input` reads this to decide whether it has to wait for a
        # navigation, and a page that never answers "complete" spends
        # `cfg.nav_timeout` per op -- twenty seconds a test, which is how this
        # fake was found.
        if "readyState" in expression:
            return "complete"
        return None

    def inserted(self) -> list[str]:
        return [params["text"] for method, params in self.calls if method == "Input.insertText"]

    def pressed(self) -> list[dict]:
        return [params for method, params in self.calls if method == "Input.dispatchKeyEvent"]


def _session(focused: dict = NOTHING_FOCUSED) -> tuple[Session, RecordingCdp]:
    driver = RecordingCdp(focused)
    session = Session("test", Config(), driver)
    session.page_session = "session-1"
    return session, driver


def _run(session: Session, op: dict, **kwargs):
    return session._run_op(op, dry_run=False, **kwargs)


# --- what the op does, before any rail is applied -----------------------------------------


def test_a_bare_character_is_typed_rather_than_pressed():
    """The mechanism the whole finding rests on. If this changes, the rail moves with it."""
    session, driver = _session()

    step = _run(session, {"op": "keys", "keys": "a"})

    assert step.ok, step.to_dict()
    assert driver.inserted() == ["a"]
    assert driver.pressed() == [], "a bare character was dispatched as a key press"


def test_a_named_key_is_pressed_rather_than_typed():
    session, driver = _session()

    step = _run(session, {"op": "keys", "key": "Enter"})

    assert step.ok, step.to_dict()
    assert driver.inserted() == []
    assert [params["key"] for params in driver.pressed()] == ["Enter", "Enter"]


def test_a_combination_is_pressed_rather_than_typed():
    session, driver = _session()

    step = _run(session, {"op": "keys", "keys": "ctrl+a"})

    assert step.ok, step.to_dict()
    assert driver.inserted() == []
    assert driver.pressed(), "ctrl+a dispatched nothing"


# --- the predicate and the dispatch have to agree -----------------------------------------


@pytest.mark.parametrize("combo, inserts", [
    ("a", True),
    ("7", True),
    ("Z", True),
    ("Enter", False),
    ("Tab", False),
    ("Escape", False),
    ("ctrl+a", False),
    ("meta+shift+k", False),
    ("", False),
])
def test_the_predicate_answers_what_the_dispatch_actually_does(combo, inserts):
    """Two statements of one fact, so they get a test rather than a comment.

    `_inserts_text` is what the `keys` op consults to decide whether it is about to
    type into a focused field; `_dispatch_keys` is what decides whether it actually
    does. A branch changed on one side and not the other would put the rail behind a
    door the text no longer goes through -- which is exactly how the hole opened the
    first time, one layer down.
    """
    session, driver = _session()

    session._dispatch_keys(combo)

    assert bool(driver.inserted()) is inserts, (
        f"{combo!r}: the predicate says {inserts}, the dispatch did "
        f"{'type' if driver.inserted() else 'press'}")
    assert _inserts_text(*_key_parts(combo)) is inserts


# --- the rail ------------------------------------------------------------------------------


@pytest.mark.parametrize("focused", [SECRET_BY_FLAG, SECRET_BY_NAME],
                         ids=["the page flags the field", "the server's list matches the name"])
def test_typing_into_a_secret_field_with_keys_is_refused(focused):
    """The hole. A password went in a character at a time, with nothing asked."""
    session, driver = _session(focused)

    step = _run(session, {"op": "keys", "keys": list(PASSWORD)})

    assert step.ok is False, f"the password was typed: {driver.inserted()}"
    assert step.error == "blocked_by_policy", step.to_dict()
    assert driver.inserted() == [], "something reached the page before the refusal"


def test_the_refusal_is_not_liftable_by_confirm():
    """`needs_confirmation` would be a lie here, and the difference is the whole design.

    A confirmed `keys` op would still have to be recorded, and a macro stores a
    character sequence -- `{{secret}}` is one string, so there is no placeholder a
    replay could put the characters back through. `type` has both halves: it takes a
    ref, it asks, and it writes the placeholder. So the answer is that op, not this
    one with a flag.
    """
    session, driver = _session(SECRET_BY_FLAG)

    step = _run(session, {"op": "keys", "keys": list(PASSWORD), "confirm": True})

    assert step.ok is False, "confirm lifted a refusal that has nothing to lift"
    assert step.error == "blocked_by_policy", step.to_dict()
    assert driver.inserted() == []


def test_the_refusal_names_the_op_that_can_do_it():
    """A refusal that does not name the alternative is a dead end for a model."""
    session, _ = _session(SECRET_BY_FLAG)

    step = _run(session, {"op": "keys", "keys": "a"})

    detail = step.detail or ""
    assert "`type`" in detail, detail
    assert "confirm" in detail, detail


def test_a_page_that_will_not_say_what_is_focused_is_refused():
    """`{unknown: true}` is not `{secret: false}`.

    The helper is missing, the context is mid-navigation, or focus sits in a frame the
    document cannot read. "I could not look" is not a claim about the field.
    """
    session, driver = _session(WILL_NOT_SAY)

    step = _run(session, {"op": "keys", "keys": "a"})

    assert step.ok is False
    assert step.error == "blocked_by_policy", step.to_dict()
    assert driver.inserted() == []


def test_nothing_focused_is_an_answer_and_not_a_refusal():
    """The text would go nowhere. That is the page's answer, and it is not suspicious."""
    session, driver = _session(NOTHING_FOCUSED)

    step = _run(session, {"op": "keys", "keys": "a"})

    assert step.ok, step.to_dict()
    assert driver.inserted() == ["a"]


def test_typing_into_an_ordinary_field_is_still_allowed():
    session, driver = _session(ORDINARY)

    step = _run(session, {"op": "keys", "keys": "a"})

    assert step.ok, step.to_dict()
    assert driver.inserted() == ["a"]


@pytest.mark.parametrize("combo", ["Enter", "Tab", "Escape", "ctrl+a"])
def test_a_key_press_is_untouched_while_a_password_field_has_focus(combo):
    """The rail is narrow on purpose: submitting a login form is the next step after
    filling it, and a rail that stopped there would break the flow it exists to guard."""
    session, driver = _session(SECRET_BY_FLAG)

    step = _run(session, {"op": "keys", "keys": combo})

    assert step.ok, step.to_dict()
    assert driver.inserted() == []


# --- the other half: what reaches the macro file -------------------------------------------


def test_a_refused_keystroke_is_not_recorded():
    """The masking rule keys off `op == "type"`, so this op had to be stopped before it.

    `_record` replaces a secret `type`'s text with `{{secret}}`. It cannot do that for
    a `keys` step, because the payload is a character sequence and the placeholder is a
    single string -- which is why the op is refused rather than masked.
    """
    session, _ = _session(SECRET_BY_FLAG)
    session.last = Observation.from_raw({"url": "https://example.com/login", "actions": []})
    session._recording = True

    step = _run(session, {"op": "keys", "keys": list(PASSWORD)})

    assert step.ok is False
    assert session._recorder == [], f"a refused op was recorded: {session._recorder}"


# --- the crash, which was a separate way of getting it wrong -------------------------------


@pytest.mark.parametrize("payload", [5, 5.0, True, {"a": 1}])
def test_a_payload_that_is_not_a_string_or_a_list_is_refused_rather_than_raised(payload):
    """`for key in sequence` raised `TypeError` straight through the except clause.

    A number is not iterable, so the tool crashed at the protocol level -- which a host
    cannot tell apart from a bug in this server. `{"a": 1}` was quieter and worse: a
    dict iterates, so it pressed "a" and reported success.
    """
    session, _ = _session()

    step = _run(session, {"op": "keys", "keys": payload})

    assert step.ok is False, step.to_dict()
    assert step.error == "invalid_request", step.to_dict()
    assert "string or a list" in (step.detail or ""), step.detail


def test_a_list_payload_is_still_a_list_of_keys():
    session, driver = _session()

    step = _run(session, {"op": "keys", "keys": ["Enter", "Tab"]})

    assert step.ok, step.to_dict()
    assert [params["key"] for params in driver.pressed()] == ["Enter", "Enter", "Tab", "Tab"]


def test_the_refusal_survives_a_batch():
    """The op is reached through `act`, which is what a tool call actually uses."""
    session, driver = _session(SECRET_BY_FLAG)
    session.last = Observation.from_raw({"url": "https://example.com/login", "actions": []})

    payload = session.act([{"op": "keys", "keys": "a"}],
                          stop_on_error=False, observe_after=False)

    assert payload["ok"] is False
    assert payload["ops"][0]["error"] == "blocked_by_policy"
    assert driver.inserted() == []
