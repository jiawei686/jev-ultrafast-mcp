"""Deterministic verification.

`DONE` is an opinion. An assertion is a fact. Every check here is evaluated by
code against the live page, so a run can be judged without trusting a model's
summary of its own work.
"""

from __future__ import annotations

import fnmatch
import json
import re

from .observe import Observation

CHECK_TYPES = [
    "url_matches", "url_contains", "title_matches", "text_contains", "text_absent",
    "element_exists", "element_gone", "value_equals", "checked", "count_at_least",
    "js",
]


def _fail(kind: str, detail: str) -> dict:
    return {"type": kind, "ok": False, "detail": detail}


def _ok(kind: str, detail: str) -> dict:
    return {"type": kind, "ok": True, "detail": detail}


def run(checks: list[dict], observation: Observation, *, allow_js: bool = False,
        eval_js=None) -> dict:
    results: list[dict] = []
    for check in checks or []:
        kind = str(check.get("type") or "").strip()
        try:
            results.append(_one(kind, check, observation, allow_js, eval_js))
        except Exception as exc:  # noqa: BLE001 - a bad check is a failed check
            results.append(_fail(kind or "unknown", f"check raised {type(exc).__name__}: {exc}"))
    return {
        "pass": bool(results) and all(result["ok"] for result in results),
        "checks": results,
        "url": observation.url,
    }


def _needle(check: dict, *keys: str) -> str:
    """The non-empty string a text-matching check has to be given.

    An absent or empty needle is a malformed check, not a match. `"" in s` is true for
    every string, so reading a missing key as an empty needle makes the check pass
    without looking at anything -- the one failure an assertion must not have, since a
    passing assertion is what overrules the model's own claim of success. A caller who
    misspells the key (`"value"` for `"text"`) got a pass rather than an error.
    """
    for key in keys:
        value = check.get(key)
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return ""


def _one(kind: str, check: dict, observation: Observation, allow_js: bool, eval_js) -> dict:
    if kind == "url_matches":
        pattern = str(check.get("pattern") or check.get("url") or "")
        return (_ok if fnmatch.fnmatch(observation.url, pattern) else _fail)(
            kind, f"url {observation.url!r} vs pattern {pattern!r}")
    if kind == "url_contains":
        needle = _needle(check, "text")
        if not needle:
            return _fail(kind, "url_contains needs a non-empty 'text'")
        found = needle.lower() in observation.url.lower()
        return (_ok if found else _fail)(kind, f"url={observation.url!r} contains {needle!r}: {found}")
    if kind == "title_matches":
        pattern = _needle(check, "pattern", "text")
        if not pattern:
            return _fail(kind, "title_matches needs a non-empty 'pattern'")
        found = fnmatch.fnmatch(observation.title, pattern) or pattern.lower() in observation.title.lower()
        return (_ok if found else _fail)(kind, f"title={observation.title!r} vs {pattern!r}")
    if kind in {"text_contains", "text_absent"}:
        needle = _needle(check, "text")
        if not needle:
            return _fail(kind, f"{kind} needs a non-empty 'text'")
        haystack = observation.text or ""
        found = needle.lower() in haystack.lower()
        if check.get("regex"):
            found = bool(re.search(needle, haystack, re.IGNORECASE))
        want = kind == "text_contains"
        return (_ok if found == want else _fail)(
            kind, f"{needle!r} {'found' if found else 'not found'} in page text")
    if kind in {"element_exists", "element_gone"}:
        role = check.get("role")
        name = check.get("name") or check.get("text")
        ref = check.get("ref")
        if ref:
            found = ref in observation.by_ref
            detail = f"ref {ref} {'present' if found else 'absent'}"
        else:
            matches = observation.find(role, name)
            found = bool(matches)
            detail = (f"{len(matches)} match(es) for role={role!r} name={name!r}"
                      + (f" e.g. {matches[0].ref} {matches[0].name!r}" if matches else ""))
        want = kind == "element_exists"
        return (_ok if found == want else _fail)(kind, detail)
    if kind == "value_equals":
        ref = check.get("ref")
        element = observation.by_ref.get(ref or "")
        if element is None:
            return _fail(kind, f"ref {ref!r} is not on the page")
        expected = check.get("value")
        actual = element.value or element.current or ""
        found = str(actual) == str(expected)
        return (_ok if found else _fail)(kind, f"{ref} value={actual!r} vs expected={expected!r}")
    if kind == "checked":
        ref = check.get("ref")
        element = observation.by_ref.get(ref or "")
        if element is None:
            return _fail(kind, f"ref {ref!r} is not on the page")
        if "state" in check:
            raw = check["state"]
        elif "checked" in check:
            # The type is named `checked` and its argument is named `state`, so a caller
            # will guess `checked`. Reading it costs nothing, and a guess that lands is
            # worth more than being right about the key name.
            raw = check["checked"]
        else:
            # No default. `state` used to default to True, so a check that said nothing
            # about which state it wanted asserted "checked" -- and a caller writing
            # `"checked": false` got the opposite of what they wrote, as a pass.
            return _fail(kind, "checked needs 'state' (true or false)")
        if isinstance(raw, str):
            # `bool("false")` is True. A model writing JSON by hand sends strings, and
            # getting this wrong asserts the opposite of what was asked for.
            raw = raw.strip().lower() not in {"false", "0", "no", "off", ""}
        want = bool(raw)
        found = bool(element.checked) == want
        return (_ok if found else _fail)(kind, f"{ref} checked={element.checked} expected={want}")
    if kind == "count_at_least":
        role = check.get("role")
        name = check.get("name") or check.get("text")
        minimum = int(check.get("min") or 1)
        count = len(observation.find(role, name))
        found = count >= minimum
        return (_ok if found else _fail)(kind, f"{count} match(es) >= {minimum}: {found}")
    if kind == "js":
        if not allow_js:
            return _fail(kind, "JS checks are disabled; set JEVMCP_ALLOW_JS=1 to enable")
        expression = str(check.get("expr") or "")
        if not expression or eval_js is None:
            return _fail(kind, "js check needs 'expr'")
        value = eval_js(expression)
        return (_ok if value else _fail)(kind, f"{expression} -> {json.dumps(value)[:120]}")
    return _fail(kind or "unknown", f"unknown check type; supported: {', '.join(CHECK_TYPES)}")
