"""Render the extension's parity fixtures with the real Python renderer.

The Chrome extension shows the element table that `jev-ultrafast-mcp` would hand a model, and it
does that with a JavaScript port of `observe.py`. A port is only worth anything if it agrees with
the original, so the fixtures here are not hand-written expectations: every `expected` string in
`fixtures.json` is produced by calling `Observation.from_raw(...).render(...)` in Python. The
JavaScript side then has to reproduce it character for character
(`chrome-extension/test/render-parity.mjs`).

Regenerate after touching the renderer on either side:

    .venv/bin/python chrome-extension/test/make_fixtures.py

`tests/test_extension.py` fails if the committed file is not what this script produces, so a change
to `observe.py` cannot silently leave the extension rendering the old thing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jev_ultrafast_mcp.config import Config
from jev_ultrafast_mcp.observe import Observation
from jev_ultrafast_mcp.safety import is_secret

OUT = Path(__file__).with_name("fixtures.json")

# The renderer defaults the server actually uses: `server.py::_view` passes
# `max_text=CONFIG.max_text`, which is 6000 unless the environment overrides it.
SERVER_MAX_TEXT = Config().max_text


def action(ref: str, role: str, name: str = "", **extra) -> dict:
    """One row of the observer's `actions[]`, with the keys it always emits."""
    item = {
        "ref": ref, "role": role, "name": name, "label": name, "value": "",
        "editable": False, "occluded": False, "inViewport": True,
        "hoverable": False, "disabled": False,
        "checked": None, "expanded": None, "current": None, "options": [],
        "opts_total": 0, "secret": False, "context": "", "accept": None, "multiple": False,
    }
    item.update(extra)
    return item


def observation(actions: list[dict], **extra) -> dict:
    raw = {
        "url": "https://example.com/flights", "title": "Flights", "text": "",
        "scroll": {"y": 0, "height": 1240}, "reachable": 0, "actions": actions,
        "omitted": 0, "offscreen": 0, "overlays": [], "cross_frames": 0,
        "cross_frame_srcs": [], "digest": "d", "page_key": "k",
    }
    raw.update(extra)
    raw["reachable"] = extra.get("reachable", sum(
        1 for a in actions if a["inViewport"] and not a["occluded"] and not a["disabled"]
    ))
    raw["offscreen"] = extra.get("offscreen", sum(1 for a in actions if not a["inViewport"]))
    return raw


def mask(name: str, role: str) -> bool:
    return is_secret(Config(), name, role)


def case(name: str, why: str, raw: dict, new_tabs: list[dict] | None = None,
         **options) -> dict:
    opts = {"mode": "auto", "includeText": True, "focus": None,
            "maxText": SERVER_MAX_TEXT, "sequence": 3, "maskSecrets": True}
    opts.update(options)
    previous = opts.pop("previous", None)

    current = Observation.from_raw(raw, mask_secrets=mask)
    current.sequence = opts["sequence"]
    # `new_tabs` is not part of the observer's payload. `browser.py` sets it after construction by
    # diffing the live tab list against the targets it already knew, which is the only layer that
    # can see tabs. Passing it through `raw` would test a shape the server cannot produce, so the
    # fixture carries it alongside `raw` and injects it here, the same way the harness does.
    current.new_tabs = list(new_tabs or [])
    before = None
    if previous is not None:
        before = Observation.from_raw(previous, mask_secrets=mask)
        before.sequence = opts["sequence"] - 1

    rendered = current.render(
        before, mode=opts["mode"], include_text=opts["includeText"],
        focus=opts["focus"], max_text=opts["maxText"],
    )
    return {"name": name, "why": why, "raw": raw, "previous": previous,
            "new_tabs": list(new_tabs or []), "options": opts, "expected": rendered}


def build() -> dict:
    cases = []

    # Every flag and every body variant, in one table.
    cases.append(case(
        "flags-and-bodies",
        "every flag (*, \u2298, \u00bb, \u2297, \u22ee, \u25be, \u2713, \u00b7) and every body "
        "shape (plain, editable, secret, options, file, context) in one table, including a "
        "hover trigger whose menu is already open, where the \u22ee must be suppressed",
        observation([
            action("e1", "link", "Skip to main content"),
            action("e2", "button", "Submit", occluded=True),
            action("e3", "textbox", "Where from?", editable=True, value="San Francisco"),
            action("e4", "textbox", "Password", editable=True, value="hunter2", secret=True),
            action("e5", "combobox", "Passengers", current="1 adult", opts_total=3, options=[
                {"ref": "e5:1", "label": "1 adult", "value": "1", "selected": True},
                {"ref": "e5:2", "label": "2 adults", "value": "2", "selected": False},
                {"ref": "e5:3", "label": "3 adults", "value": "3", "selected": False},
            ]),
            action("e6", "checkbox", "Nonstop only", checked=True),
            action("e7", "radio", "Round trip", checked=False),
            action("e8", "button", "Advanced options", expanded="true"),
            action("e9", "button", "Off-screen button", inViewport=False),
            action("e10", "file", "Upload CV", accept=".pdf,.txt", multiple=True),
            action("e11", "button", "Book", context="Outbound"),
            action("e12", "link", "Book", context="Return"),
            action("e13", "spinbutton", "Adults", editable=True, value="2"),
            action("e14", "button", "Daily tasks", hoverable=True),
            action("e15", "button", "Daily tasks", hoverable=True, expanded="true"),
            action("e16", "button", "Submit answer", disabled=True),
        ], text="Search flights \u2014 one way or round trip."),
    ))

    # Truncation, including the code-point boundary a naive port gets wrong.
    cases.append(case(
        "truncation",
        "the _short limits (160/120/100/24/18) and the code-point ellipsis, with an emoji "
        "straddling the cut so UTF-16 slicing would diverge from Python",
        observation([
            action("e1", "button", "\U0001f680 " + "x" * 200),
            action("e2", "textbox", "Notes", editable=True, value="y" * 300),
            action("e3", "combobox", "City", current="Z" * 200, opts_total=2, options=[
                {"ref": "e3:1", "label": "\U0001f6eb " + "l" * 40, "value": "v" * 60},
                {"ref": "e3:2", "label": "short", "value": "short"},
            ]),
            action("e4", "button", "Long context button", context="c" * 200),
        ], title="T" * 200, text="w " * 4000),
    ))

    # More options than fit, so the `+N` counter and the detail cutoff both matter.
    cases.append(case(
        "option-overflow",
        "12 options against the 8-option cutoff, so the `+4` counter appears; mode=full lifts "
        "the cutoff to 20 and also suppresses context",
        observation([
            action("e1", "combobox", "Seat", current="12A", opts_total=12, options=[
                {"ref": f"e1:{i}", "label": f"Seat {i}", "value": str(i), "selected": i == 12}
                for i in range(1, 13)
            ], context="Cabin"),
        ]),
    ))
    cases.append(case(
        "option-overflow-detail",
        "the same table at mode=full: 20-option cutoff, no context suffix",
        observation([
            action("e1", "combobox", "Seat", current="12A", opts_total=12, options=[
                {"ref": f"e1:{i}", "label": f"Seat {i}", "value": str(i), "selected": i == 12}
                for i in range(1, 13)
            ], context="Cabin"),
        ]),
        mode="full",
    ))

    # Nothing actionable.
    cases.append(case(
        "empty-table",
        "a page with no actionable elements still renders a header and says so",
        observation([], reachable=0, offscreen=0),
    ))

    # Overlays, unreadable frames, a tab that opened itself.
    cases.append(case(
        "overlays-and-frames",
        "the three header warnings: a modal overlay, cross-origin frames, and a new tab",
        observation(
            [action("e1", "button", "Accept")],
            overlays=[{"role": "dialog", "name": "Cookie consent", "modal": True},
                      {"role": "alertdialog", "name": "", "modal": False}],
            cross_frames=2,
            cross_frame_srcs=["https://ads.example.com/a", "https://tracker.example/b"],
        ),
        new_tabs=[{"url": "https://example.com/step2", "target_id": "T2"}],
    ))

    # Omitted and off-screen counters in the header.
    cases.append(case(
        "counters",
        "the `omitted` and `offscreen` counters, which ride in the header rather than on a line",
        observation(
            [action("e1", "button", "Visible"),
             action("e2", "button", "Below the fold", inViewport=False),
             action("e3", "button", "Also below", inViewport=False)],
            omitted=7,
        ),
    ))

    # Text handling.
    cases.append(case(
        "no-text",
        "includeText with an empty page text emits no `text:` block",
        observation([action("e1", "button", "Only")], text=""),
    ))
    cases.append(case(
        "text-suppressed",
        "includeText=False drops the block even when the page has text",
        observation([action("e1", "button", "Only")], text="Some page words."),
        includeText=False,
    ))

    # Secrets, including the two ways a field gets masked.
    cases.append(case(
        "secret-masking",
        "the observer's own `secret` flag and the server's name-pattern mask both hide the value",
        observation([
            action("e1", "textbox", "Card number", editable=True, value="4111111111111111"),
            action("e2", "textbox", "Search", editable=True, value="not a secret"),
            action("e3", "textbox", "API key", editable=True, value="sk-live-abc"),
            action("e4", "textbox", "Empty secret", editable=True, value="", secret=True),
        ]),
    ))

    # Delta rendering: three shapes of change plus the no-change line.
    base = observation([
        action("e1", "textbox", "Where from?", editable=True, value="Zurich"),
        action("e2", "checkbox", "Nonstop only", checked=False),
        action("e3", "button", "Search"),
    ], text="Same text.")
    cases.append(case(
        "delta-no-change",
        "an identical re-observation collapses to one line instead of the whole table",
        observation([
            action("e1", "textbox", "Where from?", editable=True, value="Zurich"),
            action("e2", "checkbox", "Nonstop only", checked=False),
            action("e3", "button", "Search"),
        ], text="Same text."),
        previous=base,
    ))
    cases.append(case(
        "delta-changes",
        "added, changed (with the was/now notes) and removed all at once, plus the tally line",
        observation([
            action("e1", "textbox", "Where from?", editable=True, value="Lyon"),
            action("e2", "checkbox", "Nonstop only", checked=True),
            action("e4", "button", "Reset"),
        ], text="Same text."),
        previous=base,
    ))
    cases.append(case(
        "delta-occlusion-note",
        "a change that is only occlusion reports (now covered) rather than a value diff",
        observation([
            action("e1", "textbox", "Where from?", editable=True, value="Zurich"),
            action("e2", "checkbox", "Nonstop only", checked=False),
            action("e3", "button", "Search", occluded=True),
        ], text="Same text."),
        previous=base,
    ))
    cases.append(case(
        "delta-forced",
        "mode=delta with no previous observation degrades to a full table rather than crashing",
        observation([action("e1", "button", "Only")], text="x"),
        mode="delta",
    ))

    # Focus: naming a ref renders that one line in detail.
    cases.append(case(
        "focus-detail",
        "focus= names a ref, which renders that line in detail (more options, no context)",
        observation([
            action("e1", "combobox", "Seat", current="12A", opts_total=12, options=[
                {"ref": f"e1:{i}", "label": f"Seat {i}", "value": str(i), "selected": i == 12}
                for i in range(1, 13)
            ], context="Cabin"),
        ]),
        focus=["e1"],
    ))

    # A non-ASCII name and title, so encoding is exercised rather than assumed.
    cases.append(case(
        "cjk",
        "CJK names and a CJK title, which is the normal case for half this project's readers",
        observation(
            [action("e1", "button", "\u63d0\u4ea4"),
             action("e2", "textbox", "\u51fa\u53d1\u5730", editable=True, value="\u5317\u4eac")],
            title="\u673a\u7968\u641c\u7d22", text="\u5355\u7a0b\u6216\u5f80\u8fd4",
        ),
    ))

    return {
        "generator": "chrome-extension/test/make_fixtures.py",
        "note": ("Every `expected` string is produced by the real Python renderer "
                 "(observe.Observation.from_raw(...).render(...)). The JavaScript port in "
                 "chrome-extension/lib/render.js must reproduce each one exactly."),
        "server_max_text": SERVER_MAX_TEXT,
        "cases": cases,
    }


def main() -> int:
    payload = build()
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(OUT.parents[2])} \u2014 {len(payload['cases'])} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
