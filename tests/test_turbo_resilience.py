"""What turbo mode does when the provider — or the page — misbehaves.

Turbo mode spends money on every step, so its two failure modes are the ones
that cost the most:

  * the *provider* answers something other than the documented contract. The
    decision model is reached through several routes (TypeSafe direct,
    OpenRouter, a gateway), and each can answer with a proxy error page, a
    differently-shaped envelope, or nothing at all. Every one of those is a
    failed decision — no action was executed — and must be reported as such.
    A `KeyError` / `JSONDecodeError` escaping the tool does not say that: the
    host sees a protocol-level crash, cannot tell "the model refused" from
    "the server has a bug", and does not learn that the page is untouched.

  * the *page* keeps invalidating the refs. Re-observing is the right recovery,
    but "bounded" has to mean something specific: bounded per step (one busy
    page mid-goal must not use up the recovery budget of the steps after it)
    and bounded for the whole goal (a page that never settles must not turn
    `max_steps=20` into an unbounded number of billed requests).

No network, no browser, no key: the provider is faked at `policy._post` and the
session at `server._session`.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from jev_ultrafast_mcp import browser, policy, server
from jev_ultrafast_mcp.config import Config
from jev_ultrafast_mcp.observe import Element, Observation


def _observation() -> Observation:
    return Observation(
        url="https://example.test/",
        title="Example",
        text="Example page",
        elements=[Element(ref="e1", role="searchbox", name="Search", editable=True)],
        digest="d",
        text_digest="t",
        page_key="k",
        scroll={"y": 0},
        reachable=1,
        omitted=0,
        overlays=[],
        cross_frames=0,
        cross_frame_srcs=[],
    )


def _cfg() -> Config:
    return dataclasses.replace(Config.from_env(), typesafe_key="test-key")


# ------------------------------------------------------------------ the provider


@pytest.mark.parametrize(
    "payload",
    [
        {},                                              # no envelope at all
        {"answers": {}},                                 # the question we asked is absent
        {"answers": None},                               # answers is the wrong type
        {"answers": {"operation": None}},                # the answer is the wrong type
        {"error": {"message": "rate limited"}},          # an error envelope, HTTP 200
    ],
    ids=["empty", "missing-question", "answers-null", "answer-null", "error-envelope"],
)
def test_a_malformed_answer_is_a_typed_failure_not_a_traceback(monkeypatch, payload):
    """Whatever the route answers, `choose` raises TurboUnavailable — never KeyError/TypeError."""
    monkeypatch.setattr(policy, "_post", lambda url, key, body: payload)

    with pytest.raises(policy.TurboUnavailable):
        policy.choose(_cfg(), _observation(), "search for something", [])


def test_a_malformed_answer_says_which_question_went_unanswered(monkeypatch):
    """The error has to name the gap, or the fix is a guess."""
    monkeypatch.setattr(policy, "_post", lambda url, key, body: {"answers": {}})

    with pytest.raises(policy.TurboUnavailable) as caught:
        policy.choose(_cfg(), _observation(), "search for something", [])

    assert "operation" in str(caught.value)


class _Body:
    """A 200 response whose body is not JSON — a proxy or gateway error page."""

    status_code = 200
    is_error = False

    def json(self):
        raise json.JSONDecodeError("Expecting value", "<html>502 Bad Gateway</html>", 0)


class _Client:
    def post(self, *args, **kwargs):
        return _Body()


def test_a_non_json_body_is_a_typed_failure_not_a_traceback(monkeypatch):
    monkeypatch.setattr(policy, "CLIENT", _Client())

    with pytest.raises(policy.TurboUnavailable) as caught:
        policy._post("https://example.test/decisions", "test-key", {})

    assert "JSON" in str(caught.value)
    assert "no action executed" in str(caught.value)


def test_the_tool_reports_a_turbo_failure_under_one_prefix():
    """`turbo_unavailable:` is the vocabulary the tool documents; keep it for every path."""
    assert server._error(policy.TurboUnavailable("no key")).startswith("turbo_unavailable:")


# --------------------------------------------------------------------- the page


class _FakeSession:
    """The parts of `browser.Session` the goal loop touches, scripted."""

    def __init__(self, act_results: list[dict]):
        self._act = list(act_results)
        self.history: list = []
        self.last: Observation | None = None
        self.acts: list[list[dict]] = []
        self.resets = 0
        self._streak = 0
        # Order matters for the handoff: a goal that navigates has to do it
        # before it reads the page, or it plans against the page it just left.
        self.events: list[str] = []
        # Scripted answers to "has the page stopped fetching?". Empty means
        # "yes", which is what a session that did not navigate reports.
        self.idle: list[bool] = []
        self.idle_questions = 0

    def navigate(self, url: str) -> None:
        self.events.append(f"navigate {url}")

    def reset_progress(self) -> None:
        self.resets += 1
        self._streak = 0

    def page_is_idle(self) -> bool:
        self.idle_questions += 1
        return self.idle.pop(0) if self.idle else True

    def observe(self, **_kwargs) -> Observation:
        self.events.append("observe")
        self.last = _observation()
        return self.last

    def evaluate_js(self, _expression: str):
        # A real session evaluates JS against the page. Refusing loudly here
        # keeps a `js` check from passing against a fixture that never ran it.
        raise AssertionError("the scripted session does not evaluate JS")

    def act(self, ops, **_kwargs) -> dict:
        self.acts.append(ops)
        scripted = self._act.pop(0) if self._act else {"ok": True}
        step = {"op": ops[0]["op"], "ok": scripted.get("ok", True), "ms": 1}
        if not step["ok"]:
            step["error"] = scripted["error"]
        payload = {"ops": [step], "ok": step["ok"], "steps": len(self.acts)}
        if not step["ok"]:
            payload["view"] = "= no change"
        # Mirror the real session rather than letting a test assert `stuck` into
        # existence: the loop has to read the same contract the browser writes,
        # so the fake counts the streak the way `browser.Session.act` does and
        # derives `stuck` from the shared limit.
        changed = scripted.get("page_changed", True)
        self._streak = 0 if changed else self._streak + 1
        payload["page_changed"] = changed
        if self._streak >= browser.NO_CHANGE_LIMIT:
            payload["stuck"] = (
                f"{self._streak} consecutive actions changed nothing. "
                "Do not retry the same ref: re-read the observation, look for a "
                "covering dialog, or change strategy."
            )
        return payload


def _stale() -> dict:
    return {"ok": False, "error": "detached"}


def _ok() -> dict:
    return {"ok": True}


def _still() -> dict:
    """A step that succeeded and left the page exactly as it was."""
    return {"ok": True, "page_changed": False}


def _wait() -> dict:
    return {"operation": "WAIT", "ref": None, "confidence": 0.6}


def _checked_in() -> Observation:
    """The page after a check-in: the button is gone, the proof is in the text."""
    return dataclasses.replace(_observation(),
                               text="Daily Rewards  Streak 5  Points 130  Checked in today")


def _drive(monkeypatch, session: _FakeSession, decisions: list[dict], max_steps: int = 20,
           verify: list[dict] | None = None, url: str = ""):
    """Run `browser_goal` against a scripted session and decision sequence."""
    seen: list[dict] = []

    def fake_choose(_cfg, _observation, _goal, _history):
        seen.append(_observation)
        if not decisions:
            return {"operation": "DONE", "ref": None, "confidence": 1.0}
        nxt = decisions.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(server.policy, "available", lambda _cfg: True)
    monkeypatch.setattr(server.policy, "choose", fake_choose)
    monkeypatch.setattr(server, "_session", lambda _name: session)
    return server.browser_goal("do the thing", url=url, max_steps=max_steps, verify=verify,
                               verbose=True), seen


def _click() -> dict:
    return {"operation": "CLICK", "ref": "e1", "confidence": 0.9}


def test_the_handoff_opens_the_page_before_it_plans(monkeypatch):
    """`url` is what makes the handoff complete: one call, no pre-opened page.

    The order is the substance, not a detail. A goal that reads the page before
    navigating plans against whatever the session happened to be showing -- and
    on a session that has never opened anything that is `about:blank`, so the
    model is asked to work out a task from an empty page.
    """
    session = _FakeSession([_ok()])
    decisions = [_click(), {"operation": "DONE", "confidence": 0.9}]

    out, _seen = _drive(monkeypatch, session, decisions, url="https://example.test/checkin")

    assert session.events[0] == "navigate https://example.test/checkin", (
        f"the goal must open the page before it reads it, got {session.events[:3]}")
    assert session.events[1] == "observe", session.events[:3]
    assert "status: done" in out, out


def test_a_goal_without_a_url_runs_on_the_page_it_is_given(monkeypatch):
    """Continuing from an earlier page is why `url` is optional, not required."""
    session = _FakeSession([_ok()])

    _drive(monkeypatch, session, [{"operation": "DONE", "confidence": 0.9}])

    assert session.events[0] == "observe", session.events[:3]
    assert not [e for e in session.events if e.startswith("navigate")], session.events[:3]


def test_a_stale_ref_does_not_spend_the_next_step_s_budget(monkeypatch):
    """Four steps each hitting one stale ref all recover.

    The per-step budget has to reset, or a page that invalidates a ref once per
    step exhausts the goal's recovery on step four and fails a goal that was
    working — which is exactly how the fix for `detached` regressed in spirit
    while looking, from the source, like a bounded retry.
    """
    session = _FakeSession([_stale(), _ok()] * 4)
    # Recovering costs a decision: the step that went stale is attempted again,
    # so four successful steps need eight decisions.
    decisions = [_click() for _ in range(8)] + [{"operation": "DONE", "confidence": 0.9}]

    out, _seen = _drive(monkeypatch, session, decisions)

    assert "status: done" in out, out
    assert "steps: 4" in out, out
    assert out.count("re-observing") == 4, "each step got its own recovery"

    # Each stale ref was re-observed and then re-tried: 4 stale + 4 real steps.
    assert len(session.acts) == 8


def test_a_page_that_never_settles_ends_the_step(monkeypatch):
    """One step that stays stale is bounded by the per-step budget, not by max_steps."""
    session = _FakeSession([_stale()] * 50)

    out, seen = _drive(monkeypatch, session, [_click()] * 50)

    assert "failed:detached" in out, out
    assert len(seen) == server.STALE_REF_RETRIES + 1, "3 recoveries, then the step ends"


def test_recovery_cannot_multiply_the_request_count(monkeypatch):
    """A goal-wide ceiling keeps `max_steps` a bound on billed requests.

    Per-step recovery alone multiplies the worst case by `STALE_REF_RETRIES`, so
    a page that is stale on every step would turn `max_steps=3` into 12 requests.
    """
    session = _FakeSession(([_stale()] * 3 + [_ok()]) * 20)

    out, seen = _drive(monkeypatch, session, [_click()] * 50, max_steps=3)

    assert len(seen) <= 2 * 3, f"{len(seen)} requests for max_steps=3"
    assert "failed:" in out, out


PROOF = [{"type": "text_contains", "text": "Checked in today"}]


def test_the_page_beats_a_blocked_model_when_it_proves_the_goal(monkeypatch):
    """An assertion is a fact; `BLOCKED` is an opinion. The fact wins.

    This is not a rare disagreement, it is the ordinary shape of a goal whose
    last action removes what it acted on. Clicking a check-in button makes the
    button disappear, so the model -- correctly, given what it can see --
    reports that it has nothing to act on, on a goal that in fact succeeded.
    """
    session = _FakeSession([_ok()])
    monkeypatch.setattr(session, "observe", lambda **_kwargs: _checked_in())

    out, _seen = _drive(monkeypatch, session,
                        [_click(), {"operation": "BLOCKED", "ref": None, "confidence": 1.0}],
                        verify=PROOF)

    assert "status: done" in out, out
    assert "verified: PASS" in out, out
    assert "the assertion wins" in out, out


def test_a_blocked_model_stays_blocked_when_the_page_does_not_prove_it(monkeypatch):
    """The converse, so the rule above cannot be satisfied by optimism alone.

    The first BLOCKED is re-read (see the mounting tests below), so a genuinely
    blocked goal has to be blocked on the second answer too: the re-read buys a
    second look, not a different verdict.
    """
    session = _FakeSession([_ok()])
    blocked = {"operation": "BLOCKED", "ref": None, "confidence": 1.0}

    out, seen = _drive(monkeypatch, session, [dict(blocked), dict(blocked)], verify=PROOF)

    assert "status: blocked" in out, out
    assert "verified: FAIL" in out, out
    assert len(seen) == 2, "the first BLOCKED is re-read, the second is believed"


def test_a_model_that_says_done_over_an_unproven_page_is_not_reported_as_done(monkeypatch):
    """The same rule pointing the other way, and the direction that costs more.

    The rule above upgrades `BLOCKED` to `done` when the page proves the goal.
    This is the converse: a model that reports DONE over a page that does not
    prove it has not finished the goal, it has run out of ideas. Measured on a
    real daily check-in, twice -- `status: done` after four steps, and `status:
    done` at step 0 with the element it had been told to click not even on the
    page -- while the points balance had not moved either time. A caller that
    reads only `status` stops on an unfinished task, which is the one error
    `verify` exists to prevent.
    """
    session = _FakeSession([_ok()])

    out, _seen = _drive(monkeypatch, session,
                        [_click(), {"operation": "DONE", "confidence": 0.9}], verify=PROOF)

    assert "status: done" not in out, out
    assert "status: unconfirmed" in out, out
    assert "verified: FAIL" in out, out
    assert "the assertion wins" in out, out


def test_done_without_a_verify_is_still_taken_at_its_word(monkeypatch):
    """With no assertion there is no second opinion, so the summary stands.

    Pins the scope of the rule above: it fires on `verify` disagreeing with
    `done`, not on `done` being doubted in general.
    """
    session = _FakeSession([_ok()])

    out, _seen = _drive(monkeypatch, session,
                        [_click(), {"operation": "DONE", "confidence": 0.9}])

    assert "status: done" in out, out
    assert "verified:" not in out, out


# ------------------------------------------------------ the page has stopped moving


def test_a_page_that_stops_changing_ends_the_goal_instead_of_burning_max_steps(monkeypatch):
    """`act` counts how long the page has stood still; the loop has to read it.

    Measured on a real daily check-in: the submit succeeded, the page never
    changed again, and the model spent four WAITs discovering it. The run then
    reported "stopped: hit max_steps=6", which reads as "still working" when the
    truth is "there is nothing left to do". The count was already being kept --
    the loop simply dropped it, so the only bound on a stalled goal was the
    caller's step budget.
    """
    session = _FakeSession([_ok()] + [_still()] * 5)

    out, _seen = _drive(monkeypatch, session, [_click()] + [_wait()] * 5, max_steps=20)

    assert "status: stopped: no progress" in out, out
    assert "hit max_steps" not in out, out
    # Stopped once the page had been still for the shared limit -- not at the
    # end of the budget, and not one step earlier than the limit allows.
    assert len(session.acts) == browser.NO_CHANGE_LIMIT + 1, len(session.acts)


def test_a_goal_that_stalls_after_succeeding_is_confirmed_by_its_assertion(monkeypatch):
    """Stopping early must not turn a finished goal into a failed one.

    The commonest way to stall is to have finished: the last action removed the
    very thing it acted on. The observation is refreshed *before* the break for
    this reason -- `verify` judges the page as it stands now, so the assertion
    still gets to say the goal was met.
    """
    session = _FakeSession([_ok()] + [_still()] * 5)
    monkeypatch.setattr(session, "observe", lambda **_kwargs: _checked_in())

    out, _seen = _drive(monkeypatch, session, [_click()] + [_wait()] * 5,
                        max_steps=20, verify=PROOF)

    assert "status: done" in out, out
    assert "verified: PASS" in out, out
    assert "the assertion wins" in out, out


def test_the_stall_count_does_not_carry_from_one_goal_into_the_next(monkeypatch):
    """The streak belongs to a run, not to the session that serves it.

    The count is only ever incremented, so a goal handed a session a previous
    goal left near the limit would call itself stuck before it had acted once.
    """
    session = _FakeSession([_still(), _ok()])
    session._streak = browser.NO_CHANGE_LIMIT - 1  # what the previous goal left behind

    out, _seen = _drive(monkeypatch, session, [_click(), _click()], max_steps=2)

    assert "no progress" not in out, out
    assert session.resets == 1, "a goal starts its stall count from zero"


# ------------------------------------------------------ the page is not ready yet


def _thin() -> Observation:
    """The page as it reads before its JavaScript has mounted the header."""
    return dataclasses.replace(_observation(), elements=[])


def _mounted() -> Observation:
    """The same page once the header — and the menu the goal is after — is there."""
    return dataclasses.replace(
        _observation(),
        elements=[Element(ref="e9", role="button", name="Today's tasks")])


def test_the_first_read_waits_for_the_page_to_stop_growing(monkeypatch):
    """`load` is not "rendered": the header arrives afterwards.

    The observer's own settle pass only waits while there is *nothing*
    actionable at all, so on a content-rich page it answers immediately and
    misses exactly the late half — on 1point3acres the signed-in state and the
    daily-task menu. A goal handed that table can only answer BLOCKED.
    """
    session = _FakeSession([])
    reads = iter([_thin(), _mounted(), _mounted()])
    monkeypatch.setattr(session, "observe", lambda **_kwargs: next(reads, _mounted()))

    settled = server._first_read(session)

    assert [element.ref for element in settled.elements] == ["e9"], (
        "the first read must be the settled page, not the one that answered first")


def test_a_shell_that_is_still_fetching_is_not_settled_by_agreement(monkeypatch):
    """Still is not the same as finished.

    A client-rendered app's shell holds the same few elements for as long as its
    bundle takes to arrive, so "two reads agreed" settles on a page with no
    controls on it. Measured on cloudstudio.net's user centre: three elements
    across every poll, `BLOCKED` twice on the same page, and the rendered
    version one second away. Agreement is evidence only once the page has also
    stopped fetching.
    """
    session = _FakeSession([])
    reads = iter([_thin(), _thin(), _thin(), _thin(), _mounted(), _mounted()])
    monkeypatch.setattr(session, "observe", lambda **_kwargs: next(reads, _mounted()))
    session.idle = [False, False, False, True]

    settled = server._first_read(session)

    assert [element.ref for element in settled.elements] == ["e9"], (
        "the shell was accepted because it was still, not because it was finished")
    assert session.idle_questions == 4, (
        f"the page was asked {session.idle_questions} times whether it had finished")


def test_a_page_that_never_stops_fetching_costs_one_budget_and_no_more(monkeypatch):
    """A page that never drains must not hold the goal open.

    `page_is_idle` answers "may I stop?", so a site whose requests never finish
    has to end in a read of whatever is on screen rather than in a wait that
    outlives the budget.
    """
    monkeypatch.setattr(server.CONFIG, "settle_timeout", 0.3)
    monkeypatch.setattr(server.CONFIG, "settle_poll_ms", 20)
    session = _FakeSession([])
    reads = 0

    def counting(**_kwargs):
        nonlocal reads
        reads += 1
        return _thin()

    monkeypatch.setattr(session, "observe", counting)
    session.idle = [False] * 500

    settled = server._first_read(session)

    assert settled.elements == []
    assert reads <= 20, f"{reads} reads for one page that never went quiet"


def test_a_blocked_first_answer_is_re_read_before_it_is_believed(monkeypatch):
    """BLOCKED on the first read is usually the page, not the goal.

    The menu is not missing, it is late. Believing the first answer ends a goal
    that would have worked one second later, so the loop spends one re-read
    before it accepts "nothing to act on".
    """
    session = _FakeSession([_ok()])
    reads = iter([_thin(), _thin(), _mounted(), _mounted(), _mounted()])
    monkeypatch.setattr(session, "observe", lambda **_kwargs: next(reads, _mounted()))

    out, seen = _drive(monkeypatch, session,
                       [{"operation": "BLOCKED", "ref": None, "confidence": 0.6},
                        {"operation": "CLICK", "ref": "e9", "confidence": 0.9},
                        {"operation": "DONE", "confidence": 0.9}])

    assert "re-reading" in out, out
    assert "status: done" in out, out
    assert [element.ref for element in seen[0].elements] == [], (
        "the first look was the page that had not mounted yet")
    assert [element.ref for element in seen[1].elements] == ["e9"], (
        "the re-read found the menu, which is what unblocked the goal")


def test_the_re_read_is_bounded_so_max_steps_still_bounds_the_bill(monkeypatch):
    """A page that never mounts must not turn BLOCKED into a retry loop."""
    session = _FakeSession([])
    monkeypatch.setattr(session, "observe", lambda **_kwargs: _thin())
    blocked = {"operation": "BLOCKED", "ref": None, "confidence": 1.0}

    out, seen = _drive(monkeypatch, session, [dict(blocked)] * 10)

    assert "status: blocked" in out, out
    assert len(seen) == 2, f"{len(seen)} requests for one BLOCKED answer"


def test_a_provider_failure_mid_goal_keeps_what_the_goal_already_did(monkeypatch):
    """The trace must survive a provider that dies on step three.

    Losing the steps already taken is the failure the host cannot debug: it
    knows the goal failed, not that it had already clicked twice and was one
    step from done.
    """
    session = _FakeSession([_ok(), _ok()])
    decisions = [_click(), _click(), policy.TurboUnavailable("Decision model unreachable")]

    out, _seen = _drive(monkeypatch, session, decisions)

    assert "turbo_unavailable" in out, out
    assert "1. CLICK e1" in out and "2. CLICK e1" in out, out


# --------------------------------------------------------------- what it cost

@pytest.mark.parametrize(
    "usage,expected",
    [
        ({"total_tokens": 500, "prompt_tokens": 400, "completion_tokens": 100}, 500),
        ({"prompt_tokens": 400, "completion_tokens": 100}, 500),
        ({"input_tokens": 400, "output_tokens": 100}, 500),
        ({"totalTokens": 500}, 500),
        ({}, 0),
        (None, 0),
        ("500", 0),
        ({"total_tokens": True}, 0),
        ({"cached_tokens": 12}, 0),
    ],
    ids=["total-wins", "prompt-completion", "input-output", "camel", "empty", "none", "not-a-dict",
         "bool-is-not-a-count", "unknown-key"],
)
def test_the_token_count_does_not_double_count_a_total(usage, expected):
    """Routes name the same two numbers differently, and some add a total.

    Summing every key that contains "token" would add a total to its own parts
    and report twice what the goal spent — an inflated number in the one line a
    reader uses to judge whether delegating is worth it.
    """
    assert server._tokens(usage) == expected


def test_a_goal_reports_what_the_handoff_cost(monkeypatch):
    """The claim is that delegating saves the caller turns; this is the bill.

    Both halves matter: how many decisions the server made on the caller's
    behalf, and how much of the wall time was the model versus the page. A
    server that spends the caller's money owes them the count.
    """
    session = _FakeSession([_ok(), _ok()])
    decisions = [
        {"operation": "CLICK", "ref": "e1", "confidence": 0.9, "latency_ms": 300,
         "usage": {"prompt_tokens": 400, "completion_tokens": 20}},
        {"operation": "CLICK", "ref": "e1", "confidence": 0.9, "latency_ms": 300,
         "usage": {"total_tokens": 500, "prompt_tokens": 400, "completion_tokens": 100}},
        {"operation": "DONE", "ref": None, "confidence": 0.9, "latency_ms": 250,
         "usage": {"prompt_tokens": 300, "completion_tokens": 10}},
    ]

    out, _seen = _drive(monkeypatch, session, decisions)

    assert "turbo: 3 decisions" in out, out
    assert "1,230 tokens" in out, out
    assert "0.8s model" in out, out


def test_a_goal_that_never_reached_the_model_reports_no_cost(monkeypatch):
    """A refused plan costs nothing, and must not print a budget that implies otherwise."""
    session = _FakeSession([])
    monkeypatch.setattr(server.policy, "available", lambda _cfg: False)
    monkeypatch.setattr(server, "_session", lambda _name: session)

    out = server.browser_goal("do the thing", verbose=True)

    assert "turbo_unavailable" in out, out
    assert "turbo: " not in out, out
