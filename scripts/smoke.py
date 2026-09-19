#!/usr/bin/env python
"""End-to-end proof for jev-ultrafast-mcp against a deliberately awkward page.

Runs the real thing: launches Chrome, serves the fixture over HTTP, drives it
through the same code paths the MCP tools use, and asserts on outcomes. Also
accounts for the two things that decide whether an agent loop is usable:
round trips and bytes handed to the model.

    python scripts/smoke.py [--headed] [--keep]
"""

from __future__ import annotations

import argparse
import http.server
import json
import socket
import sys
import tempfile
import threading
import time
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_ultrafast_mcp import macros as macros_mod  # noqa: E402
from jev_ultrafast_mcp.browser import BrowserManager, Session  # noqa: E402
from jev_ultrafast_mcp.config import Config  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
LIVE_MANAGERS: list = []
METRICS: dict[str, object] = {"observes": 0, "acts": 0, "ops": 0, "view_bytes": 0,
                              "full_bytes": 0, "delta_bytes": 0, "wall_ms": 0}


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    flag = "ok  " if ok else "FAIL"
    print(f"  [{flag}] {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m", flush=True)


def wait_until(pred, timeout: float = 10.0, interval: float = 0.1):
    """Poll `pred` until it returns something truthy, or give up.

    A fixed `time.sleep` is a flake waiting to happen: it is tuned on a warm
    laptop and then runs on a cold, oversubscribed CI runner. Poll the condition
    you actually care about instead.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


# ------------------------------------------------------------------ plumbing


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *args, **kwargs: None  # noqa: ARG005
    port = free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


def ref_of(observation, name: str, role: str | None = None) -> str | None:
    matches = observation.find(role, name)
    return matches[0].ref if matches else None


def observe(session: Session, *, mode: str = "auto", count: bool = True):
    started = time.monotonic()
    observation = session.observe()
    text = observation.render(observation.previous, mode=mode)
    if count:
        METRICS["observes"] = int(METRICS["observes"]) + 1
        METRICS["view_bytes"] = int(METRICS["view_bytes"]) + len(text)
        if observation.previous is None:
            METRICS["full_bytes"] = int(METRICS["full_bytes"]) + len(text)
        else:
            METRICS["delta_bytes"] = int(METRICS["delta_bytes"]) + len(text)
    observation.last_view = text  # type: ignore[attr-defined]
    observation.last_ms = int((time.monotonic() - started) * 1000)  # type: ignore[attr-defined]
    return observation


def act(session: Session, ops: list[dict], **kwargs):
    started = time.monotonic()
    payload = session.act(ops, **kwargs)
    payload["wall_ms"] = int((time.monotonic() - started) * 1000)
    METRICS["acts"] = int(METRICS["acts"]) + 1
    METRICS["ops"] = int(METRICS["ops"]) + len(ops)
    METRICS["view_bytes"] = int(METRICS["view_bytes"]) + len(payload.get("view") or "")
    METRICS["delta_bytes"] = int(METRICS["delta_bytes"]) + len(payload.get("view") or "")
    return payload


def op_ok(payload: dict, index: int = 0) -> bool:
    ops = payload.get("ops") or []
    return bool(ops) and ops[index].get("ok", False)


def op_error(payload: dict, index: int = 0) -> str:
    ops = payload.get("ops") or []
    return (ops[index].get("error") or "") if ops else "no ops"


# ---------------------------------------------------------------------- flow


def run(base: str, headed: bool) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="jev-smoke-"))
    cfg = Config.from_env()
    cfg.headless = not headed
    cfg.profile_dir = workdir / "profile"
    cfg.state_dir = workdir / "state"
    cfg.window = (1280, 860)
    cfg.allow_js = False

    manager = BrowserManager(cfg)
    started = time.monotonic()
    session = manager.session("smoke")
    session.navigate(f"{base}/fixture.html")
    observation = observe(session, mode="full")

    print(f"\033[2mchrome: {manager.cdp.call('Browser.getVersion').get('product')}\033[0m")

    # ---------------------------------------------------------------- 1. read
    section("1. Observation — one atomic read, indexed refs")
    check("page title read", observation.title == "Ultrafast Fixture", observation.title)
    check("element table is not empty", len(observation.elements) >= 12,
          f"{len(observation.elements)} elements")
    from_ref = ref_of(observation, "Where from?", "textbox")
    check("text field indexed", from_ref is not None, f"Where from? -> {from_ref}")
    check("native select exposes options",
          any(element.options for element in observation.elements),
          next((f"{element.ref} opts={len(element.options)}" for element in observation.elements
                if element.options), ""))
    shadow = ref_of(observation, "Shadow action", "button")
    check("shadow DOM element indexed", shadow is not None, f"Shadow action -> {shadow}")
    iframe_btn = ref_of(observation, "Iframe action", "button")
    check("same-origin iframe element indexed", iframe_btn is not None,
          f"Iframe action -> {iframe_btn}")
    password = next((e for e in observation.elements if e.secret), None)
    check("password field visible but value withheld",
          password is not None and password.value == "",
          f"{password.ref if password else '-'} name={password.name if password else '-'} "
          f"value={password.value!r}")

    # --------------------------------------------------- 2. batch of primitives
    section("2. Batch execution — three ops, one round trip")
    pax = ref_of(observation, "Passengers")
    nonstop = ref_of(observation, "Nonstop only")
    payload = act(session, [
        {"op": "type", "ref": from_ref, "text": "Zurich"},
        {"op": "select", "ref": pax, "label": "3 adults"},
        {"op": "toggle", "ref": nonstop},
    ])
    check("all three ops succeeded", payload["ok"],
          "  ".join(f"{o['op']}:{'ok' if o['ok'] else o.get('error')}" for o in payload["ops"]))
    observation = observe(session)
    check("typed value landed", any(e.value == "Zurich" for e in observation.elements),
          next((f"{e.ref}={e.value!r}" for e in observation.elements if e.value == "Zurich"), ""))
    check("select applied", any((e.current or "") == "3 adults" for e in observation.elements),
          next((f"{e.ref} current={e.current!r}" for e in observation.elements
                if (e.current or "") == "3 adults"), ""))
    check("checkbox toggled on",
          any(e.checked is True for e in observation.elements),
          next((f"{e.ref} checked={e.checked}" for e in observation.elements
                if e.checked is not None), ""))
    check("delta observation is far smaller than the full one",
          len(observation.last_view) < len(observation.render(None, mode="full")) * 0.8,
          f"delta={len(observation.last_view)}B vs full="
          f"{len(observation.render(None, mode='full'))}B")

    # ------------------------------------------------------- 3. autocomplete
    section("3. Combobox — type, then pick the suggestion")
    to_ref = ref_of(observation, "Where to?", "combobox")
    check("combobox indexed with TYPE + CLICK", to_ref is not None, f"Where to? -> {to_ref}")
    act(session, [{"op": "type", "ref": to_ref, "text": "Lon"}])
    observation = observe(session)
    options = observation.find("option")
    check("suggestions became real elements", bool(options),
          ", ".join(f"{o.ref} {o.name!r}" for o in options[:4]))
    london = next((o for o in options if o.name == "London"), None)
    if london:
        payload = act(session, [{"op": "click", "ref": london.ref}])
        check("suggestion clicked", op_ok(payload))
        observation = observe(session)
        check("input now holds the picked city",
              any(e.value == "London" for e in observation.elements),
              next((f"{e.ref}={e.value!r}" for e in observation.elements if e.value == "London"), ""))
    else:
        check("suggestion clicked", False, "no 'London' option observed")

    # ------------------------------------------------------------ 4. execute
    section("4. Submit and read the result")
    go = ref_of(observation, "Search", "button")
    payload = act(session, [{"op": "click", "ref": go}])
    check("search clicked", op_ok(payload))
    observation = observe(session)
    selects = observation.find("button", "Select")
    check("three result rows appeared", len(selects) == 3,
          ", ".join(f"{e.ref}@{e.context[:28]}" for e in selects))
    check("duplicate labels disambiguated by context",
          len({e.context for e in selects}) == 3,
          f"{len({e.context for e in selects})} distinct contexts")

    from jev_ultrafast_mcp import assertions as assertions_mod
    verdict = assertions_mod.run([
        {"type": "text_contains", "text": "Option 1"},
        {"type": "count_at_least", "role": "button", "name": "Select", "min": 3},
        {"type": "element_exists", "role": "button", "name": "Select"},
    ], observation)
    check("deterministic assertions pass", verdict["pass"],
          "; ".join(c["detail"] for c in verdict["checks"]))

    # -------------------------------------------------------------- 5. modal
    section("5. Occlusion — precomputed, not discovered by a failed click")
    open_modal = ref_of(observation, "Open modal", "button")
    act(session, [{"op": "click", "ref": open_modal}])
    observation = observe(session)
    go_now = ref_of(observation, "Search", "button")
    go_element = observation.by_ref.get(go_now or "")
    check("covering dialog reported", bool(observation.overlays),
          json.dumps(observation.overlays))
    check("covered control flagged before any click", bool(go_element and go_element.occluded),
          f"{go_now} occluded={go_element.occluded if go_element else '-'}")
    blocked = act(session, [{"op": "click", "ref": go_now}])
    check("click on a covered control is refused with a reason",
          (not op_ok(blocked)) and op_error(blocked) in {"occluded", "stale"},
          op_error(blocked))
    close_modal = ref_of(observation, "Close", "button")
    payload = act(session, [{"op": "click", "ref": close_modal}])
    check("dialog dismissed", op_ok(payload))
    observation = observe(session)
    go_now = ref_of(observation, "Search", "button")
    go_element = observation.by_ref.get(go_now or "")
    check("control reachable again", bool(go_element and not go_element.occluded),
          f"{go_now} occluded={go_element.occluded if go_element else '-'}")

    # -------------------------------------------------- 6. shadow + iframe
    section("6. Coverage beyond the MVP — shadow DOM and same-origin frames")
    shadow_now = ref_of(observation, "Shadow action", "button")
    payload = act(session, [{"op": "click", "ref": shadow_now}])
    check("shadow DOM button clicked", op_ok(payload), op_error(payload) if not payload["ok"] else "")
    check("shadow DOM handler ran",
          session._safe_eval("document.querySelector('shadow-widget').dataset.clicked") == "yes")

    frame_input = ref_of(observation, "City", "textbox")
    frame_button = ref_of(observation, "Iframe action", "button")
    check("iframe controls distinct from the top document",
          frame_input is not None and frame_input != from_ref,
          f"City -> {frame_input}, Where from? -> {from_ref}")
    payload = act(session, [
        {"op": "type", "ref": frame_input, "text": "Lisbon"},
        {"op": "click", "ref": frame_button},
    ])
    check("typed and clicked inside an iframe", payload["ok"],
          "  ".join(f"{o['op']}:{'ok' if o['ok'] else o.get('error')}" for o in payload["ops"]))
    frame_value = session._safe_eval(
        "document.getElementById('frame').contentDocument.getElementById('if-city').value")
    frame_out = session._safe_eval(
        "document.getElementById('frame').contentDocument.getElementById('if-out').textContent")
    check("keystrokes reached the frame document", frame_value == "Lisbon", f"value={frame_value!r}")
    check("click reached the frame document", frame_out == "iframe clicked:Lisbon", f"{frame_out!r}")

    # ----------------------------------------------------- 7. guards + upload
    section("7. Policy envelope and file upload")
    observation = observe(session)
    danger = ref_of(observation, "Delete account", "button")
    payload = act(session, [{"op": "click", "ref": danger}])
    check("destructive click held for confirmation", op_error(payload) == "needs_confirmation",
          op_error(payload))
    payload = act(session, [{"op": "click", "ref": danger, "confirm": True}])
    check("same click runs once confirmed", op_ok(payload))

    secret_ref = next((e.ref for e in observation.elements if e.secret), None)
    payload = act(session, [{"op": "type", "ref": secret_ref, "text": "hunter2"}])
    check("typing into a secret field held for confirmation",
          op_error(payload) == "needs_confirmation", op_error(payload))

    upload_target = Path(workdir / "cv.txt")
    upload_target.write_text("fixture upload\n", encoding="utf-8")
    file_ref = next((e.ref for e in observation.elements if e.role == "file"), None)
    check("off-screen controls are still addressable", file_ref is not None,
          f"file input -> {file_ref}, offscreen={observation.offscreen}")
    payload = act(session, [{"op": "upload", "ref": file_ref, "path": str(upload_target)}])
    check("file upload accepted", op_ok(payload), op_error(payload) if not payload["ok"] else "")
    check("file actually attached",
          session._safe_eval("document.getElementById('cv').files.length") == 1)

    # ------------------------------------------------------------- 8. stale
    section("8. Staleness — a replaced node invalidates its ref")
    stale_ref = selects[0].ref
    act(session, [{"op": "click", "ref": go_now}])  # rebuilds the result list
    payload = act(session, [{"op": "click", "ref": stale_ref}])
    check("ref to a replaced node is rejected", not op_ok(payload), op_error(payload))
    check("reason is stale/detached", op_error(payload) in {"stale", "detached", "target_changed"},
          op_error(payload))

    # ----------------------------------------------------- 9. stable refs
    section("9. Stable refs across observations")
    before = ref_of(session.last, "Nonstop only")
    observe(session)
    observe(session)
    after = ref_of(session.last, "Nonstop only")
    check("ref survives repeated observations", before == after, f"{before} -> {after}")

    # ------------------------------------------------------------- 10. macro
    section("10. Macro — record once, replay with no model calls")
    session.navigate(f"{base}/fixture.html")
    observe(session, mode="full")
    session.start_recording()
    snapshot = session.last
    act(session, [{"op": "type", "ref": ref_of(snapshot, "Where from?"), "text": "Lyon"}])
    act(session, [{"op": "select", "ref": ref_of(snapshot, "Passengers"), "label": "2 adults"}])
    act(session, [{"op": "click", "ref": ref_of(session.last, "Search", "button")}])
    saved = session.stop_recording("smoke-search", goal="search Lyon for 2 adults")
    check("macro saved with semantic steps", saved["steps"] == 3, json.dumps(saved))
    stored = macros_mod.load(cfg, "smoke-search")
    check("macro stores no refs, only role+name",
          all("ref" not in step and "target" in step for step in stored["steps"]),
          json.dumps(stored["steps"][0], ensure_ascii=False))

    session.navigate(f"{base}/fixture.html")
    fresh = observe(session, mode="full")
    ops, report = macros_mod.resolve(stored["steps"], fresh)
    check("all steps resolved against a fresh page",
          all(item.get("score", 0) >= 0.7 for item in report),
          " ".join(f"{i['op']}@{i.get('score')}" for i in report))
    replay = act(session, ops)
    check("replay succeeded", replay["ok"],
          "  ".join(f"{o['op']}:{'ok' if o['ok'] else o.get('error')}" for o in replay["ops"]))
    final = observe(session)
    verdict = assertions_mod.run([
        {"type": "count_at_least", "role": "button", "name": "Select", "min": 3},
        {"type": "text_contains", "text": "Lyon"},
    ], final)
    check("replayed macro produced the real outcome", verdict["pass"],
          "; ".join(c["detail"] for c in verdict["checks"]))

    # ------------------------------------------------------------- 11. delta
    section("11. Delta economy")
    observe(session)
    second = observe(session)
    check("no-op observation collapses to one line",
          "= no change" in second.last_view, second.last_view.splitlines()[-1][:80])
    check("delta is a fraction of a full table",
          len(second.last_view) < len(second.render(None, mode="full")) * 0.25,
          f"{len(second.last_view)}B vs {len(second.render(None, mode='full'))}B")

    # ------------------------------------------------------- 12. new tab
    section("12. Tabs — a page-opened tab is reported, not lost")
    session.navigate(f"{base}/fixture.html")
    observation = observe(session, mode="full")
    popup = ref_of(observation, "Open popup", "link")
    act(session, [{"op": "click", "ref": popup}])

    def popup_tab():
        return next((t for t in session._refresh_tabs() if "popup" in t["url"]), None)

    def fixture_tab():
        return next((t for t in session._refresh_tabs() if "fixture" in t["url"]), None)

    popped = wait_until(popup_tab, timeout=15.0)
    observation = observe(session)
    check("page-opened tab surfaced", popped is not None or len(observation.tabs) >= 2,
          json.dumps([t["url"] for t in observation.tabs]))
    if popped is not None:
        # Carry the stable target_id, not the index: indexes are positional and
        # get renumbered whenever the target list changes, so "close tab 1" can
        # close the wrong tab a call later.
        session.switch_tab(target_id=popped["target_id"])
        check("switched into the new tab",
              "popup" in (session.last.url if session.last else "")
              or "popup" in (session._safe_eval("location.href") or ""),
              session._safe_eval("location.href"))
        session.close_tab(target_id=popped["target_id"])
        back = wait_until(fixture_tab, timeout=15.0)
        if back is not None:
            session.switch_tab(target_id=back["target_id"])
        check("closed the tab and returned",
              "fixture" in (session._safe_eval("location.href") or ""),
              session._safe_eval("location.href"))

    # --------------------------------------------------------- 13. screenshot
    section("13. Screenshot goes to disk, never into the context")
    payload = act(session, [{"op": "screenshot"}])
    shot = Path((payload["ops"][0].get("target") or ""))
    check("screenshot written to a file", shot.exists() and shot.stat().st_size > 1000,
          f"{shot.name} {shot.stat().st_size if shot.exists() else 0}B")

    elapsed = int((time.monotonic() - started) * 1000)
    METRICS["wall_ms"] = elapsed

    # ------------------------------------------------------------- summary
    section("Summary")
    ops = int(METRICS["ops"])
    acts = int(METRICS["acts"])
    print(f"  ops executed          : {ops}")
    print(f"  act() calls           : {acts}   (batch factor {ops / max(acts, 1):.2f} ops per call)")
    print(f"  observe() calls       : {METRICS['observes']}")
    print(f"  bytes shown to agent  : {METRICS['view_bytes']:,} "
          f"(~{METRICS['view_bytes'] // 4:,} tokens)")
    print(f"    of which full tables: {METRICS['full_bytes']:,}")
    print(f"    of which deltas     : {METRICS['delta_bytes']:,}")
    print(f"  wall clock            : {elapsed / 1000:.1f}s")

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("  failures:")
        for name in failed:
            print(f"    - {name}")

    (workdir / "metrics.json").write_text(json.dumps(
        {"metrics": METRICS, "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in RESULTS]},
        indent=2), encoding="utf-8")
    print(f"\n  work dir: {workdir}")

    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="run with a visible window")
    parser.add_argument("--keep", action="store_true", help="leave the browser running")
    args = parser.parse_args()

    httpd, base = serve(ROOT / "tests")
    print(f"fixture server: {base}/fixture.html")
    try:
        return run(base, args.headed)
    finally:
        httpd.shutdown()
        if not args.keep:
            for manager in LIVE_MANAGERS:
                try:
                    manager.shutdown()
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())
