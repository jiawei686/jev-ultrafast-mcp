"""MCP surface.

Ten tools. The important design choice is what is *not* here: there is no
`javascript`, no `click_at(x, y)`, and no `query_selector`. An agent can only
name a ref that the server observed, and the server only dispatches input after
re-checking that the ref still means what it meant. That property is what makes
it safe to hand a browser to an autonomous agent over MCP.
"""

from __future__ import annotations

import atexit
import json
import time

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import assertions as assertions_mod
from . import macros as macros_mod
from . import policy
from .browser import BrowserManager, PageStale
from .cdp import CdpError, ChromeLaunchError
from .config import Config
from .observe import Observation
from .safety import SafetyError

INSTRUCTIONS = """\
Fast browser control for agents.

Hand the task over. A browser task -- fill this form, check in here, find that
and click it -- goes to browser_goal(goal, url, verify=[...]) in one call: it
opens the page, drives the loop server-side with a decision model, and proves
the outcome with `verify`. Do not drive a task yourself, click by click; that is
what this server is for.

Reading a page is not a task. browser_open / browser_observe / browser_assert
are direct, free, and need no key -- use them to inspect, to read a value, or to
work out why a goal failed. The only other reason to fall back is browser_goal
answering `turbo_unavailable`, which means no model key is configured.

The manual loop, for those two cases only:
    browser_open -> read the element table -> browser_act -> browser_assert.

The element table lists one line per actionable control:
    e12 btn  "Sign in"
    e7  inp* "Where from?" ▸ "San Francisco"
    e8  sel* "Passengers" ▸ "1 adult" opts{1 | 2 | 3}
    e9  chk· "Nonstop"
    e30 btn⊘ "Submit"
Flags: `*` accepts TYPE, `▸` shows the current value, `✓`/`·` is the checked
state, `▾` is expanded, `⊘` means covered by another element right now.

Rules that keep it fast and correct:
  1. Refs are stable across observations. e37 keeps meaning the same control
     until that element is removed, so a plan written three steps ago still holds.
  2. Pass a list of ops to browser_act and they run in order in one round trip.
     Reach for several ops per call instead of one.
  3. Observations after an action are deltas. `= no change` means the last action
     did nothing — change strategy, do not repeat the ref.
  4. A step that fails says why (occluded, stale, out_of_viewport). The right
     response is usually to observe again, not to retry.
  5. Prove the outcome instead of trusting a summary: pass `verify` to
     browser_goal, or call browser_assert. Do not infer success from "no error".
  6. After discovering a path, record it as a macro: replay costs no tokens.
"""

SERVER = MCPServer(
    "jev-ultrafast-mcp",
    instructions=INSTRUCTIONS,
    version="0.1.5",
)

CONFIG = Config.from_env()
MANAGER = BrowserManager(CONFIG)
atexit.register(MANAGER.shutdown)


# ----------------------------------------------------------------- annotations
#
# How a host decides whether a tool call needs a human. Leaving these off is not
# neutral: an unannotated tool reads as "could do anything", so a careful client
# confirms *every* call — including the reads that make up most of an agent loop
# (observe, assert, sessions, doctor). Declaring reads as reads is what lets a
# host stop interrupting them, and the hint is cheap insurance even where the
# client ignores it.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=False,
)
WRITES = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, openWorldHint=True,
)
WRITES_LOCAL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, openWorldHint=False,
)


# ----------------------------------------------------------------- helpers


def _session(name: str):
    return MANAGER.session(name or "default")


def _view(observation: Observation, mode: str = "auto", include_text: bool = True,
          focus: list[str] | None = None) -> str:
    return observation.render(observation.previous, mode=mode, include_text=include_text,
                              focus=focus, max_text=CONFIG.max_text)


def _brief(observation: Observation) -> str:
    return (f"{observation.url} — {len(observation.elements)} elements, "
            f"{observation.reachable} reachable, obs#{observation.sequence}")


def _first_read(tab) -> Observation:
    """Read a freshly opened page until its element set stops changing.

    `load` fires long before a page's JavaScript has finished mounting. The
    observer's own settle pass only waits while there is *nothing* actionable at
    all, which a content-rich page never is, so it answers at once with a table
    that is missing exactly the late half. On 1point3acres that is the header —
    and with it the signed-in state and the "today's tasks" menu the goal is
    after: absent from the first read, present from the second. A goal handed
    that first table can only answer BLOCKED, or click at something that is not
    there yet, which is what makes a working goal look impossible.

    Stop as soon as two consecutive reads agree on the ref set, so a page that
    is already rendered costs one extra read and nothing more. Bounded by
    `JEVMCP_SETTLE_TIMEOUT`, which is the knob that already means "how long to
    wait for a client-rendered page".
    """
    observation = tab.observe()
    seen = {element.ref for element in observation.elements}
    deadline = time.monotonic() + CONFIG.settle_timeout
    while time.monotonic() < deadline:
        time.sleep(CONFIG.settle_poll_ms / 1000.0)
        observation = tab.observe()
        refs = {element.ref for element in observation.elements}
        if refs == seen:
            break
        seen = refs
    return observation


def _tabs_list(tab) -> str:
    tabs = tab._refresh_tabs()
    if not tabs:
        return "no tabs"
    lines = []
    for item in tabs:
        handle = "#" + item["target_id"][:8]
        lines.append(
            f"  [{item['index']}] {'*' if item['active'] else ' '} {handle}  "
            f"{item['url']}  \"{item['title']}\""
        )
    return "\n".join(lines)


def _error(exc: Exception) -> str:
    if isinstance(exc, ChromeLaunchError):
        return f"browser_unavailable: {exc}"
    if isinstance(exc, SafetyError):
        return f"blocked_by_policy: {exc}"
    if isinstance(exc, PageStale):
        return f"stale: {exc}"
    if isinstance(exc, CdpError):
        return f"browser_error: {exc}"
    if isinstance(exc, policy.TurboUnavailable):
        # One prefix for every way the decision model can fail, so a caller can
        # branch on it. `browser_goal` already answers `turbo_unavailable` when
        # there is no key; a key that stopped working is the same instruction.
        return f"turbo_unavailable: {exc}"
    return f"error({type(exc).__name__}): {exc}"


def _tokens(usage: object) -> int:
    """The token count a provider reported, under whichever names it chose.

    Routes spell the same two numbers differently and some add a total: summing
    every key that contains "token" would count a total alongside its own parts
    and report double what was spent. Prefer the total when it is there.
    """
    if not isinstance(usage, dict):
        return 0
    for key in ("total_tokens", "totalTokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    total = 0
    for key in ("prompt_tokens", "input_tokens", "completion_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def _render_act(payload: dict, verbose: bool = False) -> str:
    lines = []
    ops = payload.get("ops", [])
    ok_count = sum(1 for op in ops if op.get("ok"))
    lines.append(f"{ok_count}/{len(ops)} ops ok" + ("" if payload.get("ok") else "  (stopped early)"))
    for op in ops:
        ref = op.get("ref") or ""
        target = op.get("target") or ""
        label = f"{op.get('op')} {ref}".strip()
        if target:
            label += f" → {target}"
        if op.get("ok"):
            detail = f"  [{op['detail']}]" if op.get("detail") else ""
            lines.append(f"  + {label}  {op.get('ms', 0)}ms{detail}")
        else:
            lines.append(f"  x {label}  {op.get('error')}: {op.get('detail', '')}")
    if payload.get("stuck"):
        lines.append(f"! {payload['stuck']}")
    if payload.get("view"):
        lines.append("")
        lines.append(payload["view"])
    if verbose:
        lines.append("")
        lines.append(f"(steps this session: {payload.get('steps', 0)})")
    return "\n".join(lines)


# -------------------------------------------------------------------- tools


@SERVER.tool(annotations=WRITES)
def browser_open(url: str, session: str = "default", hint: str = "") -> str:
    """Open a URL in a new owned tab and return the element table.

    Use `hint` to restate the goal in one line; it is echoed back so the next
    step has the goal in context without re-reading this call.
    """
    try:
        tab = _session(session)
        tab.navigate(url)
        observation = _first_read(tab)
        head = f"opened {_brief(observation)}"
        if hint:
            head += f"\ngoal: {hint}"
        return head + "\n\n" + _view(observation, mode="full")
    except (ChromeLaunchError, SafetyError, CdpError, PageStale) as exc:
        return _error(exc)


@SERVER.tool(annotations=READ_ONLY)
def browser_observe(session: str = "default", mode: str = "auto",
                    include_text: bool = True, include_json: bool = False) -> str:
    """Re-read the page: new element table, or a delta if little changed.

    `mode`: "auto" (delta when possible), "full" (whole table, e.g. after a big
    change), "delta" (force). `include_json=True` appends a machine-readable
    copy of the element table when you want to plan over it programmatically.
    """
    try:
        tab = _session(session)
        observation = tab.observe(include_text=include_text, full=(mode == "full"))
        text = _view(observation, mode=mode, include_text=include_text)
        if include_json:
            text += "\n\njson: " + json.dumps(observation.to_dict()["json"], ensure_ascii=False)
        return text
    except (ChromeLaunchError, CdpError, PageStale) as exc:
        return _error(exc)


@SERVER.tool(annotations=WRITES)
def browser_act(ops: list[dict], session: str = "default", dry_run: bool = False,
                stop_on_error: bool = True, observe_after: bool = True) -> str:
    """Execute one or more ops in order, then return a delta observation.

    Batch ops into a single call — each call is a round trip.

    op              fields
    click           ref                     (ref may be "e12", or "e12" of a combobox to open it)
    type            ref, text, [clear=true], [submit=false]
    select          ref, value (option value or label)
    toggle          ref, [state]            (checkbox/radio/switch; no state = flip)
    hover           ref
    upload          ref, path | paths[]
    keys            key ("Enter", "Meta+A", "ArrowDown") | keys[]
    scroll          [dir=down|up|left|right], [amount=600], [ref]
    nav             url
    back | forward | reload
    wait            [ms=500]
    wait_for_ref    ref, [timeout_ms=8000]
    wait_for_text   text, [timeout_ms=8000]
    wait_for_load   [timeout_ms=20000]
    screenshot      [path], [full=false], [format=jpeg]
    tab             action=list|new|switch|close, [index], [url]
    eval            js                      (only when JEVMCP_ALLOW_JS=1)

    Actions matching the confirmation rules (pay, delete account, …) return
    needs_confirmation; re-send that op with "confirm": true to proceed.
    """
    try:
        tab = _session(session)
        payload = tab.act(ops, dry_run=dry_run, stop_on_error=stop_on_error,
                          observe_after=observe_after)
        return _render_act(payload)
    except (ChromeLaunchError, CdpError, PageStale, SafetyError, ValueError) as exc:
        return _error(exc)


@SERVER.tool(annotations=READ_ONLY)
def browser_assert(checks: list[dict], session: str = "default") -> str:
    """Verify the current page against deterministic checks. Returns pass/fail.

    checks
      {"type": "url_matches",     "pattern": "*/checkout*"}
      {"type": "url_contains",    "text": "/orders/"}
      {"type": "title_matches",   "pattern": "*Order*"}
      {"type": "text_contains",   "text": "Thanks", "regex": false}
      {"type": "text_absent",     "text": "Error"}
      {"type": "element_exists",  "role": "button", "name": "Continue"}
      {"type": "element_gone",    "ref": "e12"}
      {"type": "value_equals",    "ref": "e7", "value": "Zurich"}
      {"type": "checked",         "ref": "e9", "state": true}
      {"type": "count_at_least",  "role": "link", "min": 3}
      {"type": "js",              "expr": "document.title.length > 3"}
    """
    try:
        tab = _session(session)
        observation = tab.observe(include_text=True)
        result = assertions_mod.run(
            checks, observation, allow_js=CONFIG.allow_js,
            eval_js=tab.evaluate_js,
        )
        lines = [f"{'PASS' if result['pass'] else 'FAIL'}  ({result['url']})"]
        for check in result["checks"]:
            lines.append(f"  {'ok' if check['ok'] else 'X '} {check['type']}: {check['detail']}")
        return "\n".join(lines)
    except (ChromeLaunchError, CdpError, PageStale) as exc:
        return _error(exc)


@SERVER.tool(annotations=WRITES)
def browser_macro(action: str, session: str = "default", name: str = "",
                  params: dict | None = None, goal: str = "",
                  start_url: str = "", threshold: float = 0.7) -> str:
    """Record, replay, list, or delete a macro — a discovered path with no model calls.

    action="record_start"  begin capturing ops (needs the session to be driving the task)
    action="record_stop"   finish and save under `name`
    action="run"           replay `name`; `params` fills {{placeholders}} in text/url
    action="list" | "inspect" | "delete"

    Replay re-resolves each step by role + name against a fresh observation and
    refuses to act when the best match is weak or ambiguous.
    """
    try:
        tab = _session(session)
        if action == "record_start":
            tab.start_recording()
            return "recording started — drive the task, then call action=\"record_stop\" with a name"
        if action == "record_stop":
            if not name:
                return "record_stop needs a name"
            saved = tab.stop_recording(name, goal=goal)
            return (f"saved macro {saved['name']!r}: {saved['steps']} steps → {saved['path']}\n"
                    f"replay with action=\"run\", name={saved['name']!r}")
        if action == "list":
            items = macros_mod.listing(CONFIG)
            if not items:
                return "no macros saved yet"
            return "\n".join(
                f"  {item['name']}  {item['steps']} steps  {item['created']}"
                + (f"  goal={item['goal']!r}" if item["goal"] else "")
                for item in items
            )
        if action == "inspect":
            return json.dumps(macros_mod.load(CONFIG, name), indent=2, ensure_ascii=False)
        if action == "delete":
            return f"deleted {name!r}" if macros_mod.delete(CONFIG, name) else f"no macro {name!r}"
        if action == "run":
            data = macros_mod.load(CONFIG, name)
            start = start_url or data.get("start_url")
            if start:
                tab.navigate(start)
            observation = tab.observe()
            ops, report = macros_mod.resolve(data.get("steps", []), observation,
                                             params or {}, threshold=threshold)
            payload = tab.act(ops, stop_on_error=True, observe_after=True)
            header = f"replayed {name!r}: resolved {len(ops)} steps from {len(data.get('steps', []))}\n" + \
                     "\n".join(
                         f"  step {item['step']} {item['op']}"
                         + (f" → {item.get('ref')} {item.get('name')!r} ({item.get('score')})"
                            if item.get("ref") else "")
                         for item in report
                     )
            return header + "\n\n" + _render_act(payload)
        return f"unknown macro action {action!r}; use record_start|record_stop|run|list|inspect|delete"
    except (macros_mod.MacroError, ChromeLaunchError, CdpError, PageStale, SafetyError) as exc:
        return _error(exc)


# Reasons that mean "the refs you were given no longer describe the page" --
# as opposed to "this element cannot be acted on right now". The first kind is
# one observation away from working, because the element is usually still there
# under a new ref, so a goal recovers from it instead of ending. Live pages do
# this constantly: typing into a JS-rendered search box replaces the whole
# search area, taking the submit button's node with it.
STALE_REF_REASONS = {"detached", "page_changed", "target_changed", "unknown_ref", "stale"}

# How many times one goal step may re-observe after its refs went stale. It is
# per step, so a page that invalidates a ref once per step does not spend the
# recovery budget of the steps after it. The goal as a whole gets the same
# allowance again -- see `recovered` in the loop -- which keeps per-step
# recovery from multiplying the request count by STALE_REF_RETRIES.
STALE_REF_RETRIES = 3


@SERVER.tool(annotations=WRITES)
def browser_goal(goal: str, url: str = "", session: str = "default", max_steps: int = 20,
                 verify: list[dict] | None = None, verbose: bool = False) -> str:
    """Hand a whole browser task over. Needs a decision-model key.

    This is the entry point for browser work, not an optimisation on top of the
    manual loop. Pass `url` and the goal and the page is opened and driven to
    the end here: one call, one host turn, instead of a turn per click.

    Leave `url` out when the task continues from a page an earlier step left
    behind; the goal then runs against whatever the session is already showing.

    Each step costs one request (operation + every target head in a single
    speculative fan-out). `verify` runs browser_assert-style checks on the final
    page, so the result is a fact rather than a model's claim of success.

    The key is TYPESAFE_API_KEY, or OPENROUTER_API_KEY when TYPESAFE_BASE_URL
    points at https://openrouter.ai/api/alpha/decisions -- same model, same
    contract, no TypeSafe account needed. Reading a page needs no key at all, so
    when no key is set the handoff is unavailable while browser_open,
    browser_observe and browser_act keep working.
    """
    if not policy.available(CONFIG):
        return ("turbo_unavailable: no decision-model key is set, so the task cannot be "
                "handed over.\n"
                "Set TYPESAFE_API_KEY, or OPENROUTER_API_KEY with "
                "TYPESAFE_BASE_URL=https://openrouter.ai/api/alpha/decisions.\n"
                "Until then, drive the loop yourself: browser_observe → pick a ref → browser_act.")
    try:
        tab = _session(session)
        # The stall count belongs to a run, not to the session: a goal that
        # inherited the previous goal's count would call itself stuck on step 1.
        tab.reset_progress()
        started = time.perf_counter()
        if url:
            tab.navigate(url)
        observation = _first_read(tab)
        trace: list[str] = []
        status = "running"
        steps = 0
        stale = 0        # re-observations spent on the step currently in flight
        recovered = 0    # re-observations spent by the goal as a whole
        re_read = False  # the one re-read allowed when the first answer is BLOCKED
        calls = 0        # decision requests, including the one that said DONE
        model_ms = 0     # time spent waiting on the decision model
        browser_ms = 0   # time spent waiting on the page
        tokens = 0
        while steps < max_steps:
            history = [{"op": step.op, "ref": step.ref, "target": step.target, "ok": step.ok}
                       for step in tab.history[-10:]]
            try:
                decision = policy.choose(CONFIG, observation, goal, history)
            except policy.TurboUnavailable as exc:
                # A provider that fails mid-run must not erase the steps already
                # taken: those are the whole record of how far the goal got, and
                # without them the host cannot tell a goal that was one click
                # from done from one that never started.
                status = f"turbo_unavailable: {exc}"
                trace.append(f"  !   step {steps + 1}: {exc}")
                break
            calls += 1
            model_ms += decision.get("latency_ms") or 0
            tokens += _tokens(decision.get("usage"))
            operation = decision["operation"]
            if operation in {"DONE", "BLOCKED"}:
                # "Nothing to act on" about a page that has not finished
                # rendering is not an answer about the goal, it is an answer
                # about the clock. Re-read once, only while nothing has been
                # attempted yet, and only out of the goal-wide recovery budget
                # so `max_steps` stays a bound on billed requests.
                if (operation == "BLOCKED" and steps == 0 and not re_read
                        and recovered < max_steps):
                    re_read = True
                    recovered += 1
                    trace.append("  -   BLOCKED on a page with nothing to act on; "
                                 "re-reading in case it is still mounting")
                    observation = _first_read(tab)
                    continue
                status = operation.lower()
                trace.append(f"  {steps + 1}. {operation} (conf {decision.get('confidence', 0):.2f})")
                break
            op: dict = {"op": policy.OPERATION_TO_ACT[operation]}
            if decision.get("ref"):
                op["ref"] = decision["ref"]
            if operation == "TYPE_TEXT":
                element = observation.by_ref.get(decision["ref"])
                op["text"] = policy.text_for(CONFIG, goal, element, observation, history)
            if operation == "SELECT" and decision.get("value") is not None:
                op["value"] = decision["value"]
            if operation == "SCROLL":
                op["dir"] = "down"
            steps += 1
            payload = tab.act([op], stop_on_error=False, observe_after=True)
            step_result = payload["ops"][0]
            error = step_result.get("error") or ""
            # The guard refusing a stale ref is right; believing it was fatal is
            # what stopped the goal. Without this the same goal succeeds or fails
            # depending on whether the page happened to be still while it was read.
            if (error in STALE_REF_REASONS and stale < STALE_REF_RETRIES
                    and recovered < max_steps):
                stale += 1
                recovered += 1
                steps -= 1
                trace.append(f"  -   re-observing ({error}: refs went stale, "
                             f"{stale}/{STALE_REF_RETRIES})")
                observation = tab.observe()
                continue
            stale = 0
            browser_ms += step_result.get("ms") or 0
            trace.append(
                f"  {steps}. {operation} {decision.get('ref') or ''} "
                f"{decision.get('target') or ''} → {'ok' if step_result['ok'] else error} "
                f"({decision.get('latency_ms', 0)}ms model / {step_result.get('ms', 0)}ms browser)"
            )
            if not step_result["ok"]:
                status = f"failed:{error}"
                break
            observation = tab.last or tab.observe()
            # `act` already counts how long the page has stood still; the loop
            # used to drop that count on the floor and keep paying one decision
            # request per step until `max_steps`. Measured on a real daily
            # check-in: the submit succeeded, the page never changed again, and
            # the model spent four WAITs discovering it. The run then reported
            # "stopped: hit max_steps=6", which reads as "still working" when
            # the truth is "there is nothing left to do". The observation above
            # is refreshed before the break on purpose: `verify` below settles
            # the goal against it, and a goal whose last action removed the
            # thing it acted on must be judged on the page as it stands now.
            if payload.get("stuck"):
                trace.append(f"  !   {payload['stuck']}")
                status = "stopped: no progress"
                break
        else:
            status = f"stopped: hit max_steps={max_steps}"

        verified = None
        if verify:
            verified = assertions_mod.run(verify, observation, allow_js=CONFIG.allow_js,
                                          eval_js=tab.evaluate_js)
            # `DONE` is an opinion; an assertion is a fact. When the model's
            # summary disagrees with the page, the page wins. This is not a
            # rare disagreement -- it is the normal shape of a goal whose last
            # action removes the thing it acted on. A check-in button is gone
            # the moment it is clicked, so the model, finding nothing left to
            # do, reports BLOCKED on a goal that in fact succeeded. Passing
            # that straight through would tell the host a finished task failed
            # and invite it to redo work that is already done.
            if verified["pass"] and status != "done":
                trace.append(f"  =   the model reported {status!r}, but the page proves the "
                             f"goal was met; the assertion wins")
                status = "done"
            elif not verified["pass"] and status == "done":
                # The same rule pointing the other way, and the direction that
                # costs more when it is missing. A model that reports DONE over a
                # page that does not prove it has not finished the goal, it has
                # run out of ideas. Measured on a real daily check-in: `status:
                # done` twice while the points balance had not moved -- once at
                # step 4, once at step 0 with the target element not even on the
                # page. Leaving `done` above a `verified: FAIL` line invites the
                # caller to stop on an unfinished task, which is the one error an
                # assertion exists to prevent.
                trace.append("  =   the model reported 'done', but the page does not prove "
                             "it; the assertion wins")
                status = "unconfirmed: the model reported done, the page does not prove it"

        lines = [f"goal: {goal}", f"status: {status}", f"steps: {steps}"]
        if calls:
            # What the handoff cost, in units the caller can check for itself.
            # The whole argument for delegating a browser flow is that the agent
            # spends one turn instead of one per click, so the numbers behind
            # that claim belong in the answer rather than in a README.
            lines.append(
                f"turbo: {calls} decision{'s' if calls != 1 else ''} · {tokens:,} tokens · "
                f"{model_ms / 1000:.1f}s model + {browser_ms / 1000:.1f}s page · "
                f"{time.perf_counter() - started:.1f}s wall"
            )
        if verbose:
            lines.append("trace:")
            lines.extend(trace)
        if verified is not None:
            lines.append(f"verified: {'PASS' if verified['pass'] else 'FAIL'}")
            for check in verified["checks"]:
                lines.append(f"  {'ok' if check['ok'] else 'X '} {check['type']}: {check['detail']}")
        lines.append("")
        lines.append(_view(observation))
        return "\n".join(lines)
    except (policy.TurboUnavailable, ChromeLaunchError, CdpError, PageStale) as exc:
        return _error(exc)


@SERVER.tool(annotations=WRITES_LOCAL)
def browser_tabs(session: str = "default", action: str = "list", index: int = -1,
                 target_id: str = "", url: str = "about:blank") -> str:
    """List, open, switch to, or close tabs.

    Tabs opened by the page show up in observations on their own. To act on one,
    prefer `target_id` (the `#handle` printed by action="list"): indexes are
    positional and get renumbered whenever the tab list changes, so an index
    read a call ago can address a different tab. `index` is a convenience when
    listing and acting in the same breath; omit both to mean "the current tab".
    """
    try:
        tab = _session(session)
        if action == "list":
            return _tabs_list(tab)
        if action == "new":
            tab.cdp.call("Target.createTarget", url=url, background=not CONFIG.foreground)
            tab._refresh_tabs()
            return "opened new tab\n" + _tabs_list(tab)
        which = index if index >= 0 else None
        if action == "switch":
            tab.switch_tab(which, target_id=target_id or None)
            observation = tab.observe()
            return f"switched to tab {target_id or which}\n\n" + _view(observation, mode="full")
        if action == "close":
            tab.close_tab(which, target_id=target_id or None)
            return f"closed tab {target_id or which or 'current'}"
        return f"unknown tab action {action!r}"
    except (ChromeLaunchError, CdpError, PageStale) as exc:
        return _error(exc)


@SERVER.tool(annotations=READ_ONLY)
def browser_sessions() -> str:
    """List open sessions (independent owned tabs)."""
    return "\n".join(f"  {name}" for name in sorted(MANAGER._sessions)) or "no sessions"


@SERVER.tool(annotations=WRITES_LOCAL)
def browser_close(session: str = "default", shutdown_browser: bool = False) -> str:
    """Close a session's tab. Set shutdown_browser=True to stop the browser too.

    Only a browser this server launched is stopped. In attach mode the browser is
    yours: shutdown detaches and leaves it, and every other window, running.
    """
    closed = MANAGER.close(session)
    if shutdown_browser:
        MANAGER.shutdown()
        if not MANAGER.owns_browser:
            return f"closed {closed or 'nothing'}; detached (your browser is still running)"
        return f"closed {closed or 'nothing'}; browser stopped"
    return f"closed {closed or 'nothing'}"


@SERVER.tool(annotations=READ_ONLY)
def browser_doctor() -> str:
    """Report environment: browser binary, connection, keys, and policy envelope.

    Call this when anything behaves unexpectedly — it separates "no browser"
    from "blocked by policy" from "no key".
    """
    report = MANAGER.doctor()
    report["config"] = {
        "max_actions": CONFIG.max_actions,
        "window": list(CONFIG.window),
        "profile": str(CONFIG.resolved_profile()),
        "macros_dir": str(CONFIG.macros_dir()),
    }
    report["hints"] = []
    if not report.get("connected"):
        if report.get("connection") == "dropped":
            # The socket is gone but the setup is fine, so the "point me at a
            # browser" advice below would send the caller off to fix a config
            # that was never wrong. Say what actually happened instead.
            report["hints"].append(
                "The socket to the browser dropped — Chrome quit, the machine slept, or a "
                "keepalive went unanswered. Nothing needs setting up: the next call opens a "
                "fresh socket, adopting the browser already running rather than starting a "
                "second one."
            )
        elif CONFIG.mode == "attach":
            report["hints"].append(
                "Attach mode: nothing is started for you. Point JEVMCP_CDP_URL at a browser "
                f"already exposing CDP (now {CONFIG.cdp_url or 'unset'}) — e.g. the "
                "chrome://inspect/#remote-debugging toggle. Shutdown detaches, never quits it."
            )
        else:
            report["hints"].append("Browser is not started yet; it launches on the first browser_open.")
    if report.get("chrome_error"):
        report["hints"].append("Set JEVMCP_CHROME to your Chrome/Chromium executable.")
    # A guard that is on has to say so. The envelope is opt-in and off by
    # default, so anyone who meets it has almost always inherited it from an
    # earlier task rather than chosen it — and a site that will not open is
    # then indistinguishable from a broken browser, which is the one thing this
    # tool promises to tell apart.
    if CONFIG.allow_domains:
        report["hints"].append(
            "Domain envelope is on: navigation outside JEVMCP_ALLOW_DOMAINS is refused "
            f"({', '.join(CONFIG.allow_domains)}). That list is set in the client's config, "
            "not a default — so a site that will not open is this, not a broken browser."
        )
    if CONFIG.deny_domains:
        report["hints"].append(
            f"JEVMCP_DENY_DOMAINS is set ({', '.join(CONFIG.deny_domains)}); those hosts are "
            "refused whatever the allowlist says."
        )
    return json.dumps(report, indent=2, ensure_ascii=False)


def main() -> None:
    SERVER.run("stdio")


if __name__ == "__main__":
    main()
