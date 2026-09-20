"""What a screenshot op does when the capture stalls instead of failing.

CI reports `Page.captureScreenshot: timed out after 30.0s` on the first capture
after a tab is closed and another promoted, and the capture immediately after it
succeeds -- same run, same page, 0.04s. The command was accepted and no frame came
back inside the client's timeout, so the condition is a stall, and a stall is what
a retry is for.

The mechanism is not established, so these tests deliberately assert the *shape*
of the mitigation rather than a cause: exactly one retry, only for a timeout, and
reported in the step rather than hidden. The last of those is the one that keeps
this honest -- a retry nobody can see turns a real defect into a slow green build,
and the day it starts firing every run is the day it needs to be visible.
"""

from __future__ import annotations

import base64

import pytest

from jev_ultrafast_mcp.browser import Session
from jev_ultrafast_mcp.cdp import CdpError
from jev_ultrafast_mcp.config import Config

JPEG = base64.b64encode(b"\xff\xd8" + b"pixels" * 400).decode()
TIMEOUT = "Page.captureScreenshot: timed out after 30.0s"


class StallingCdp:
    """A driver that stalls the first `Page.captureScreenshot` and then answers."""

    def __init__(self, *, fail_first: bool = True, error: str = TIMEOUT) -> None:
        self.calls: list[str] = []
        self.fail_first = fail_first
        self.error = error

    def call(self, method: str, session_id: str | None = None,
             timeout: float | None = None, **params: object) -> dict:
        self.calls.append(method)
        if method != "Page.captureScreenshot":
            return {}
        if self.fail_first and self.calls.count("Page.captureScreenshot") == 1:
            raise CdpError(self.error)
        return {"data": JPEG}


def _session(tmp_path, **kwargs) -> tuple[Session, StallingCdp]:
    driver = StallingCdp(**kwargs)
    cfg = Config()
    cfg.state_dir = tmp_path
    return Session("test", cfg, driver), driver


def _captures(driver: StallingCdp) -> int:
    return driver.calls.count("Page.captureScreenshot")


def test_a_stalled_capture_is_tried_again(tmp_path):
    session, driver = _session(tmp_path)

    path, note = session._do_screenshot({})

    assert _captures(driver) == 2, "the stall was not retried"
    assert path.is_file() and path.stat().st_size > 1000
    assert note, "the retry happened and the caller was not told"


def test_a_capture_that_works_first_time_is_not_retried(tmp_path):
    session, driver = _session(tmp_path, fail_first=False)

    _, note = session._do_screenshot({})

    assert _captures(driver) == 1, "a successful capture was sent twice"
    assert not note, "a capture that worked was reported as a retry"


def test_a_capture_that_fails_for_another_reason_is_not_retried(tmp_path):
    """Only a stall is retried. A refusal is an answer, and repeating it is noise.

    `Page.captureScreenshot` failing any other way -- a closed target, a dead
    session -- will fail identically the second time, so retrying it doubles the
    cost of the error and hides which attempt produced it.
    """
    session, driver = _session(tmp_path, error="Page.captureScreenshot: Target closed")

    with pytest.raises(CdpError, match="Target closed"):
        session._do_screenshot({})

    assert _captures(driver) == 1


def test_a_second_stall_is_reported_rather_than_retried_forever(tmp_path):
    """One retry, not a loop: a capture that stalls twice is a real failure."""
    session, driver = _session(tmp_path)
    original = driver.call

    def always_stall(method, session_id=None, timeout=None, **params):
        if method == "Page.captureScreenshot":
            driver.calls.append(method)
            raise CdpError(TIMEOUT)
        return original(method, session_id=session_id, timeout=timeout, **params)

    driver.call = always_stall  # type: ignore[method-assign]

    with pytest.raises(CdpError, match="timed out"):
        session._do_screenshot({})

    assert _captures(driver) == 2, "it kept trying"


def test_the_step_carries_the_retry(tmp_path):
    """The retry reaches the report, so it cannot become silent background noise."""
    session, driver = _session(tmp_path)

    step = session._run_op({"op": "screenshot"}, dry_run=False)

    assert step.ok, step.to_dict()
    assert _captures(driver) == 2
    assert "second attempt" in (step.detail or ""), step.to_dict()
