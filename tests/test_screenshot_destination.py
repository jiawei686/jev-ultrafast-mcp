"""A screenshot goes to the shots directory, whatever the caller names.

`docs/DESIGN.md` lists screenshots in the table of what the policy envelope
bounds -- "Written to `~/.jev-ultrafast-mcp/shots/`" -- and that was true of no
absolute path. `path` was honoured as a *destination* when it was absolute, so a
screenshot op could overwrite any file the process can write. That is a larger
capability than any rail beside it in that table: navigation, secrets, JS
evaluation and destructive clicks are all bounded, and this was not. Nothing in
this repo passes an absolute path, so the branch served no flow -- and the step
reports the path it used, which is all a caller needs in order to read the file
or hand it to `upload`.

Two further shapes reached `write_bytes` as the shots directory itself -- `"."`,
and `".."` once flattened -- raising `IsADirectoryError` *through* `_run_op`'s
except clause. That is the third instance of the same defect in this codebase,
after `policy._validate`'s `AttributeError` and `browser_tabs`' uncaught
`SafetyError`: a refusal that escapes as a protocol-level crash, which a host
cannot tell apart from a bug in this server. It is why these tests drive
`_run_op` and not `_do_screenshot` -- the property is what a tool call returns,
not what the helper raises.

`path` names a file inside the directory now. The refusal is a `SafetyError`,
which `_run_op` maps to `blocked_by_policy`: the same code the domain envelope
uses, and the split the extension's own comment states -- `blocked_by_policy` is
about what the caller asked for, `invalid_request` is about a switch being off.
"""

from __future__ import annotations

import base64

import pytest

from jev_ultrafast_mcp.browser import Session
from jev_ultrafast_mcp.config import Config
from jev_ultrafast_mcp.observe import Observation

JPEG = base64.b64encode(b"\xff\xd8" + b"pixels" * 400).decode()

# Names a caller might plausibly send meaning "put it over there". Every one of
# them is outside the shots directory, and every one of them used to be either
# honoured or crash.
ELSEWHERE = [
    "/tmp/jev-not-the-shots-dir.jpg",
    "/etc/passwd",
    "~/shot.jpg",
    "nested/shot.jpg",
    "../shot.jpg",
    "a/../b.jpg",
]


class QuietCdp:
    """Answers the capture; nothing else is reached."""

    def __init__(self) -> None:
        self.captures = 0

    def call(self, method: str, session_id: str | None = None,
             timeout: float | None = None, **params: object) -> dict:
        if method == "Page.captureScreenshot":
            self.captures += 1
            return {"data": JPEG}
        return {}

    def evaluate(self, expression: str, session_id: str, **kwargs: object):
        return None


def _session(tmp_path) -> tuple[Session, QuietCdp]:
    driver = QuietCdp()
    cfg = Config()
    cfg.state_dir = tmp_path
    return Session("test", cfg, driver), driver


def _shots(tmp_path):
    return sorted((tmp_path / "shots").glob("*"))


def test_a_named_screenshot_lands_in_the_shots_directory(tmp_path):
    session, _ = _session(tmp_path)

    step = session._run_op({"op": "screenshot", "path": "login.png"}, dry_run=False)

    assert step.ok, step.to_dict()
    assert _shots(tmp_path) == [tmp_path / "shots" / "login.png"]
    assert step.target == str(tmp_path / "shots" / "login.png"), (
        "the step has to report the file it used, since that is what the caller reads"
    )


def test_an_unnamed_screenshot_still_lands_in_the_shots_directory(tmp_path):
    session, _ = _session(tmp_path)

    step = session._run_op({"op": "screenshot"}, dry_run=False)

    assert step.ok, step.to_dict()
    written = _shots(tmp_path)
    assert len(written) == 1 and written[0].parent == tmp_path / "shots"
    assert written[0].suffix == ".jpg"


def test_a_blank_name_means_no_name_given(tmp_path):
    """`" "` is not a filename, and it is not a refusal either.

    The op already reads an absent `path` as "generate one", and a blank string
    carries no name to honour, so it takes the same branch. Refusing it would be
    the upload rule, where there is no default to fall back on.
    """
    session, _ = _session(tmp_path)

    step = session._run_op({"op": "screenshot", "path": "   "}, dry_run=False)

    assert step.ok, step.to_dict()
    written = _shots(tmp_path)
    assert len(written) == 1 and written[0].name.startswith("shot-")


@pytest.mark.parametrize("requested", ELSEWHERE)
def test_a_destination_is_refused_rather_than_honoured(tmp_path, requested):
    """The hole: an absolute path used to be written wherever it pointed."""
    session, driver = _session(tmp_path)

    step = session._run_op({"op": "screenshot", "path": requested}, dry_run=False)

    assert step.ok is False, f"{requested!r} was accepted"
    assert step.error == "blocked_by_policy", step.to_dict()
    assert _shots(tmp_path) == [], f"something was written for {requested!r}"
    assert driver.captures == 0, "the page was captured before the path was checked"


@pytest.mark.parametrize("requested", [".", ".."])
def test_the_names_that_flatten_to_nothing_are_refused_not_crashed(tmp_path, requested):
    """These reached `write_bytes` as the directory, and raised past `_run_op`."""
    session, _ = _session(tmp_path)

    step = session._run_op({"op": "screenshot", "path": requested}, dry_run=False)

    assert step.ok is False, f"{requested!r} was accepted"
    assert step.error == "blocked_by_policy", step.to_dict()
    assert _shots(tmp_path) == []


def test_the_refusal_says_where_screenshots_go(tmp_path):
    """A refusal that does not name the alternative is a dead end for a model."""
    session, _ = _session(tmp_path)

    step = session._run_op({"op": "screenshot", "path": "/tmp/x.jpg"}, dry_run=False)

    assert str(tmp_path / "shots") in (step.detail or ""), step.detail
    assert "filename" in (step.detail or ""), step.detail


def test_a_refused_path_is_still_refused_in_a_batch(tmp_path):
    """The op is reached through `act`, which is what a tool call actually uses.

    `last` is set so `act` does not try to read the page first: this test is
    about the refusal surviving the batch, and a driver that cannot answer
    `readState` would fail here for a reason that has nothing to do with paths.
    """
    session, _ = _session(tmp_path)
    session.last = Observation.from_raw({"url": "https://example.com/", "actions": []})

    payload = session.act([{"op": "screenshot", "path": "/tmp/x.jpg"}],
                          stop_on_error=False, observe_after=False)

    assert payload["ok"] is False
    assert payload["ops"][0]["error"] == "blocked_by_policy"
    assert _shots(tmp_path) == []
