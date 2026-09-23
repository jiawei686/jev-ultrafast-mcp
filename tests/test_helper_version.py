"""The helper's version is stated three times, and one of the three decides everything.

`browser.HELPER_VERSION` and `chrome-extension/lib/session.js`'s constant are held
to each other at runtime by `act-parity.mjs`. The third copy -- the `VERSION` inside
`js/observer.js`, which is the number a page actually reports -- was held to
nothing, and it had drifted: the server expected 7, the page announced 6.

The consequence is not a warning. `_ensure_helper` reads the page's number and
re-injects the source when it differs, so a mismatch means the helper is re-sent on
every observation and every input op -- 25,315 bytes of it -- *and* the early return
at the top of that source, `if (W.__jevMcp && W.__jevMcp.version === VERSION) return;`,
fires against the number the page already carries, so the page keeps the helper it
had. The release that bumped the server's constant to mark a behaviour change never
reached a tab that had already been injected; it only paid for the attempt.

`tests/test_session_prep.py` answers `HELPER_VERSION` from its fake page. That is the
right thing for a test about setup commands to do, and it is also why this went
unseen for a release: a fake that agrees with the server cannot disagree with the
source. These tests read the source.
"""

from __future__ import annotations

import re
from pathlib import Path

from jev_ultrafast_mcp.browser import HELPER_SRC, HELPER_VERSION, Session
from jev_ultrafast_mcp.config import Config

ROOT = Path(__file__).resolve().parents[1]
OBSERVER_JS = ROOT / "jev_ultrafast_mcp" / "js" / "observer.js"
EXTENSION_SESSION_JS = ROOT / "chrome-extension" / "lib" / "session.js"

# `const VERSION = 7;` -- the page's own declaration.
DECLARED = re.compile(r"const VERSION = (\d+);")
# The two places that number has to be used rather than repeated.
GUARD = re.compile(r"__jevMcp\.version === VERSION\) return")
EXPORTED = re.compile(r"version: VERSION,")
# The shapes a second copy would take, if one came back.
GUARD_LITERAL = re.compile(r"__jevMcp\.version === \d+")
EXPORT_LITERAL = re.compile(r"version: \d+,")
EXTENSION_CONSTANT = re.compile(r"export const HELPER_VERSION = (\d+);")


def declared_version() -> int:
    match = DECLARED.search(OBSERVER_JS.read_text(encoding="utf-8"))
    assert match, "js/observer.js no longer declares `const VERSION = <n>;`"
    return int(match.group(1))


class PageCdp:
    """A page that answers the version question and takes the script it is given.

    `reports` is what the page announces. Injecting the helper sets it to what the
    *injected source* would announce, which is the whole mechanism under test: a
    page that is handed a helper it then reports back is how the check settles.
    """

    def __init__(self, reports: int) -> None:
        self.reports = reports
        self.injected = 0
        self.events: list[dict] = []

    def call(self, method: str, session_id: str | None = None,
             timeout: float | None = None, **params: object) -> dict:
        return {}

    def evaluate(self, expression: str, session_id: str | None = None,
                 await_promise: bool = False, timeout: float | None = None):
        if "__jevMcp && window.__jevMcp.version" in expression:
            return self.reports
        self.injected += 1
        self.reports = declared_version()
        return None


def _session(reports: int) -> tuple[Session, PageCdp]:
    driver = PageCdp(reports)
    session = Session("test", Config(), driver)
    session.page_session = "session-1"
    return session, driver


def test_the_page_states_its_version_once_and_by_name():
    """Two literals in one file are two literals that can disagree."""
    source = OBSERVER_JS.read_text(encoding="utf-8")

    assert GUARD.search(source), (
        "the guard no longer compares against `VERSION` -- a literal there is a second copy")
    assert EXPORTED.search(source), (
        "the exposed `version` is no longer `VERSION` -- a literal there is a second copy")
    assert not GUARD_LITERAL.search(source), "the guard repeats the number instead of naming it"
    assert not EXPORT_LITERAL.search(source), "the export repeats the number instead of naming it"


def test_the_server_and_the_page_agree_on_the_helper_version():
    """The comparison `_ensure_helper` makes, checked against the source it ships."""
    assert HELPER_VERSION == declared_version(), (
        f"browser.HELPER_VERSION is {HELPER_VERSION} and js/observer.js declares "
        f"{declared_version()}. A server number above the source's makes the check "
        "permanently true: the helper is re-injected on every call, and a page holding "
        "the shipped one is never upgraded."
    )


def test_the_extension_states_the_same_version_as_the_source():
    """`act-parity.mjs` holds the extension to `browser.py`, not to the helper.

    Node is not always installed, so the runtime comparison can be skipped; this one
    reads the file. The extension is the third copy, and it is the one that would be
    edited by hand.
    """
    match = EXTENSION_CONSTANT.search(EXTENSION_SESSION_JS.read_text(encoding="utf-8"))
    assert match, "chrome-extension/lib/session.js no longer exports HELPER_VERSION"

    assert int(match.group(1)) == declared_version(), (
        "the extension expects a different helper than js/observer.js declares")


def test_the_page_already_carrying_this_helper_is_left_alone():
    """The shipped source's own number, which is what a page reports back."""
    session, driver = _session(declared_version())

    for _ in range(3):
        session._ensure_helper()

    assert driver.injected == 0, (
        "the page already holds the helper this package ships, and the source was sent anyway")


def test_a_page_carrying_an_older_helper_is_upgraded_once():
    """The case the version number exists for, and the one that was inert.

    The page is handed the source, which sets it to the declared version; the next
    check then finds them equal and stops. Against the drifted tree the page reports
    6, the server wants 7, and the source's early return fires on the 6 -- so it is
    re-sent forever and the page stays on the helper it had.
    """
    session, driver = _session(declared_version() - 1)

    session._ensure_helper()
    session._ensure_helper()

    assert driver.injected == 1, (
        "an older helper should be replaced exactly once, not re-sent on every check")
    assert driver.reports == declared_version(), (
        "the page is still announcing the version it had before it was handed the new source")


def test_the_source_that_is_shipped_is_the_one_that_is_measured():
    """`HELPER_SRC` is read from disk at import; a stale constant would be invisible."""
    assert HELPER_SRC == OBSERVER_JS.read_text(encoding="utf-8"), (
        "HELPER_SRC and js/observer.js differ -- the server would inject something "
        "other than the file the version tests read")
