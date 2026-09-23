"""What a recording is allowed to write to disk, and what it is not.

A macro is a file. Everything else in this project keeps its state in memory for
the length of a run, so a value that leaks is a value that leaked once; a macro
outlives the run, gets copied between machines, and is pasted into the
extension's replay panel. Two rules therefore have to hold at the moment a step
is described rather than at the moment it is executed, and neither had a test:

- **A secret field's text is never stored.** `_record` replaces it with
  `macros.SECRET_PLACEHOLDER` before the descriptor is built, and the caller
  supplies the value at replay time. Nothing else in the suite would notice its
  absence, because a recording that stores the password still replays correctly
  -- it would simply be a password sitting in a JSON file.
- **`confirm: true` is not stored.** The confirmation envelope holds back a
  click whose label matches a destructive rule; if the recorder persisted the
  caller's `confirm`, then recording such a click once would launder it past the
  envelope on every later replay, with no model and no human in the loop. That
  is the exact shape of failure the macro feature is most exposed to.

Both are asserted through the real `describe`, so a rule added to `describe`'s
whitelist fails here rather than passing silently.
"""

from __future__ import annotations

import json
from pathlib import Path

from jev_ultrafast_mcp import macros
from jev_ultrafast_mcp.browser import Session
from jev_ultrafast_mcp.config import Config
from jev_ultrafast_mcp.observe import Observation

PASSWORD = "correct-horse-battery-staple"
BROWSER_PY = Path(__file__).resolve().parents[1] / "jev_ultrafast_mcp" / "browser.py"

# A login form, as the observer would report it: one ordinary field and one the
# page marked `secret`. `from_raw` is the real path, so `by_ref` and the secret
# flag are built the way a live observation builds them.
RAW = {
    "url": "https://example.com/login",
    "title": "Sign in",
    "actions": [
        {"ref": "e1", "role": "textbox", "name": "Username", "editable": True},
        {"ref": "e2", "role": "textbox", "name": "Password", "editable": True,
         "secret": True},
    ],
}


class RecordingCdp:
    """Enough of a driver to construct a `Session`; no call is expected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, timeout: float | None = None, **params: object) -> dict:
        self.calls.append((method, params))
        return {}

    def evaluate(self, expression: str, session_id: str, **kwargs: object):
        return None


def _recording() -> Session:
    """A session mid-recording, with the login form as its last observation."""
    session = Session("default", Config(), RecordingCdp())
    session.last = Observation.from_raw(RAW)
    session._recording = True
    session._recorder = []
    return session


def _describe(session: Session, op: str, ref: str, **fields: object) -> dict | None:
    """Run one op through the recorder, the way `_run_op` does at the end."""
    raw_op = {"op": op, "ref": ref, **fields}
    session._record(raw_op, op, ref, "")
    return session._recorder[-1] if session._recorder else None


def test_the_recorder_uses_the_shared_name_and_not_a_copy_of_it() -> None:
    """The constant has to be *used*, or naming it bought nothing.

    Asserting `step["text"] == macros.SECRET_PLACEHOLDER` cannot tell the two
    apart: the literal and the constant have the same value, so the recorder can
    spell the placeholder out and every behavioural test still passes. That is
    how `observe.EDITABLE_ROLES` rotted -- a second copy of a value nothing
    consumed, so nothing could ever notice it drifting. This is the assertion
    that makes the name load-bearing.
    """
    source = BROWSER_PY.read_text(encoding="utf-8")
    assert "SECRET_PLACEHOLDER" in source
    assert f'"{macros.SECRET_PLACEHOLDER}"' not in source, (
        "browser.py spells the placeholder out instead of naming it"
    )


def test_a_secret_fields_text_is_not_written_into_the_step() -> None:
    session = _recording()
    step = _describe(session, "type", "e2", text=PASSWORD)
    assert step is not None
    assert step["text"] == macros.SECRET_PLACEHOLDER
    assert PASSWORD not in json.dumps(step)


def test_an_ordinary_fields_text_is_kept() -> None:
    """The masking has to be narrow: over-masking is the other half of the bug.

    A `type` whose text was replaced unconditionally would make every recorded
    form useless while looking safe, so the negative case is asserted rather
    than assumed.
    """
    session = _recording()
    step = _describe(session, "type", "e1", text="alice")
    assert step is not None
    assert step["text"] == "alice"


def test_the_saved_file_does_not_contain_the_secret(tmp_path) -> None:
    """The property, stated where it matters: the bytes on disk."""
    session = _recording()
    session.cfg.macros_dir = lambda: tmp_path  # type: ignore[method-assign]
    _describe(session, "type", "e2", text=PASSWORD)
    saved = session.stop_recording("login")
    written = (tmp_path / "login.json").read_text(encoding="utf-8")
    assert PASSWORD not in written
    assert macros.SECRET_PLACEHOLDER in written
    assert saved["steps"] == 1


def test_a_secret_left_unsupplied_replays_as_the_placeholder() -> None:
    """Not a special case: an unfilled placeholder stays as it was written.

    That is what makes it fail visibly rather than silently submit the literal
    string, and it is why the extension's panel can offer the field without
    knowing anything about secrets.
    """
    session = _recording()
    step = _describe(session, "type", "e2", text=PASSWORD)
    ops, _ = macros.resolve([step], session.last, {})
    assert ops[0]["text"] == macros.SECRET_PLACEHOLDER


def test_a_supplied_secret_replays_as_the_value() -> None:
    session = _recording()
    step = _describe(session, "type", "e2", text=PASSWORD)
    ops, _ = macros.resolve([step], session.last, {macros.SECRET_PARAM: PASSWORD})
    assert ops[0]["text"] == PASSWORD


def test_a_confirmed_click_does_not_replay_as_confirmed() -> None:
    """Recording once must not launder a destructive click past the envelope."""
    session = _recording()
    step = _describe(session, "click", "e1", confirm=True)
    assert step is not None
    assert "confirm" not in step
    ops, _ = macros.resolve([step], session.last, {})
    assert "confirm" not in ops[0], "a replayed click would skip needs_confirmation"
