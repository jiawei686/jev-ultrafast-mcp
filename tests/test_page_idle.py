"""A navigation ends when the page stops fetching, not when the DOM stops moving.

`load` covers the HTML and its synchronous subresources. A client-rendered app
then fetches its bundle and its data, and until that lands the page is a shell
that holds the same handful of elements indefinitely. "The element table stopped
changing" is therefore not evidence that the page has arrived, and a first read
that trusts it hands the decision model a page with no controls on it -- which
reads downstream as `BLOCKED`, i.e. as a goal that cannot be done, when the
loaded version was a second away.

`Session.page_is_idle` answers the missing half from the `Network` domain's own
events. These tests drive it with synthetic events: no browser, no socket, so
the property under test is the counting rule and not whether Chrome happens to
be installed.

The counts are recomputed from the event log on every call rather than
accumulated, which is why `test_a_finished_request_does_not_keep_the_page_busy`
matters: an implementation that kept a running tally would pass the first
assertion and then never let go of a request it had already seen finish.
"""

from __future__ import annotations

import time

from jev_ultrafast_mcp.browser import Session
from jev_ultrafast_mcp.config import Config

PAGE_SESSION = "session-1"


class FakeCdp:
    """Answers the calls a session makes, and records the expressions it ran.

    `document.readyState` answers "complete" so that `_wait_loaded` returns at
    once; every other expression answers nothing, which is what a page with no
    helper installed looks like.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.evaluated: list[str] = []

    def call(self, method: str, timeout: float | None = None, **params: object) -> dict:
        if method == "Target.createTarget":
            return {"targetId": "tab-a"}
        if method == "Target.attachToTarget":
            return {"sessionId": PAGE_SESSION}
        if method == "Target.getTargets":
            return {"targetInfos": []}
        return {}

    def evaluate(self, expression: str, session_id: str, **kwargs: object):
        self.evaluated.append(expression)
        return "complete" if expression == "document.readyState" else None

    def close(self) -> None:
        pass


def _session(**cfg: object) -> Session:
    return Session("test", Config(**cfg), FakeCdp())


def _sent(cdp: FakeCdp, request_id: str, session_id: str = PAGE_SESSION) -> None:
    cdp.events.append({"method": "Network.requestWillBeSent", "sessionId": session_id,
                       "params": {"requestId": request_id}})


def _done(cdp: FakeCdp, request_id: str, method: str = "Network.loadingFinished",
          session_id: str = PAGE_SESSION) -> None:
    cdp.events.append({"method": method, "sessionId": session_id,
                       "params": {"requestId": request_id}})


def test_a_request_in_flight_keeps_the_page_busy():
    """The bundle is the difference between a shell and a page."""
    session = _session()
    session.start()
    session.navigate("https://example.test/user")

    _sent(session.cdp, "bundle.js")
    assert session.page_is_idle() is False, "a request in flight is not a finished page"

    _done(session.cdp, "bundle.js")
    assert session.page_is_idle() is True, "the request finished; nothing is left to wait for"


def test_a_finished_request_does_not_keep_the_page_busy():
    """Counts are recomputed from the log, so a completed request is forgotten.

    A running tally passes the first assertion above and then keeps the page
    busy forever, because nothing ever subtracts.
    """
    session = _session()
    session.start()
    session.navigate("https://example.test/user")

    for request_id in ("a", "b", "c"):
        _sent(session.cdp, request_id)
        _done(session.cdp, request_id)

    assert session.page_is_idle() is True


def test_a_failed_request_counts_as_finished():
    """`loadingFailed` is how an aborted or refused request ends.

    Counting only `loadingFinished` would make one blocked beacon -- an ad
    frame, a tracker the network refuses -- look like a page that never
    finished loading.
    """
    session = _session()
    session.start()
    session.navigate("https://example.test/user")

    _sent(session.cdp, "tracker.js")
    _done(session.cdp, "tracker.js", method="Network.loadingFailed")

    assert session.page_is_idle() is True


def test_another_tabs_traffic_does_not_hold_this_page_open():
    """One socket carries every tab, so the events have to be filtered.

    A background tab that streams would otherwise make every foreground page
    look permanently busy.
    """
    session = _session()
    session.start()
    session.navigate("https://example.test/user")

    _sent(session.cdp, "elsewhere", session_id="some-other-session")

    assert session.page_is_idle() is True


def test_a_session_that_did_not_navigate_has_nothing_to_wait_for():
    """Only a navigation we issued has a load for us to wait on.

    A goal that continues on a page an earlier step left behind must not pay a
    settle budget for a load nobody watched -- and must not spend a round trip
    asking about it either.

    The deadline is pushed into the future on purpose. Left at its default this
    test passes because the budget has run out, which is a different reason for
    the same answer: the state worth pinning is "not watching a navigation, and
    time still on the clock".
    """
    session = _session(settle_timeout=60.0)
    session.start()
    session._idle_deadline = time.monotonic() + 60.0
    _sent(session.cdp, "left-over")
    before = len(session.cdp.evaluated)

    assert session.page_is_idle() is True
    assert len(session.cdp.evaluated) == before, "a page we did not open was still polled"


def test_a_settled_navigation_is_not_re_examined():
    """A navigation is watched once, and the answer is remembered.

    Requests keep arriving after a page has loaded -- beacons, polls, a lazy
    image -- and none of them are the load we were waiting for. Re-arming on
    every one would put the settle budget back on every later read of the page,
    which is the cost this whole mechanism was supposed to stop paying.
    """
    session = _session(settle_timeout=60.0)
    session.start()
    session.navigate("https://example.test/user")
    _sent(session.cdp, "bundle.js")
    _done(session.cdp, "bundle.js")
    assert session.page_is_idle() is True, "the fixture never settled to begin with"

    _sent(session.cdp, "beacon.js")
    before = len(session.cdp.evaluated)

    assert session.page_is_idle() is True, "a settled navigation was re-opened"
    assert len(session.cdp.evaluated) == before, "a settled page was polled again"


def test_a_page_that_never_goes_quiet_gives_up_when_the_budget_expires():
    """The wait is bounded, and the bound is spent once rather than per read.

    A page with a long poll or a streaming connection never drains. It has to
    end in a read of whatever is on screen, and the next question has to be
    free -- otherwise every later read of that page pays the budget again.
    """
    session = _session(settle_timeout=0.05)
    session.start()
    session.navigate("https://example.test/user")
    _sent(session.cdp, "stream")

    deadline = time.monotonic() + 2.0
    while not session.page_is_idle() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert session.page_is_idle() is True, "the budget never expired"
    assert session._awaiting_idle is False, "the wait was left armed for the next read"
