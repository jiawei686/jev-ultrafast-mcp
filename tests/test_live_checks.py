"""Tests for scripts/live_check.py -- the decisions that must not need a network.

`live_check.py` deliberately stays out of CI: it needs the open web, and a red
build caused by somebody else's rate limiter is worse than no build at all.
That leaves its most important judgement -- "is this failure ours, or the
site's?" -- untested by construction. These cover the pure parts of that
judgement, so a regression is caught without a browser and without a network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _live_check():
    spec = importlib.util.spec_from_file_location(
        "jev_live_check", ROOT / "scripts" / "live_check.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


live = _live_check()


# Trimmed from what DuckDuckGo actually served a fresh headless profile, not
# invented: this is the text that used to be scored as a FAILED search.
DDG_CHALLENGE = """\
[obs#2] https://duckduckgo.com/?q=python+asyncio  "python asyncio at DuckDuckGo"
e1   btn    Submit
e57  btn    Share Feedback
text:
Unfortunately, bots use DuckDuckGo too. Please complete the following challenge
to confirm this search was made by a human. Select all squares containing a duck:
"""

NORMAL_RESULTS = """\
[obs#3] https://duckduckgo.com/?q=python+asyncio  "python asyncio at DuckDuckGo"
e1   lnk    Python Asyncio Tutorial
e2   lnk    asyncio - Asynchronous I/O
e3   cmb*   Search privately
"""


def test_the_duckduckgo_challenge_is_recognised():
    assert live.bot_challenge(DDG_CHALLENGE) == "complete the following challenge"


def test_a_normal_result_page_is_left_alone():
    assert live.bot_challenge(NORMAL_RESULTS) is None


def test_other_interstitials_are_recognised():
    for text in (
        "Just a moment...",                                       # Cloudflare
        "Our systems have detected unusual traffic from your computer network.",
        "Verify you are human",
        "Checking your browser before accessing",
    ):
        assert live.bot_challenge(text), text


def test_an_unreachable_site_is_told_apart_from_a_broken_page():
    """Only one of these means "check your network"."""
    offline = TimeoutError("Page.navigate timed out")
    assert live.unreachable(offline) is True
    assert live.unreachable(OSError("getaddrinfo failed")) is True

    our_bug = KeyError("no element with ref e9 on this page")
    assert live.unreachable(our_bug) is False
