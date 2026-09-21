"""An empty string is not a path, and `exists()` cannot tell the difference.

`Path("")` resolves to the **current directory**, which exists on every machine.
So a check of the form `not Path(p).exists()` -- the natural way to write "is this
file there?" -- accepts an empty string and then resolves it to the working
directory. The caller gets no complaint about the value they actually sent, and
whatever consumes the path is handed a directory.

This is the same trap that let a screenshot check pass for a file that was never
written (`scripts/smoke.py`, and the `Path("")` section of the changelog). It is
worth its own file because the two instances have *different* correct fixes, and
the difference is the point:

- for a screenshot the answer is a file, so `is_file()` is right;
- for an upload a directory is a legitimate argument -- `DOM.setFileInputFiles`
  takes one for a `webkitdirectory` input -- so the fix has to reject the empty
  string specifically, and `is_file()` would be a regression.

The last test here is the one that pins that second half, so that a later reader
who spots the same shape and reaches for `is_file()` finds out why not.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from jev_ultrafast_mcp.browser import Session
from jev_ultrafast_mcp.config import Config


class UploadCdp:
    """Answers the two questions an upload op asks: is the ref live, and where is the input."""

    def __init__(self) -> None:
        self.files: list[str] | None = None
        # `_after_input` settles the page and watches this for a navigation that
        # an upload can trigger. Empty is the quiet case, which is the one here.
        self.events: deque[dict] = deque(maxlen=16)

    def evaluate(self, expression: str, session_id: str, **kwargs: object):
        # `_after_input` asks this to decide whether to wait for a navigation, and
        # waits out `nav_timeout` when the answer is not "complete" -- 20s per
        # upload, which is the whole reason this branch is answered at all.
        if "readyState" in expression:
            return "complete"
        # `_guard`, asking whether the ref still means what the caller chose.
        return {"ok": True}

    def call(self, method: str, session_id: str | None = None,
             timeout: float | None = None, **params: object) -> dict:
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "node-1"}}
        if method == "DOM.setFileInputFiles":
            self.files = list(params.get("files") or [])
            return {}
        return {}


def _session(allow_uploads: bool = True) -> tuple[Session, UploadCdp]:
    driver = UploadCdp()
    cfg = Config(allow_uploads=allow_uploads)
    return Session("test", cfg, driver), driver


def _upload(session: Session, paths: list[str]):
    return session._run_op({"op": "upload", "ref": "e5", "paths": paths}, dry_run=False)


def test_an_empty_upload_path_is_refused():
    """The trap: this used to be accepted, and the working directory sent to CDP."""
    session, driver = _session()

    step = _upload(session, [""])

    assert not step.ok, step.to_dict()
    assert step.error == "invalid_request", step.to_dict()
    assert "empty" in (step.detail or ""), step.to_dict()
    assert driver.files is None, f"an empty path reached CDP as {driver.files}"


def test_a_whitespace_only_path_is_refused():
    """`" "` is not a filename either, and `Path(" ")` is a real-looking path.

    The message is asserted, not just the refusal: `Path("   ")` does not exist,
    so this was already refused before the blank check -- as `file not found:    `,
    which sends the caller looking for a file whose name is three spaces. The
    point of catching it as blank is the sentence they get instead.
    """
    session, driver = _session()

    step = _upload(session, ["   "])

    assert not step.ok and step.error == "invalid_request", step.to_dict()
    assert "empty" in (step.detail or ""), step.to_dict()
    assert driver.files is None


def test_a_missing_path_is_still_reported_as_missing():
    """The check that was already right, kept right."""
    session, driver = _session()

    step = _upload(session, ["/tmp/jev-definitely-not-here.pdf"])

    assert not step.ok and step.error == "invalid_request", step.to_dict()
    assert "file not found" in (step.detail or ""), step.to_dict()
    assert driver.files is None


def test_a_real_file_is_still_accepted_and_resolved():
    session, driver = _session()
    real = str(Path(__file__).resolve())

    step = _upload(session, [real])

    assert step.ok, step.to_dict()
    assert driver.files == [real], driver.files


def test_a_directory_is_still_accepted(tmp_path):
    """Why the fix is not `is_file()`.

    A directory is a legitimate upload target for a `webkitdirectory` input, so
    demanding a file would refuse something the caller is entitled to ask for.
    The empty string is rejected because it names nothing, not because it is not
    a file -- and this is the test that fails if somebody "tightens" the check
    into `is_file()` on the strength of the screenshot instance of the same trap.
    """
    session, driver = _session()

    step = _upload(session, [str(tmp_path)])

    assert step.ok, step.to_dict()
    assert driver.files == [str(tmp_path)], driver.files
