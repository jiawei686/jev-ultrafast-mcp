"""Resolve the extension's macro fixtures with the real Python resolver.

The extension can replay a recorded macro without asking a model anything, and it
does that with a JavaScript port of `macros.py`. A port is only worth having if it
agrees with the original, and the failure it invites is the quiet one: a scoring
rule that is off by a hair still resolves most steps, so the day it resolves the
*wrong* step is the day nobody is watching. The fixtures here are therefore not
hand-written expectations -- every `expected` block is whatever
`macros.resolve(...)` actually returned, including the two error messages, which
are compared in full.

That last part is stricter than it sounds. `resolve` refuses in two situations,
and both refusals are features: a weak match and an ambiguous one. A replay that
raises has left the page untouched, which is the whole point -- so "did it raise"
is part of the contract, and so is the sentence it raised with, because that
sentence is what a human reads to fix the macro.

Regenerate after touching `macros.py` or the resolver port:

    .venv/bin/python chrome-extension/test/make_macro_fixtures.py

`tests/test_macro_port.py` fails if the committed file is not what this script
produces, so the two cannot drift apart silently.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from jev_ultrafast_mcp import macros  # noqa: E402
from jev_ultrafast_mcp.observe import Observation  # noqa: E402

OUT = HERE.with_name("macro-fixtures.json")


def _observer_helpers():
    """Reuse `make_fixtures.py`'s `action()`/`observation()` builders.

    Not copy-pasted, because those two functions *are* the observer's wire
    format: if the observer grows a field, the render fixtures and these have to
    learn about it together, and one definition is the only way to guarantee it.
    """
    spec = importlib.util.spec_from_file_location("_jev_render_fixtures", HERE.with_name("make_fixtures.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _observer_helpers()
action = helpers.action

# Two reads off a live page, kept beside this file rather than inlined, so the payloads
# can be read and checked as what they are: observer output, unedited.
LIVE = json.loads(HERE.with_name("live-observation.json").read_text(encoding="utf-8"))


# --- macros that were actually recorded -----------------------------------------------
#
# These two are inlined from real files in `~/.jev-ultrafast-mcp/macros/` rather
# than read at generation time, because a fixture that depends on the author's
# home directory cannot be regenerated on anyone else's machine. The shapes are
# the point: one carries a `{{template}}`, the other is a fossil.

# `wb-try.json` -- recorded against DuckDuckGo, and the only macro on this machine
# that exercises a parameter.
REAL_SEARCH = [
    {
        "op": "type",
        "target": {"role": "combobox", "name": "Search with DuckDuckGo",
                   "context": "", "label": "Search with DuckDuckGo"},
        "text": "{{query}}",
    },
    {"op": "keys", "key": "Enter"},
]

# `tmp-1p3a-scratch.json` -- twelve identical clicks on the "今日任务" trigger.
#
# This is what the HOVER gap produced: a menu that only opens on hover was
# invisible to the decision model, so it clicked the trigger, the menu toggled,
# and the loop ran to `max_steps`. The file records the failure rather than the
# task. It is here because replay has to be *predictable* about repetition, and
# because it is the honest shape of a bad macro -- the resolver will happily
# resolve all twelve steps onto the same element, which is exactly why a
# `verify` after a replay is not optional.
REAL_SCRATCH_1P3A = [
    {
        "op": "click",
        "target": {"role": "button", "name": "今日任务", "context": "", "label": "今日任务"},
    }
    for _ in range(12)
]


def case(name: str, why: str, steps: list[dict], actions: list[dict] | None = None,
         params: dict | None = None, threshold: float = macros.DEFAULT_THRESHOLD,
         raw: dict | None = None, **extra) -> dict:
    """Run one case through the real resolver and freeze what it said.

    `raw` overrides `actions` for the cases taken off a live page: there the payload is
    the observer's own output, and rebuilding it from an action list would be exactly
    the hand-written approximation those cases exist to avoid.
    """
    if raw is None:
        raw = helpers.observation(actions or [], **extra)
    observation = Observation.from_raw(raw)
    try:
        ops, report = macros.resolve(steps, observation, params or {}, threshold=threshold)
        expected: dict = {"ops": ops, "report": report}
    except macros.MacroError as exc:
        expected = {"error": str(exc)}
    return {
        "name": name,
        "why": why,
        "steps": steps,
        "raw": raw,
        "params": params or {},
        "threshold": threshold,
        "expected": expected,
    }


def click(name: str, role: str = "button", context: str = "") -> dict:
    """The commonest step shape there is."""
    return {"op": "click", "target": {"role": role, "name": name, "context": context, "label": name}}


def build() -> dict:
    cases: list[dict] = []

    # --- how a score is built ---------------------------------------------------------

    cases.append(case(
        "exact-match",
        "role and name both equal: the base 0.6 plus 0.4, which is the .0 the report has to "
        "print as 1.0 rather than 1",
        [click("Check in")],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "near-role-family",
        "a macro recorded against a menuitem resolving against a button: 0.55 + 0.4. The "
        "family exists because the same control is announced differently by different markup",
        [click("Check in", role="menuitem")],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "a-role-outside-the-family-is-not-a-candidate",
        "a checkbox is not a button however it is labelled: score 0, so there is nothing to "
        "compare and the run stops before touching the page",
        [click("Check in")],
        [action("e1", "checkbox", "Check in")],
    ))

    cases.append(case(
        "case-and-collapsed-space",
        "`_norm` lowercases and collapses runs of space, so a macro recorded off a label that "
        "was re-flowed still matches",
        [click("  Check   IN ")],
        [action("e1", "button", "check in")],
    ))

    # Two characters where Python and JavaScript disagree about what "space" is.
    # JavaScript's `\s` counts U+FEFF and omits U+0085; Python's `str.split()` does
    # the opposite. A port that reaches for `/\s+/` gets both of these wrong, and
    # gets them wrong *silently* -- on a label with an invisible character in it,
    # which is exactly the label nobody proofreads.

    cases.append(case(
        "a-zero-width-no-break-space-is-not-a-space-to-python",
        "U+FEFF is whitespace to JavaScript's `\\s` and not to Python's `str.split()`. Folding "
        "it would make two different labels equal and score 1.0 where Python scores 0.8 -- a "
        "replay confident about the wrong element",
        [click("Check\uFEFFin")],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "a-next-line-character-is-a-space-to-python",
        "U+0085 is whitespace to Python and not to JavaScript's `\\s`, so the port has to call "
        "it one: the two sides agree on 1.0 only if the class is spelled out",
        [click("Check\u0085in")],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "substring-either-way",
        "either direction of containment earns 0.2: a shortened label on the page and a "
        "lengthened one both land",
        [click("Check in")],
        [action("e1", "button", "Check in"), action("e2", "button", "Check in now")],
    ))

    cases.append(case(
        "token-overlap-carries-a-reordered-name",
        "same words, different order, neither containing the other: the token overlap is 1.0 "
        "and the score is 0.6 + 0.2",
        [click("check in daily")],
        [action("e1", "button", "daily check in")],
    ))

    cases.append(case(
        "token-overlap-below-half-is-not-a-match",
        "a third of the words in common is not the same control, so the score is 0 and the "
        "step refuses rather than picking the nearest thing",
        [click("daily check in")],
        [action("e1", "button", "daily quiz result")],
    ))

    cases.append(case(
        "a-nameless-target-still-matches",
        "a macro step with no name falls back to the role alone: 0.6 + 0.1, which is exactly "
        "the threshold and therefore passes. `>=` and `>` are the difference between a usable "
        "macro and a broken one",
        [{"op": "click", "target": {"role": "button", "name": "", "context": ""}}],
        [action("e1", "button", "Anything at all")],
    ))

    cases.append(case(
        "the-threshold-is-what-refuses-a-weak-match",
        "a nameless target in a near-role family: 0.55 + 0.1 = 0.65, under 0.7, so it refuses. "
        "This is the only reachable score the default threshold rejects, and it is why the "
        "threshold is worth having",
        [{"op": "click", "target": {"role": "menuitem", "name": "", "context": ""}}],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "the-refusal-lists-what-it-considered",
        "when nothing clears the threshold the error names the top five in page order, so a "
        "human can see whether the macro is wrong or the page is. Order comes from a stable "
        "sort over equal scores",
        [{"op": "click", "target": {"role": "menuitem", "name": "", "context": ""}}],
        [action("e1", "button", "First"), action("e2", "button", "Second"),
         action("e3", "button", "Third"), action("e4", "button", "Fourth"),
         action("e5", "button", "Fifth"), action("e6", "button", "Sixth")],
    ))

    # --- ties, context, and the two refusals ------------------------------------------

    cases.append(case(
        "context-breaks-a-tie",
        "two identically-named buttons on different rows: context is worth 0.15, which clears "
        "the 0.06 margin, so the macro recorded on the right row wins instead of refusing",
        [click("Book", context="Outbound")],
        [action("e1", "button", "Book", context="Outbound"),
         action("e2", "button", "Book", context="Return")],
    ))

    cases.append(case(
        "a-tie-without-context-refuses",
        "the same two buttons with a macro that has no context: a perfect match on both sides "
        "is the silent misclick this design exists to prevent, so it raises instead of picking "
        "the first",
        [click("Book")],
        [action("e1", "button", "Book", context="Outbound"),
         action("e2", "button", "Book", context="Return")],
    ))

    cases.append(case(
        "a-tie-inside-the-margin-refuses",
        "1.0 against 0.95 -- a button and a link with the same label, which is what a styled "
        "anchor next to a real button looks like. 0.05 apart is inside the 0.06 margin, so the "
        "best match being perfect does not save it",
        [click("Submit")],
        [action("e1", "button", "Submit"), action("e2", "link", "Submit")],
    ))

    cases.append(case(
        "a-tie-from-two-token-scores-refuses",
        "0.733 against 0.7, both from the 0.2 * overlap term: the margin is compared after "
        "`round(score, 3)`, so the rounding is part of the decision and not cosmetic",
        [click("daily check streak")],
        [action("e1", "button", "streak daily"), action("e2", "button", "daily check bonus")],
    ))

    cases.append(case(
        "context-below-its-overlap-floor-is-ignored",
        "context only counts at 0.4 token overlap, so a context that shares one word out of "
        "four adds nothing and the tie stands",
        [click("Book", context="Outbound flights one way")],
        [action("e1", "button", "Book", context="Outbound"),
         action("e2", "button", "Book", context="Outbound")],
    ))

    cases.append(case(
        "context-at-its-overlap-floor-counts",
        "0.4 is the floor for a context to count and it is reached exactly here -- two of five "
        "words in common, one above a context that shares too little. The comparison is "
        "inclusive, and inclusive is what makes the tie breakable at all",
        [click("Book", context="outbound flights one")],
        [action("e1", "button", "Book", context="outbound flights bonus extra"),
         action("e2", "button", "Book", context="Return")],
    ))

    cases.append(case(
        "the-threshold-can-be-lowered",
        "the same 0.65 that the default rejects resolves at 0.6. Replay decides how sure it "
        "has to be; the resolver only measures",
        [{"op": "click", "target": {"role": "menuitem", "name": "", "context": ""}}],
        [action("e1", "button", "Check in")],
        threshold=0.6,
    ))

    # --- what the CJK character class costs -------------------------------------------
    #
    # `_tokens` splits on `[^a-z0-9]+`, which matches every CJK character outright.
    # So a Chinese label tokenises to the empty set, and both places that use tokens
    # -- the name-overlap branch and the context bonus -- quietly stop working for
    # the language this project is mostly used in. Three cases pin the behaviour:
    # the two that still work, and the one that cannot.

    cases.append(case(
        "cjk-exact-match-is-fine",
        "an exact CJK label never reaches the token branch -- equality is checked first -- so "
        "the ordinary case on a Chinese site resolves at 1.0",
        [click("签到领奖", role="menuitem")],
        [action("e1", "menuitem", "签到领奖")],
    ))

    cases.append(case(
        "cjk-substring-still-works",
        "containment is string-level, not token-level, so a label that only grew a suffix "
        "still resolves",
        [click("签到")],
        [action("e1", "menuitem", "签到领奖")],
    ))

    cases.append(case(
        "cjk-reordered-name-cannot-be-matched",
        "the same four characters in a different order: neither contains the other, and the "
        "token branch returns 0 because a CJK label has no tokens at all. Any reworded Chinese "
        "label is invisible to this resolver -- a real limit, reproduced here on purpose so "
        "the port does not paper over it",
        [click("签到领奖")],
        [action("e1", "menuitem", "领奖签到")],
    ))

    cases.append(case(
        "cjk-context-cannot-break-a-tie",
        "two Chinese buttons with the same label and different context: the context bonus "
        "needs tokens on both sides, gets none, and the tie refuses. On a Chinese site every "
        "context in a macro is inert, so a repeated label is unresolvable however carefully it "
        "was recorded",
        [click("签到", role="menuitem", context="今日任务")],
        [action("e1", "menuitem", "签到", context="今日任务"),
         action("e2", "menuitem", "签到", context="每日答题")],
    ))

    cases.append(case(
        "the-same-tie-in-english-resolves",
        "the control for the case above: identical markup, English labels, and the context "
        "bonus does its job. The difference between these two cases is the whole bug",
        [click("Check in", role="menuitem", context="Daily tasks")],
        [action("e1", "menuitem", "Check in", context="Daily tasks"),
         action("e2", "menuitem", "Check in", context="Daily quiz")],
    ))

    # --- parameters -------------------------------------------------------------------

    cases.append(case(
        "a-template-becomes-its-parameter",
        "the recorded DuckDuckGo macro, with the query filled in. One macro file, any search",
        REAL_SEARCH,
        [action("e1", "combobox", "Search with DuckDuckGo")],
        params={"query": "knee mri"},
    ))

    cases.append(case(
        "a-missing-parameter-is-left-alone",
        "no value supplied means the target keeps the literal `{{query}}` rather than "
        "becoming an empty search. A macro that silently sends nothing is worse than one that "
        "sends the wrong thing visibly",
        REAL_SEARCH,
        [action("e1", "combobox", "Search with DuckDuckGo")],
    ))

    cases.append(case(
        "parameters-are-stringified-the-way-python-does-it",
        "a number, a boolean, a dotted name and a placeholder padded with spaces: Python's "
        "`str()` gives `42`, `True` and the raw text, and a port that reaches for JavaScript's "
        "`String()` gets two of those wrong",
        [
            {"op": "type", "target": {"role": "textbox", "name": "Amount"}, "text": "{{amount}}"},
            {"op": "type", "target": {"role": "textbox", "name": "Recurring"}, "text": "{{recurring}}"},
            {"op": "type", "target": {"role": "textbox", "name": "Name"}, "text": "{{user.name}}"},
            {"op": "type", "target": {"role": "textbox", "name": "Padded"}, "text": "{{  padded  }}"},
        ],
        [action("e1", "textbox", "Amount"), action("e2", "textbox", "Recurring"),
         action("e3", "textbox", "Name"), action("e4", "textbox", "Padded")],
        params={"amount": 42, "recurring": False, "user.name": "wei", "padded": " spaced "},
    ))

    cases.append(case(
        "only-the-fields-the-macro-has-reach-the-op",
        "a `type` with no `submit` must not grow one, or a replay would press Enter on a form "
        "the recording never submitted",
        [{"op": "type", "target": {"role": "textbox", "name": "Amount"}, "text": "10"}],
        [action("e1", "textbox", "Amount")],
    ))

    # --- op shapes --------------------------------------------------------------------

    cases.append(case(
        "every-op-field-survives-resolution",
        "one step per field the resolver carries: text, value, key, state, dir, amount, ms, "
        "paths. A field dropped here is a replay that types the wrong thing",
        [
            {"op": "type", "target": {"role": "textbox", "name": "Query"}, "text": "knee",
             "submit": True},
            {"op": "select", "target": {"role": "combobox", "name": "Cabin"}, "value": "business"},
            {"op": "keys", "key": "Escape"},
            {"op": "toggle", "target": {"role": "checkbox", "name": "Nonstop"}, "state": False},
            {"op": "scroll", "target": {"role": "button", "name": "Results"}, "dir": "up",
             "amount": 300},
            {"op": "wait", "ms": 750},
            {"op": "wait_for_text", "text": "Booked"},
            {"op": "hover", "target": {"role": "button", "name": "今日任务"}},
        ],
        [action("e1", "textbox", "Query"), action("e2", "combobox", "Cabin"),
         action("e3", "checkbox", "Nonstop"), action("e4", "button", "Results"),
         action("e5", "button", "今日任务")],
    ))

    cases.append(case(
        "an-empty-string-is-still-a-field-the-macro-set",
        "clearing a field is an operation, not an omission: Python writes `text: ''` because "
        "the key is present, while a port that tests truthiness drops the step's whole point "
        "and leaves the old value in the box",
        [
            {"op": "type", "target": {"role": "textbox", "name": "Query"}, "text": ""},
            {"op": "select", "target": {"role": "combobox", "name": "Cabin"}, "value": ""},
        ],
        [action("e1", "textbox", "Query"), action("e2", "combobox", "Cabin")],
    ))

    cases.append(case(
        "an-empty-path-list-is-not-a-path-list",
        "`[]` is falsy in Python and truthy in JavaScript. An upload step with no paths has "
        "nothing to attach, and the two languages only agree here if the length is checked -- "
        "`if (step.paths)` would hand the executor an empty list to open",
        [{"op": "upload", "target": {"role": "file", "name": "CV"}, "paths": []}],
        [action("e1", "file", "CV")],
    ))

    cases.append(case(
        "an-empty-scroll-direction-is-not-a-direction",
        "the same falsy-empty trap on `dir`: with no direction there is no amount either, so "
        "the executor supplies both. Writing `dir: ''` and a default 600 would turn a step the "
        "recording never really made into a full-screen scroll",
        [{"op": "scroll", "target": {"role": "button", "name": "Results"}, "dir": ""}],
        [action("e1", "button", "Results")],
    ))

    cases.append(case(
        "a-zero-millisecond-wait-is-not-a-wait",
        "the third falsy field, after `dir` and `paths`: `ms: 0` is skipped in Python because "
        "a wait of no time is not a wait, and the executor's own default is what applies. A "
        "port that tests presence schedules a zero-length timer the recording never had",
        [{"op": "wait", "ms": 0}, {"op": "wait", "ms": 500}],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "a-name-overlap-just-below-half-is-still-nothing",
        "0.4 in common is under the 0.5 floor, so the element is not a candidate at all and the "
        "refusal says `best=none`. That floor is a decision about what counts as the same "
        "control, and moving it by a tenth turns this refusal into a 0.68 near-miss -- a "
        "different sentence in the report, which is how the fixtures notice",
        [click("daily check streak goal")],
        [action("e1", "button", "daily check bonus")],
    ))

    cases.append(case(
        "an-explicit-zero-amount-is-not-the-default",
        "`amount: 0` is a real value and `600` is only the fallback for a missing key. "
        "Python's `dict.get(key, default)` tests presence, and `|| 600` in a port would "
        "silently turn a no-op scroll into a full screen",
        [{"op": "scroll", "target": {"role": "button", "name": "Results"}, "dir": "down",
          "amount": 0}],
        [action("e1", "button", "Results")],
    ))

    cases.append(case(
        "a-scroll-without-a-direction-takes-both-defaults",
        "no `dir` means neither field is written at all -- the executor supplies them",
        [{"op": "scroll", "target": {"role": "button", "name": "Results"}}],
        [action("e1", "button", "Results")],
    ))

    cases.append(case(
        "upload-paths-are-substituted-one-by-one",
        "a path list is the one field where the template runs per item rather than once over "
        "the whole value",
        [{"op": "upload", "target": {"role": "file", "name": "CV"},
          "paths": ["/tmp/{{name}}-cv.pdf", "/tmp/{{name}}-cover.pdf"]}],
        [action("e1", "file", "CV")],
        params={"name": "wei"},
    ))

    cases.append(case(
        "ops-without-a-target-pass-straight-through",
        "a key press and a wait have nothing to resolve against, so they reach the executor "
        "untouched. Not every step is about an element",
        [{"op": "keys", "key": "Enter"}, {"op": "wait", "ms": 500},
         {"op": "wait_for_text", "text": "Done"}],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "an-empty-target-is-treated-as-no-target",
        "`{}` is a falsy dict in Python and a truthy object in JavaScript. The distinction is "
        "invisible until a macro carries a bare empty target, at which point the two languages "
        "disagree about whether there is anything to match",
        [{"op": "wait", "ms": 100, "target": {}}],
        [action("e1", "button", "Check in")],
    ))

    cases.append(case(
        "steps-without-an-op-are-skipped-without-renumbering",
        "a step that is not an operation is dropped, but the numbers the rest are reported "
        "under still come from the file, so a report line matches the macro a human opens",
        [{}, {"op": "click", "target": {"role": "button", "name": "Check in"}}, {}],
        [action("e1", "button", "Check in")],
    ))

    # --- the fossil -------------------------------------------------------------------

    cases.append(case(
        "a-recorded-failure-replays-predictably",
        "`tmp-1p3a-scratch.json`: twelve identical clicks recorded while the HOVER gap made "
        "the real target unreachable. Every step resolves to the same element at 1.0 and the "
        "replay would do exactly what the recording did. Predictable is not the same as "
        "correct, which is why replay ends in a `verify`",
        REAL_SCRATCH_1P3A,
        [action("e8", "button", "今日任务"), action("e9", "button", "最新回复")],
    ))

    cases.append(case(
        "the-fossil-after-the-gap-was-closed",
        "the same trigger once the menu it hides is in the table: the hover step is what "
        "reaches it, and this is the macro that should have been recorded",
        [
            {"op": "hover", "target": {"role": "button", "name": "今日任务", "context": ""}},
            {"op": "click", "target": {"role": "menuitem", "name": "签到领奖",
                                       "context": "今日任务"}},
        ],
        [action("e8", "button", "今日任务", hoverable=True),
         action("e645", "menuitem", "签到领奖", context="今日任务")],
    ))

    # --- observed on a live page ------------------------------------------------------
    #
    # The cases above describe the wire format. These four *are* it: the payloads come
    # from `live-observation.json`, which holds two reads off a launch-mode browser
    # looking at a page shaped like the real one. So `context` is present on exactly the
    # labels that repeat and nowhere else, `hoverable` is set on the trigger alone, and
    # the menu items sit in the middle of the list because that is where they entered
    # the DOM -- none of which a hand-written action list would have got right.
    #
    # The descriptors are the ones `macros.describe` produced from those same two reads,
    # which is the path a recording takes.

    cases.append(case(
        "a-live-page-and-the-menu-behind-its-trigger",
        "the path that matters: a hover step onto the trigger, then a click on the menu "
        "item that only exists afterwards. Both resolve at 1.0 against a payload the real "
        "observer produced, which is the whole premise of replaying this task",
        [
            {"op": "hover", "target": {"role": "button", "name": "今日任务",
                                       "context": "", "label": "今日任务"}},
            {"op": "click", "target": {"role": "menuitem", "name": "签到领奖",
                                       "context": "", "label": "签到领奖"}},
        ],
        raw=LIVE["after"],
    ))

    cases.append(case(
        "a-live-page-with-a-repeated-chinese-label",
        "two rows, both buttons labelled 签到, contexts 甲帖 签到 and 乙帖 签到. Neither "
        "context has a single token, so neither scores, so the tie stands and the step "
        "refuses. This is the CJK limitation happening on a live page rather than in a "
        "synthetic case -- and refusing is the right outcome of a wrong situation",
        [{"op": "click", "target": {"role": "button", "name": "签到",
                                    "context": "甲帖 签到", "label": "签到"}}],
        raw=LIVE["before"],
    ))

    cases.append(case(
        "a-live-page-with-a-repeated-english-label",
        "the same shape in English, and the interesting part is that it fails too: 'Post "
        "C Check in' against 'Post D Check in' overlaps by three words out of five, which "
        "clears the 0.4 floor for *both* candidates. The context bonus is all-or-nothing, "
        "so a context that differs by one word cannot break the tie it was recorded to "
        "break -- a limit that has nothing to do with CJK",
        [{"op": "click", "target": {"role": "button", "name": "Check in",
                                    "context": "Post C Check in", "label": "Check in"}}],
        raw=LIVE["before"],
    ))

    cases.append(case(
        "a-live-page-with-a-latin-letter-in-the-context",
        "the control for the case above, and the reason it is worth having: the same two "
        "rows with 帖子 A and 帖子 B behind them resolve, because {a} and {b} share no "
        "tokens. One Latin character is the entire difference between a replayable step "
        "and an unplayable one",
        [{"op": "click", "target": {"role": "button", "name": "打卡",
                                    "context": "帖子 A 打卡", "label": "打卡"}}],
        raw=LIVE["before"],
    ))

    return {
        "generator": "chrome-extension/test/make_macro_fixtures.py",
        "note": ("Every `expected` block is produced by the real Python resolver "
                 "(macros.resolve) -- either `{ops, report}` or `{error}`, the latter being the "
                 "exact sentence MacroError carried. Both are compared in full by "
                 "chrome-extension/test/macro-parity.mjs, because a refusal is part of the "
                 "contract: replay that raises has not touched the page."),
        # Frozen here as well as exercised through the cases, because a constant
        # is the cheapest thing to drift and the hardest to notice: a port with
        # `AMBIGUITY_MARGIN = 0.05` agrees with Python on every fixture whose
        # scores are far apart, and disagrees only on the day it matters.
        "limits": {
            "threshold": macros.DEFAULT_THRESHOLD,
            "ambiguity_margin": macros.AMBIGUITY_MARGIN,
            "context_weight": macros.CONTEXT_WEIGHT,
        },
        "cases": cases,
    }


def main() -> int:
    payload = build()
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    refusals = sum(1 for item in payload["cases"] if "error" in item["expected"])
    print(f"wrote {OUT.relative_to(OUT.parents[2])} \u2014 {len(payload['cases'])} cases, "
          f"{refusals} of them refusals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
