"""Session management and guarded execution.

The safety property that matters: a ref is a code-owned handle on a live DOM
node. The agent never authors a selector, a coordinate, or a snippet of
JavaScript. Every input is re-validated inside the page immediately before it
is dispatched, and a mutation is never retried.

The speed property that matters: freshness and geometry checks are answered by
the page itself (`__jevMcp.verify` / `__jevMcp.resolve`), so a step costs a few
hundred bytes on the wire instead of a full guard table.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import macros as macros_mod
from .cdp import (
    Cdp,
    CdpError,
    ChromeLaunchError,
    attach_chrome,
    launch_chrome,
    reattach_chrome,
    stop_chrome,
)
from .config import Config
from .observe import Observation
from .safety import SafetyError, check_url, confirm_reason, is_secret

HELPER_SRC = (Path(__file__).with_name("js") / "observer.js").read_text(encoding="utf-8")
HELPER_VERSION = 6

MODIFIERS = {
    "alt": 1, "option": 1,
    "ctrl": 2, "control": 2,
    "meta": 4, "cmd": 4, "command": 4, "super": 4,
    "shift": 8,
}
KEY_SPECS = {
    "enter": ("Enter", "Enter", 13), "return": ("Enter", "Enter", 13),
    "tab": ("Tab", "Tab", 9), "escape": ("Escape", "Escape", 27), "esc": ("Escape", "Escape", 27),
    "backspace": ("Backspace", "Backspace", 8), "delete": ("Delete", "Delete", 46),
    "arrowup": ("ArrowUp", "ArrowUp", 38), "arrowdown": ("ArrowDown", "ArrowDown", 40),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37), "arrowright": ("ArrowRight", "ArrowRight", 39),
    "up": ("ArrowUp", "ArrowUp", 38), "down": ("ArrowDown", "ArrowDown", 40),
    "left": ("ArrowLeft", "ArrowLeft", 37), "right": ("ArrowRight", "ArrowRight", 39),
    "home": ("Home", "Home", 36), "end": ("End", "End", 35),
    "pageup": ("PageUp", "PageUp", 33), "pagedown": ("PageDown", "PageDown", 34),
    "space": (" ", "Space", 32),
}
CLICKABLE_KINDS = {"click", "type", "select", "toggle", "hover", "upload"}
NAV_KINDS = {"nav", "back", "forward", "reload", "new_tab", "close_tab", "switch_tab"}
REQUIRES_REF = CLICKABLE_KINDS | {"scroll_to", "wait_for_ref"}

# Geometry failures that a scroll can fix. Frame reasons are included because
# an element can be perfectly placed inside its own frame and still be below
# the top-level fold.
SCROLLABLE_REASONS = {
    "out_of_viewport", "occluded",
    "frame_out_of_viewport", "frame_occluded", "frame_hidden",
}


class PageStale(RuntimeError):
    """A ref no longer refers to the page state the agent observed."""


@dataclass
class Step:
    op: str
    ok: bool
    ref: str | None = None
    target: str | None = None
    error: str | None = None
    detail: str | None = None
    ms: int = 0
    page_changed: bool | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


# How many consecutive actions may change nothing before the page counts as
# stuck. Three is long enough for a slow render to finish and short enough that
# a model with nothing left to do stops paying for observations. Exposed as a
# name because the goal loop reads the same number: the signal is computed here
# and consumed there, and a caller that stopped one step earlier than this
# limit would be stopping on a page that had not had its chance.
NO_CHANGE_LIMIT = 3


@dataclass
class Session:
    name: str
    cfg: Config
    cdp: Cdp
    background: bool = True

    target_id: str = ""
    page_session: str = ""
    sequence: int = 0
    last: Observation | None = None
    history: list[Step] = field(default_factory=list)
    tabs: list[dict] = field(default_factory=list)
    _known_targets: set[str] = field(default_factory=set)
    _recorder: list[dict] = field(default_factory=list)
    _recording: bool = False
    _recording_start_url: str = ""
    _no_change_streak: int = 0
    # True while the agent's last observation still matches the live page. The
    # first op of a batch gets a strict freshness check; later ops get an
    # identity check, because the batch's own earlier ops legitimately moved the
    # page on and a full-state match would reject the batch's own work.
    _fresh: bool = False
    _strict: bool = True
    # True between issuing a navigation and the page it landed on going quiet.
    # Set by `navigate`, answered by `page_is_idle`, which explains why the
    # question cannot be asked of the DOM.
    _awaiting_idle: bool = False
    _idle_deadline: float = 0.0

    # ------------------------------------------------------------- life cycle

    def start(self, url: str = "about:blank") -> "Session":
        self._attach_page("about:blank")
        if url and url != "about:blank":
            self.navigate(url)
        return self

    def _prepare_page(self) -> None:
        """The setup every freshly attached session gets, in one place.

        Two routes reach a page: attaching a session to a target we just created
        (`_attach_page`) and attaching one to a tab that already exists
        (`switch_tab`). These calls are documented as session-scoped, so the
        second route looks like it must re-issue them.

        Measured on this build, it does not. With `switch_tab` skipping this
        block entirely, a switched-to tab still reported `window.innerWidth` as
        `cfg.window`, still answered `document.hasFocus()` true, still ran rAF,
        and still fired a document-start script after navigating itself. Chrome
        applies these to the *target*, so a re-attach inherits them and skipping
        the block costs nothing observable today.

        It is shared anyway, because that is not a property to rely on. The
        scoping is undocumented, it is the sort of thing a Chrome release
        changes, and the failure would be silent -- a tab read through the wrong
        viewport produces an ordinary-looking observation, not an error. One
        block means the next per-session setting added here cannot be forgotten
        there.

        `Network.enable` is here for the same reason rather than because it
        shares the scoping question: it is what makes `page_is_idle` able to
        see that a page is still fetching, and a session that skipped it would
        quietly go back to settling on a shell.

        `Target.activateTarget` is deliberately *not* part of it. Bringing a
        window to the front is a decision about the user's desktop, not about
        the session, so it stays in `_attach_page`, where `background` governs
        it.
        """
        self.cdp.call("Page.enable", session_id=self.page_session)
        self.cdp.call("Runtime.enable", session_id=self.page_session)
        self.cdp.call("Network.enable", session_id=self.page_session)
        width, height = self.cfg.window
        self.cdp.call("Emulation.setDeviceMetricsOverride", session_id=self.page_session,
                      width=width, height=height, deviceScaleFactor=1, mobile=False)
        # Focus emulation keeps rAF/menus alive in a background tab without
        # stealing the user's foreground window.
        self.cdp.call("Emulation.setFocusEmulationEnabled", session_id=self.page_session, enabled=True)
        self.cdp.call("Page.addScriptToEvaluateOnNewDocument", session_id=self.page_session,
                      source=HELPER_SRC)

    def _attach_page(self, url: str) -> None:
        result = self.cdp.call("Target.createTarget", url=url, background=self.background)
        self.target_id = result["targetId"]
        self.page_session = self.cdp.call(
            "Target.attachToTarget", targetId=self.target_id, flatten=True
        )["sessionId"]
        self._prepare_page()
        if not self.background:
            try:
                self.cdp.call("Target.activateTarget", targetId=self.target_id)
            except CdpError:
                pass
        self._refresh_tabs()
        self._known_targets = {tab["target_id"] for tab in self.tabs}

    def close(self) -> None:
        if self.target_id:
            try:
                self.cdp.call("Target.closeTarget", targetId=self.target_id)
            except CdpError:
                pass
            self.target_id = ""

    # ------------------------------------------------------------- primitives

    def _ensure_helper(self) -> None:
        version = self._safe_eval("(window.__jevMcp && window.__jevMcp.version) || 0")
        if version != HELPER_VERSION:
            self.cdp.evaluate(HELPER_SRC, self.page_session, timeout=15)

    def _safe_eval(self, expression: str, *, await_promise: bool = False, timeout: float = 8.0):
        """Evaluate, returning None when the execution context is mid-navigation."""
        try:
            return self.cdp.evaluate(expression, self.page_session,
                                     await_promise=await_promise, timeout=timeout)
        except CdpError:
            return None

    def evaluate_js(self, expression: str):
        """Public JS escape hatch — only reachable when JEVMCP_ALLOW_JS is on."""
        if not self.cfg.allow_js:
            raise ValueError("JS evaluation is disabled; set JEVMCP_ALLOW_JS=1 to enable it")
        return self.cdp.evaluate(expression, self.page_session)

    def _call(self, method: str, timeout: float | None = None, **params):
        return self.cdp.call(method, session_id=self.page_session, timeout=timeout, **params)

    def _refresh_tabs(self) -> list[dict]:
        try:
            targets = self.cdp.call("Target.getTargets")["targetInfos"]
        except CdpError:
            return self.tabs
        tabs = []
        for info in targets:
            if info.get("type") != "page" or info.get("url", "").startswith(("devtools://",)):
                continue
            tabs.append({
                "index": len(tabs),
                "target_id": info["targetId"],
                "url": info.get("url", ""),
                "title": info.get("title", ""),
                "active": info["targetId"] == self.target_id,
            })
        self.tabs = tabs
        return tabs

    # ----------------------------------------------------------- navigation

    def navigate(self, url: str, *, timeout: float | None = None) -> None:
        check_url(self.cfg, url)
        self.cdp.events.clear()
        self._call("Page.navigate", url=url)
        # The budget for waiting on this navigation runs from here, not from
        # each read of the page, so a site that polls forever costs one
        # `settle_timeout` rather than one per read.
        self._awaiting_idle = True
        self._idle_deadline = time.monotonic() + self.cfg.settle_timeout
        self._wait_loaded(timeout)

    def _wait_loaded(self, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (timeout or self.cfg.nav_timeout)
        settled = False
        while time.monotonic() < deadline:
            if not settled:
                self._call("Runtime.evaluate", expression="1", returnByValue=True, timeout=2)
                settled = True
            if any(message.get("method") == "Page.loadEventFired" for message in self.cdp.events):
                break
            if self._safe_eval("document.readyState") == "complete":
                break
            time.sleep(0.02)
        self._ensure_helper()
        return self._safe_eval("document.readyState") == "complete"

    # --------------------------------------------------- has it stopped fetching

    def _drain_events(self) -> None:
        """Move whatever the browser has queued into `self.cdp.events`.

        The client only collects events while a command is in flight, so
        anything the page did while we were asleep is still sitting in the
        socket. A no-op evaluate is the cheapest command that makes it read
        them.
        """
        self._safe_eval("1", timeout=2)

    def page_is_idle(self) -> bool:
        """Whether the page we navigated to has stopped fetching.

        `load` fires when the HTML and its synchronous subresources are in. A
        client-rendered app then goes on to fetch its bundle and its data, and
        *that* is the half an element table is waiting for. Nothing in the DOM
        separates "the shell is up because the app has nothing to show" from
        "the shell is up because the bundle is still downloading" -- the DOM is
        equally still in both cases -- so a settle rule that watches only the
        DOM settles on the shell and hands the model a page with no controls
        on it. Network activity is the difference, so network activity is what
        this asks about.

        Counted from the `Network` domain's own events rather than from the
        browser's `networkIdle` lifecycle state. That state is defined as half
        a second of quiet, so it would charge every navigation half a second
        even for a page that finished loading long ago; zero requests in flight
        is the same answer immediately.

        True means "there is nothing to wait for", which covers the two cases
        where waiting would be wrong: every request we saw has finished, and we
        never navigated at all -- a session handed a page it did not open has
        no load of ours to wait for. It also goes true once the budget expires,
        because a page that polls forever must cost a bounded wait rather than
        hang the goal. So this answers "may I stop?", not "is it ready".
        """
        if not self._awaiting_idle:
            return True
        if time.monotonic() >= self._idle_deadline:
            self._awaiting_idle = False
            return True
        self._drain_events()
        sent: set[str] = set()
        done: set[str] = set()
        for message in self.cdp.events:
            if message.get("sessionId") != self.page_session:
                continue
            method = message.get("method")
            if method not in ("Network.requestWillBeSent", "Network.loadingFinished",
                              "Network.loadingFailed"):
                continue
            request_id = (message.get("params") or {}).get("requestId")
            if request_id:
                (sent if method == "Network.requestWillBeSent" else done).add(request_id)
        if sent - done:
            return False
        self._awaiting_idle = False
        return True

    def _history(self, delta: int) -> None:
        entries = self._call("Page.getNavigationHistory")
        index = entries["currentIndex"] + delta
        if index < 0 or index >= len(entries["entries"]):
            raise CdpError("No history entry in that direction")
        self.cdp.events.clear()
        self._call("Page.navigateToHistoryEntry", entryId=entries["entries"][index]["id"])
        self._wait_loaded()

    def _resolve_tab(self, index: int | None = None, target_id: str | None = None) -> dict:
        """Turn a tab reference into a live tab entry.

        An index is positional and the target list is renumbered whenever it
        changes -- activating a tab alone can reorder it. So an index is only
        trustworthy if it was read in the same breath as the action that uses
        it, which is exactly the mistake that makes "close tab 1" close the
        wrong tab. A `target_id` is stable and is the safe reference to carry
        across calls.
        """
        tabs = self._refresh_tabs()
        if target_id:
            match = next((tab for tab in tabs if tab["target_id"] == target_id), None)
            if match is None:
                raise CdpError(f"No tab with that target_id any more; {len(tabs)} tab(s) open")
            return match
        if index is None:
            raise CdpError("A tab action needs either index or target_id")
        if index < 0 or index >= len(tabs):
            raise CdpError(f"No tab at index {index}; {len(tabs)} tab(s) open")
        return tabs[index]

    def switch_tab(self, index: int | None = None, *, target_id: str | None = None) -> None:
        target = self._resolve_tab(index, target_id)
        if target["target_id"] == self.target_id:
            return
        self.target_id = target["target_id"]
        self.page_session = self.cdp.call(
            "Target.attachToTarget", targetId=self.target_id, flatten=True
        )["sessionId"]
        self._prepare_page()
        self._ensure_helper()
        self.last = None  # a fresh tab is a fresh observation context
        self._refresh_tabs()

    def _await_tab(self, *, exclude: str = "", timeout: float = 3.0) -> dict | None:
        """Wait for a tab that is not `exclude` to be attachable.

        `Target.closeTarget` is a request, not a fact: the closed target keeps
        appearing in `Target.getTargets` for a moment afterwards. So "the list is
        non-empty" is not the same as "there is a tab left to drive", and
        attaching to the tab that is on its way out raises. Wait for a survivor.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = [tab for tab in self._refresh_tabs() if tab["target_id"] != exclude]
            if remaining:
                return remaining[0]
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

    def close_tab(self, index: int | None = None, *, target_id: str | None = None) -> None:
        if not self._refresh_tabs():
            return
        if target_id or index is not None:
            target = self._resolve_tab(index, target_id)["target_id"]
        else:
            target = self.target_id  # no reference: close the tab we are driving
        if target == self.target_id:
            self.close()
            survivor = self._await_tab(exclude=target)
            if survivor is not None:
                self.switch_tab(target_id=survivor["target_id"])
        else:
            self.cdp.call("Target.closeTarget", targetId=target)
        self._refresh_tabs()

    # -------------------------------------------------------------- observe

    def _read_state(self, *, include_text: bool) -> dict:
        options = {
            "maxActions": self.cfg.max_actions,
            "maxText": self.cfg.max_text if include_text else 0,
            "includeText": include_text,
        }
        raw = self.cdp.evaluate(
            f"window.__jevMcp.readState({json.dumps(options)})",
            self.page_session, timeout=20,
        )
        if raw is None:
            raise PageStale("Page produced no snapshot (still navigating?)")
        return json.loads(raw)

    def _page_has_nodes(self) -> bool:
        """True when the document has elements, actionable or not."""
        count = self._safe_eval("document.body ? document.body.childElementCount : 0")
        return isinstance(count, int) and count > 0

    def reset_progress(self) -> None:
        """Forget the stall streak, so a new goal starts from a clean count.

        The streak belongs to a run, not to a session. A goal that inherits the
        previous goal's count would report itself stuck on its very first step.
        """
        self._no_change_streak = 0

    def observe(self, *, include_text: bool = True, full: bool = False,
                focus: list[str] | None = None) -> Observation:
        self._ensure_helper()
        data = self._read_state(include_text=include_text)
        if not data.get("actions") and self._page_has_nodes():
            # Content but nothing actionable. On a client-rendered page this is
            # what the very first read looks like -- the HTML arrived, the
            # JavaScript that fills it in has not run yet. Reporting "nothing
            # to act on" makes the agent change strategy for no reason and
            # spend a round trip discovering it was wrong, so wait for evidence
            # (an element appearing) rather than for a quiet period: Bing's
            # home page is completely still for about three seconds before it
            # paints, so "the DOM stopped moving" would give up exactly when
            # patience was needed. Bounded, so a genuinely inert page costs one
            # timeout and nothing more.
            deadline = time.monotonic() + self.cfg.settle_timeout
            while time.monotonic() < deadline:
                time.sleep(self.cfg.settle_poll_ms / 1000.0)
                data = self._read_state(include_text=include_text)
                if data.get("actions"):
                    break
        observation = Observation.from_raw(
            data, mask_secrets=lambda name, role: is_secret(self.cfg, name, role)
        )
        self.sequence += 1
        observation.sequence = self.sequence

        previous = self.last
        observation.previous = (
            previous if (not full and previous is not None and previous.url == observation.url) else None
        )

        tabs = self._refresh_tabs()
        seen = {tab["target_id"] for tab in tabs}
        if previous is not None:
            # A popup or a target=_blank page is exactly what jev's MVP cannot
            # follow; surfacing it as a first-class tab is the cheapest fix.
            observation.new_tabs = [tab for tab in tabs if tab["target_id"] not in self._known_targets]
        observation.tabs = tabs
        self._known_targets = seen
        self.last = observation
        self._fresh = True
        return observation

    # ---------------------------------------------------------------- actions

    def act(self, ops: list[dict], *, dry_run: bool = False, stop_on_error: bool = True,
            observe_after: bool = True) -> dict:
        if not isinstance(ops, list) or not ops:
            raise ValueError("act() needs a non-empty list of ops")
        if self.last is None:
            self.observe()

        results: list[Step] = []
        strict = self._fresh
        for raw_op in ops:
            step = self._run_op(raw_op, dry_run=dry_run, strict=strict)
            results.append(step)
            self.history.append(step)
            if step.ok and not dry_run:
                # The page may have moved on because *we* moved it. Later ops in
                # this batch get identity checks instead of a full-state match.
                strict = False
                self._fresh = False
            if not step.ok and stop_on_error:
                break

        payload: dict = {
            "ops": [step.to_dict() for step in results],
            "ok": all(step.ok for step in results),
            "steps": len(self.history),
        }
        if observe_after and not dry_run:
            try:
                observation = self.observe()
            except PageStale:
                observation = None
            if observation is not None:
                previous = observation.previous
                changed = previous is None or observation.digest != previous.digest
                self._no_change_streak = 0 if changed else self._no_change_streak + 1
                payload["page_changed"] = changed
                payload["view"] = observation.render(previous, mode="auto")
        if self._no_change_streak >= NO_CHANGE_LIMIT:
            payload["stuck"] = (
                f"{self._no_change_streak} consecutive actions changed nothing. "
                "Do not retry the same ref: re-read the observation, look for a "
                "covering dialog, or change strategy."
            )
        return payload

    def _resolve_ref(self, ref: str) -> tuple[str, str | None]:
        """Split `e12` / `e12:3` into the element ref and an optional option index."""
        if ":" in ref:
            head, _, tail = ref.partition(":")
            return (head if head.startswith("e") else "e" + head), tail
        return (ref if ref.startswith("e") else "e" + ref), None

    def _guard(self, ref: str, strict: bool | None = None) -> dict:
        """Ask the page whether this ref still means what the agent chose.

        `strict` compares the full observed page state; the relaxed form only
        re-checks that the ref is still the same control, which is what the
        second and later ops in a batch need.
        """
        strict = self._strict if strict is None else strict
        if strict:
            page_key = self.last.page_key if self.last else ""
            result = self._safe_eval(
                "window.__jevMcp.verify(%s, %s)" % (json.dumps(ref), json.dumps(page_key))
            )
        else:
            result = self._safe_eval("window.__jevMcp.reinspect(%s)" % json.dumps(ref))
        if not isinstance(result, dict):
            return {"ok": False, "reason": "page_unavailable"}
        return result

    def _geometry(self, ref: str) -> dict:
        result = self._safe_eval("window.__jevMcp.resolve(%s)" % json.dumps(ref))
        if not isinstance(result, dict):
            return {"ok": False, "reason": "page_unavailable"}
        return result

    def _ensure_reachable(self, ref: str, strict: bool | None = None) -> dict:
        """Verify, then nudge into view if needed, then verify geometry again.

        Scrolling before input is not a retry of a mutation — nothing has been
        dispatched yet — so it is safe, and it removes a whole class of
        "target changed or covered" round trips.
        """
        guard = self._guard(ref, strict)
        if not guard.get("ok"):
            return {"ok": False, "reason": guard.get("reason", "stale")}
        geometry = self._geometry(ref)
        if geometry.get("ok"):
            return geometry
        if geometry.get("reason") in SCROLLABLE_REASONS:
            self._safe_eval("window.__jevMcp.scrollTo(%s)" % json.dumps(ref))
            self._safe_eval("window.__jevMcp.settle('fast')", await_promise=True, timeout=4)
            geometry = self._geometry(ref)
            if geometry.get("ok"):
                return geometry
        return {"ok": False, "reason": geometry.get("reason", "unreachable")}

    def _label(self, ref: str) -> str:
        value = self._safe_eval("window.__jevMcp.label(%s)" % json.dumps(ref))
        return value if isinstance(value, str) else ""

    # ------------------------------------------------------------ op dispatch

    def _run_op(self, raw_op: dict, *, dry_run: bool, strict: bool = True) -> Step:
        self._strict = strict
        started = time.monotonic()
        if not isinstance(raw_op, dict):
            return Step(op="?", ok=False, error="bad_op", detail="each op must be an object")
        op = str(raw_op.get("op") or "").strip().lower()
        ref_raw = raw_op.get("ref")
        ref = None
        target_label = None
        # Set by the ops that have something to say beyond their target, so a
        # caller reading the step learns it without a second call. `None`, not
        # `""`: `Step.to_dict` drops `None` and would keep an empty string.
        note = None

        try:
            if op in REQUIRES_REF:
                if not ref_raw:
                    raise ValueError(f"op {op!r} needs a ref")
                ref, _ = self._resolve_ref(str(ref_raw))
                guard = self._guard(ref)
                if not guard.get("ok"):
                    raise PageStale(guard.get("reason", "stale"))

            if op == "click":
                target_label = self._label(ref)
                blocked = confirm_reason(self.cfg, target_label, raw_op.get("role") or "")
                if blocked and not raw_op.get("confirm"):
                    return Step(op=op, ref=ref, target=target_label, ok=False,
                                error="needs_confirmation",
                                detail=f"{blocked}; re-send with \"confirm\": true to proceed")
                if dry_run:
                    return Step(op=op, ref=ref, target=target_label, ok=True, detail="dry run")
                self._do_click(ref)
                self._after_input(("options" if raw_op.get("kind") == "combobox" else "fast"))

            elif op == "type":
                target_label = self._label(ref)
                if is_secret(self.cfg, target_label, raw_op.get("role") or "") and not raw_op.get("confirm"):
                    return Step(op=op, ref=ref, target=target_label, ok=False,
                                error="needs_confirmation",
                                detail="field looks sensitive; re-send with \"confirm\": true")
                if dry_run:
                    return Step(op=op, ref=ref, target=target_label, ok=True, detail="dry run")
                self._do_type(ref, str(raw_op.get("text") or ""), raw_op.get("clear", True),
                              raw_op.get("slow"))
                if raw_op.get("submit"):
                    self._dispatch_keys("Enter")
                self._after_input("fast")

            elif op == "select":
                wanted = raw_op.get("value", raw_op.get("label"))
                if wanted is None:
                    raise ValueError("select needs 'value' or 'label'")
                if dry_run:
                    return Step(op=op, ref=ref, ok=True, detail="dry run")
                selected = self._safe_eval(
                    "window.__jevMcp.selectOption(%s, %s)"
                    % (json.dumps(ref), json.dumps(str(wanted)))
                )
                if not isinstance(selected, dict) or not selected.get("ok"):
                    reason = (selected or {}).get("reason", "select_failed")
                    raise ValueError(f"could not select {wanted!r}: {reason}")
                target_label = str(wanted)
                self._after_input("fast")

            elif op == "toggle":
                current = self._safe_eval(
                    "(() => { const e=window.__jevRefs.nodes.get(%d); return e ? !!e.checked : null; })()"
                    % int(ref[1:])
                )
                want = raw_op.get("state")
                if want is not None and bool(want) == bool(current):
                    return Step(op=op, ref=ref, ok=True, detail="already in requested state")
                if dry_run:
                    return Step(op=op, ref=ref, ok=True, detail="dry run")
                self._do_click(ref)
                self._after_input("fast")

            elif op == "hover":
                if dry_run:
                    return Step(op=op, ref=ref, ok=True, detail="dry run")
                geometry = self._ensure_reachable(ref)
                if not geometry.get("ok"):
                    raise PageStale(geometry.get("reason", "unreachable"))
                self._call("Input.dispatchMouseEvent", type="mouseMoved",
                           x=geometry["x"], y=geometry["y"])
                self._after_input("fast")

            elif op == "upload":
                if not self.cfg.allow_uploads:
                    raise ValueError("uploads are disabled (JEVMCP_ALLOW_UPLOADS=0)")
                paths = raw_op.get("paths") or ([raw_op["path"]] if raw_op.get("path") else [])
                if not paths:
                    raise ValueError("upload needs 'path' or 'paths'")
                # An empty entry is not a path. `Path("")` is the current
                # directory, which exists, so it would sail past the check below
                # and `resolve()` would hand CDP the working directory as though
                # the caller had asked to upload it. Note the fix is *not*
                # `is_file()`: a directory is a legitimate argument here, because
                # `DOM.setFileInputFiles` takes one for a `webkitdirectory` input.
                blank = [p for p in paths if not str(p).strip()]
                if blank:
                    raise ValueError("upload paths must not be empty")
                missing = [p for p in paths if not Path(p).expanduser().exists()]
                if missing:
                    raise ValueError(f"file not found: {missing[0]}")
                resolved = [str(Path(p).expanduser().resolve()) for p in paths]
                if dry_run:
                    return Step(op=op, ref=ref, ok=True, detail="dry run")
                self._do_upload(ref, resolved)
                target_label = ", ".join(Path(p).name for p in resolved)
                self._after_input("fast")

            elif op == "keys":
                sequence = raw_op.get("keys") or raw_op.get("key")
                if not sequence:
                    raise ValueError("keys needs 'key' or 'keys'")
                if dry_run:
                    return Step(op=op, ok=True, detail="dry run")
                for key in ([sequence] if isinstance(sequence, str) else sequence):
                    self._dispatch_keys(str(key))
                target_label = str(sequence)
                self._after_input("fast")

            elif op == "scroll":
                direction = str(raw_op.get("dir") or raw_op.get("direction") or "down")
                amount = int(raw_op.get("amount") or 600)
                delta = amount if direction in {"down", "right"} else -amount
                if dry_run:
                    return Step(op=op, ok=True, detail=f"dry run {direction} {amount}")
                if ref is not None:
                    self._safe_eval("window.__jevMcp.scrollTo(%s)" % json.dumps(ref))
                    self._after_input("fast")
                else:
                    width, height = self.cfg.window
                    self._call("Input.dispatchMouseEvent", type="mouseWheel",
                               x=width // 2, y=height // 2,
                               deltaX=delta if direction in {"left", "right"} else 0,
                               deltaY=delta if direction in {"up", "down"} else 0)
                    self._after_input("fast")
                target_label = direction

            elif op in {"nav", "goto"}:
                url = str(raw_op.get("url") or "")
                if not url:
                    raise ValueError("nav needs 'url'")
                check_url(self.cfg, url)
                if dry_run:
                    return Step(op=op, ok=True, detail=f"dry run \u2192 {url}")
                self.navigate(url)
                self.last = None

            elif op == "back":
                if not dry_run:
                    self._history(-1)
                    self.last = None

            elif op == "forward":
                if not dry_run:
                    self._history(1)
                    self.last = None

            elif op == "reload":
                if not dry_run:
                    self.cdp.events.clear()
                    self._call("Page.reload")
                    self._wait_loaded()
                    self.last = None

            elif op == "wait":
                ms = int(raw_op.get("ms") or 500)
                if not dry_run:
                    time.sleep(max(0, min(ms, 15000)) / 1000)
                target_label = f"{ms}ms"

            elif op == "wait_for_ref":
                timeout = float(raw_op.get("timeout_ms") or 8000) / 1000
                deadline = time.monotonic() + timeout
                found = False
                while time.monotonic() < deadline:
                    if self._guard(ref, False).get("ok"):
                        found = True
                        break
                    time.sleep(0.05)
                if not found:
                    raise PageStale(f"ref {ref} did not become ready within {timeout:.1f}s")

            elif op == "wait_for_text":
                needle = str(raw_op.get("text") or "")
                if not needle:
                    raise ValueError("wait_for_text needs 'text'")
                timeout = float(raw_op.get("timeout_ms") or 8000) / 1000
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    body = self._safe_eval("document.body ? document.body.innerText : ''")
                    if body and needle.lower() in body.lower():
                        break
                    time.sleep(0.08)
                else:
                    raise PageStale(f"text {needle!r} never appeared")

            elif op == "wait_for_load":
                if not dry_run:
                    self._wait_loaded(float(raw_op.get("timeout_ms") or 20000) / 1000)

            elif op == "screenshot":
                if dry_run:
                    return Step(op=op, ok=True, detail="dry run")
                path, note = self._do_screenshot(raw_op)
                target_label = str(path)

            elif op == "eval":
                if not self.cfg.allow_js:
                    raise ValueError("eval is disabled; set JEVMCP_ALLOW_JS=1 to enable it")
                expression = str(raw_op.get("js") or "")
                if not expression:
                    raise ValueError("eval needs 'js'")
                if dry_run:
                    return Step(op=op, ok=True, detail="dry run")
                value = self._safe_eval(expression)
                target_label = json.dumps(value)[:200]

            elif op == "tab":
                action = str(raw_op.get("action") or "list")
                raw_index = raw_op.get("index")
                index = int(raw_index) if raw_index is not None else None
                target_id = str(raw_op.get("target_id") or "") or None
                if dry_run:
                    return Step(op=op, ok=True, detail="dry run")
                if action == "switch":
                    self.switch_tab(index, target_id=target_id)
                elif action == "close":
                    self.close_tab(index, target_id=target_id)
                elif action == "new":
                    url = str(raw_op.get("url") or "about:blank")
                    check_url(self.cfg, url)
                    self.cdp.call("Target.createTarget", url=url, background=self.background)
                self._refresh_tabs()
                which = target_id or (index if index is not None else "current")
                target_label = f"{action} tab {which}"

            else:
                raise ValueError(f"unknown op {op!r}")

        except (PageStale, CdpError, SafetyError, ValueError) as exc:
            return Step(op=op, ref=ref, target=target_label, ok=False,
                        error=_error_code(exc), detail=str(exc)[:300],
                        ms=int((time.monotonic() - started) * 1000))

        step = Step(op=op, ref=ref, target=target_label, ok=True, detail=note,
                    ms=int((time.monotonic() - started) * 1000))
        if not dry_run:
            self._record(raw_op, op, ref, target_label)
        return step

    # ------------------------------------------------------- input mechanics

    def _do_click(self, ref: str) -> None:
        geometry = self._ensure_reachable(ref)
        if not geometry.get("ok"):
            raise PageStale(geometry.get("reason", "unreachable"))
        x, y = geometry["x"], geometry["y"]
        self._call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for event in ("mousePressed", "mouseReleased"):
            self._call("Input.dispatchMouseEvent", type=event, x=x, y=y,
                       button="left", clickCount=1)

    def _do_type(self, ref: str, text: str, clear: bool, slow: bool | None) -> None:
        geometry = self._ensure_reachable(ref)
        if not geometry.get("ok"):
            raise PageStale(geometry.get("reason", "unreachable"))
        x, y = geometry["x"], geometry["y"]
        for event, extra in (("mousePressed", {"button": "left", "clickCount": 1}),
                             ("mouseReleased", {"button": "left", "clickCount": 1})):
            self._call("Input.dispatchMouseEvent", type=event, x=x, y=y, **extra)
        if clear:
            self._select_all()
        if slow:
            for char in text:
                self._call("Input.dispatchKeyEvent", type="keyDown", text=char)
                self._call("Input.dispatchKeyEvent", type="keyUp")
            return
        if text:
            self._call("Input.insertText", text=text)

    def _do_upload(self, ref: str, paths: list[str]) -> None:
        node_id = int(ref[1:])
        result = self._call(
            "Runtime.evaluate",
            expression=f"window.__jevRefs.nodes.get({node_id})",
            returnByValue=False,
        )
        remote = result.get("result", {})
        if not remote.get("objectId"):
            raise PageStale("file input is gone")
        self._call("DOM.setFileInputFiles", files=paths, objectId=remote["objectId"])

    def _capture(self, params: dict) -> tuple[dict, str | None]:
        """`Page.captureScreenshot`, retried once if the first attempt stalls.

        CI reports `Page.captureScreenshot: timed out after 30.0s`: the command is
        accepted and no frame comes back inside the client's own timeout, so it is
        a stall rather than a refusal. A retry is the remedy, and the run that
        proved it needed one twice -- both captures in the same section timed out
        on their first attempt and both succeeded on the second, and without the
        retry that job would have been red like most of its predecessors.

        The mechanism is *not* established, and the first guess was wrong: it is
        not "the first capture after a tab is closed and another promoted",
        because in the run above the second capture -- sent immediately after the
        first had succeeded -- stalled too. What survives is something about the
        state or the moment rather than the ordinal: it reproduces on CI's Chrome
        (152) and never on the local one (153) across a dozen runs, the page
        reports itself visible either way, and it hit one matrix entry out of
        three on the same commit and the same runner image, which reads as
        scheduling noise rather than a version.

        A silent retry would turn a real defect into a slow green build, so the
        step carries the fact that it happened: if this starts firing on every
        run, that is visible in the report instead of hidden behind a pass.

        The note is `None` and not `""` when there was no retry, because
        `Step.to_dict` drops `None` and keeps empty strings: a `""` would put a
        `detail` field on every successful step, which the extension's port does
        not emit, and the two reports are compared field for field.
        """
        try:
            return self._call("Page.captureScreenshot", **params), None
        except CdpError as exc:
            if "timed out" not in str(exc):
                raise
        result = self._call("Page.captureScreenshot", **params)
        return result, "the first capture timed out; this is the second attempt"

    def _do_screenshot(self, raw_op: dict) -> tuple[Path, str | None]:
        directory = self.cfg.state_dir / "shots"
        directory.mkdir(parents=True, exist_ok=True)
        fmt = str(raw_op.get("format") or "jpeg").lower()
        # `quality` is a JPEG-only parameter and CDP rejects an explicit null
        # for it, so it has to be absent rather than None.
        params: dict = {
            "format": "png" if fmt == "png" else "jpeg",
            "captureBeyondViewport": bool(raw_op.get("full")),
        }
        if fmt != "png":
            params["quality"] = 80
        result, note = self._capture(params)
        import base64
        name = raw_op.get("path") or f"shot-{int(time.time() * 1000)}.{'png' if fmt == 'png' else 'jpg'}"
        path = Path(name)
        if not path.is_absolute():
            path = directory / path.name
        path.write_bytes(base64.b64decode(result["data"]))
        return path, note

    def _select_all(self) -> None:
        import sys
        modifier = 4 if sys.platform == "darwin" else 2
        for event in ("keyDown", "keyUp"):
            self._call("Input.dispatchKeyEvent", type=event, key="a", code="KeyA",
                       windowsVirtualKeyCode=65, modifiers=modifier,
                       **({"commands": ["selectAll"]} if event == "keyDown" else {}))

    def _dispatch_keys(self, combo: str) -> None:
        parts = [part.strip().lower() for part in combo.replace("-", "+").split("+") if part.strip()]
        modifiers = 0
        while parts and parts[0] in MODIFIERS:
            modifiers |= MODIFIERS[parts.pop(0)]
        if not parts:
            return
        name = parts[-1]
        if len(name) == 1 and not modifiers:
            self._call("Input.insertText", text=name)
            return
        if name in KEY_SPECS:
            key, code, virtual = KEY_SPECS[name]
        elif len(name) == 1:
            key = name
            code = f"Key{name.upper()}"
            virtual = ord(name.upper())
        else:
            key, code, virtual = name, name, 0
        payload = dict(key=key, code=code, windowsVirtualKeyCode=virtual,
                       nativeVirtualKeyCode=virtual, modifiers=modifiers)
        self._call("Input.dispatchKeyEvent", type="rawKeyDown", **payload)
        self._call("Input.dispatchKeyEvent", type="keyUp", **payload)

    def _after_input(self, settle_kind: str) -> None:
        """Settle in-page, then absorb a navigation if one was triggered."""
        self.cdp.events.clear()
        self._safe_eval(
            "window.__jevMcp.settle(%s)" % json.dumps(settle_kind),
            await_promise=True, timeout=5,
        )
        if self._safe_eval("document.readyState") == "complete":
            return
        deadline = time.monotonic() + self.cfg.nav_timeout
        while time.monotonic() < deadline:
            if any(message.get("method") == "Page.loadEventFired" for message in self.cdp.events):
                break
            if self._safe_eval("document.readyState") == "complete":
                break
            time.sleep(0.03)
        self._ensure_helper()

    # ------------------------------------------------------------- recording

    def start_recording(self) -> None:
        self._recorder = []
        self._recording = True
        # The macro's start_url is where the task *began*, not where it ended.
        # A search flow ends on the results page; recording that as the start
        # makes replay begin somewhere the first step cannot be found -- which
        # is exactly what it did before this line existed.
        self._recording_start_url = self.current_url()

    def stop_recording(self, name: str, *, goal: str = "") -> dict:
        self._recording = False
        steps = list(self._recorder)
        start = self._recording_start_url or self.current_url()
        return macros_mod.save(self.cfg, name, steps, goal=goal, start_url=start)

    def current_url(self) -> str:
        return (self.last.url if self.last else self._safe_eval("location.href") or "")

    def _record(self, raw_op: dict, op: str, ref: str | None, label: str | None) -> None:
        if not self._recording:
            return
        # A macro is a file on disk. Never write a secret into one.
        element = self.last.by_ref.get(ref) if (self.last and ref) else None
        if element is not None and element.secret and op == "type":
            raw_op = {**raw_op, "text": "{{secret}}"}
        descriptor = macros_mod.describe(raw_op, op, ref, label, self.last)
        if descriptor is not None:
            self._recorder.append(descriptor)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, PageStale):
        return exc.args[0] if exc.args else "stale"
    if isinstance(exc, SafetyError):
        return "blocked_by_policy"
    if isinstance(exc, CdpError):
        return "browser_error"
    return "invalid_request"


# --------------------------------------------------------------------- manager


class BrowserManager:
    """Owns the browser connection and the named sessions on top of it."""

    def __init__(self, cfg: Config, *, allow_extensions: bool = False):
        self.cfg = cfg
        # Only the extension check sets this. Everything else wants the browser a developer's own
        # extensions cannot reach into -- see `launch_chrome`.
        self.allow_extensions = allow_extensions
        self._cdp: Cdp | None = None
        self._process = None
        self._profile: Path | None = None
        self._sessions: dict[str, Session] = {}

    @property
    def cdp(self) -> Cdp:
        """The live socket to the browser, rebuilt if the one we had went dead.

        A server outlives its socket. The user closes Chrome, the machine
        sleeps, a keepalive goes unanswered -- and `websockets` never
        reconnects, so the stored object goes on looking like a connection while
        answering nothing. Without this check the first hiccup is permanent:
        every later call fails with `transport closed` and no way back short of
        restarting the process. Checking here instead makes the failure one
        call wide, and it heals on the next one.
        """
        if self._cdp is not None and not self._cdp.alive:
            self._drop_connection()
        if self._cdp is None:
            self._connect()
        return self._cdp

    def _drop_connection(self) -> None:
        """Forget a socket that stopped carrying commands.

        The sessions go with it: each is bound to a socket and to target ids
        that only exist on it, so they cannot be replayed against a new one. The
        browser itself is left running -- in attach mode it is the user's, and
        in launch mode the process we started is usually still alive with only
        the socket gone, so `_connect` adopts it rather than starting a second
        one.
        """
        try:
            self._cdp.close()
        except Exception:  # noqa: BLE001 - the socket is already gone, which is the point
            pass
        self._cdp = None
        self._sessions.clear()

    def _connect(self) -> None:
        if self.cfg.mode == "attach":
            if not self.cfg.cdp_url:
                raise ChromeLaunchError("JEVMCP_CDP_URL is required when JEVMCP_MODE=attach")
            self._cdp = attach_chrome(
                self.cfg.cdp_url,
                timeout=self.cfg.call_timeout,
                data_dirs=self.cfg.attach_data_dirs(),
                # Chrome asks the user to approve a new debugging client. The
                # handshake waits on that click, so it gets a human-sized budget
                # rather than the call timeout.
                open_timeout=max(60.0, self.cfg.call_timeout),
            )
            return
        if (self._process is not None and self._process.poll() is None
                and self._profile is not None):
            self._cdp = reattach_chrome(self._profile, timeout=self.cfg.call_timeout)
            return
        self._cdp, self._process, self._profile = launch_chrome(
            self.cfg, allow_extensions=self.allow_extensions)

    def session(self, name: str = "default") -> Session:
        session = self._sessions.get(name)
        if session is None:
            session = Session(name=name, cfg=self.cfg, cdp=self.cdp,
                              background=not self.cfg.foreground)
            session.start("about:blank")
            self._sessions[name] = session
        return session

    def start(self, name: str, url: str) -> Session:
        session = self.session(name)
        if url and url != "about:blank":
            session.navigate(url)
        return session

    def close(self, name: str | None = None) -> list[str]:
        closed = []
        for key, session in list(self._sessions.items()):
            if name and key != name:
                continue
            session.close()
            self._sessions.pop(key, None)
            closed.append(key)
        return closed

    @property
    def owns_browser(self) -> bool:
        """Whether this process may stop the browser.

        In `launch` mode we started it, so stopping it is housekeeping. In
        `attach` mode the browser is the user's own: their tabs, their logins.
        `Browser.close` there would quit every window they have open -- and
        `atexit` would do it when the server exits -- so shutdown only detaches.
        """
        return self.cfg.mode != "attach"

    def shutdown(self) -> None:
        for session in list(self._sessions.values()):
            try:
                session.close()
            except Exception:  # noqa: BLE001 - teardown must not mask the result
                pass
        self._sessions.clear()
        if self._cdp is not None:
            if self.owns_browser:
                try:
                    self._cdp.call("Browser.close", timeout=3)
                except Exception:  # noqa: BLE001 - a browser that quit first is not a failure
                    pass
            self._cdp.close()
            self._cdp = None
        if self._process is not None:
            # Not `terminate()`: the browser leads its own process group and its
            # renderers outlive a signal aimed at the leader alone. See
            # `cdp.stop_chrome`.
            stop_chrome(self._process)
            self._process = None

    def doctor(self) -> dict:
        from .config import find_chrome
        # Deliberately does not go through `self.cdp`: reporting is read-only, and
        # a report that starts a browser is a report that lies about the state it
        # was asked to describe. So "connected" answers "is the socket we already
        # hold usable", which is the question a wedged server needs answered.
        held = self._cdp is not None
        live = held and self._cdp.alive
        report: dict = {
            "version": __import__("jev_ultrafast_mcp").__version__,
            "mode": self.cfg.mode,
            "headless": self.cfg.headless,
            "connected": live,
            "connection": "attached" if live else ("dropped" if held else "idle"),
            "sessions": sorted(self._sessions),
            "typesafe_turbo": bool(self.cfg.typesafe_key),
            "text_model": bool(self.cfg.text_model_key),
            "js_eval": self.cfg.allow_js,
            "uploads": self.cfg.allow_uploads,
            "allow_domains": self.cfg.allow_domains,
            "deny_domains": self.cfg.deny_domains,
        }
        try:
            report["chrome"] = find_chrome(self.cfg.chrome)
        except RuntimeError as exc:
            report["chrome_error"] = str(exc)
        if live:
            try:
                version = self._cdp.call("Browser.getVersion")
                report["browser"] = version.get("product")
                report["protocol"] = version.get("protocolVersion")
            except CdpError as exc:
                report["browser_error"] = str(exc)
        elif held:
            report["browser_error"] = "the socket to the browser dropped; the next call reconnects"
        return report
