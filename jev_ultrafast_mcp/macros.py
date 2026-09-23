"""Macros: replay a discovered path with zero model calls.

This is where an MCP server can beat a plain agent loop by a wide margin. A
macro stores *semantics*, never refs or selectors:

    {"op": "click", "target": {"role": "button", "name": "Search"}}

Replay re-resolves each target against a fresh observation. Matching is scored
and thresholded, and an ambiguous or weak match fails loudly rather than
clicking whatever happens to be nearby.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .observe import Observation

DEFAULT_THRESHOLD = 0.7
AMBIGUITY_MARGIN = 0.06
# Must exceed AMBIGUITY_MARGIN: a distinguishing context is the only thing that
# is allowed to break a tie between two identically-named controls.
CONTEXT_WEIGHT = 0.15
TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")

# What a recorded secret is replaced with before the step is written to disk. A
# macro is a file, and a file is the one place a password would outlive the run
# -- so the value is not stored at all and the caller supplies it at replay time
# under this name: `run name=login params={"secret": "..."}`. It is an ordinary
# `{{placeholder}}` rather than a special case, which is why the extension's
# replay panel offers it as a field without knowing what it is. Named here so
# both sides of the recording can say the same word; it used to be a literal in
# `browser.py` and a comment nowhere.
SECRET_PLACEHOLDER = "{{secret}}"
SECRET_PARAM = "secret"


class MacroError(RuntimeError):
    """A macro could not be resolved against the current page."""


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", _norm(text)) if token}


# --------------------------------------------------------------- descriptors


def describe(raw_op: dict, op: str, ref: str | None, label: str | None,
             observation: Observation | None) -> dict | None:
    """Turn an executed op into a page-independent descriptor."""
    step: dict = {"op": op}
    if ref and observation is not None:
        element = observation.by_ref.get(ref)
        if element is not None:
            step["target"] = {
                "role": element.role,
                "name": element.name,
                "context": element.context,
            }
            if element.name:
                step["target"]["label"] = element.label
    if op == "type":
        step["text"] = raw_op.get("text", "")
        if raw_op.get("submit"):
            step["submit"] = True
    elif op == "select":
        step["value"] = raw_op.get("value", raw_op.get("label"))
    elif op == "toggle":
        if raw_op.get("state") is not None:
            step["state"] = bool(raw_op["state"])
    elif op == "keys":
        step["key"] = raw_op.get("keys") or raw_op.get("key")
    elif op in {"nav", "goto"}:
        step["url"] = raw_op.get("url")
    elif op == "scroll":
        step["dir"] = raw_op.get("dir") or raw_op.get("direction") or "down"
        step["amount"] = int(raw_op.get("amount") or 600)
    elif op == "wait":
        step["ms"] = int(raw_op.get("ms") or 500)
    elif op == "wait_for_text":
        step["text"] = raw_op.get("text")
    elif op in {"hover", "upload", "screenshot", "wait_for_load"}:
        if op == "upload":
            step["paths"] = raw_op.get("paths") or ([raw_op["path"]] if raw_op.get("path") else [])
    elif op in {"eval", "tab", "wait_for_ref"}:
        return None  # not reproducible enough to encode
    if op in {"click", "type", "select", "toggle", "hover", "upload"} and "target" not in step:
        return None  # was a coordinate-free op on an unresolvable ref
    return step


# -------------------------------------------------------------------- storage


def _path(cfg: Config, name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("._") or "macro"
    return cfg.macros_dir() / f"{safe}.json"


def save(cfg: Config, name: str, steps: list[dict], *, goal: str = "",
         start_url: str = "") -> dict:
    steps = [step for step in steps if step]
    payload = {
        "name": name,
        "goal": goal,
        "start_url": start_url,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "steps": steps,
    }
    path = _path(cfg, name)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"name": name, "steps": len(steps), "path": str(path), "start_url": start_url}


def load(cfg: Config, name: str) -> dict:
    path = _path(cfg, name)
    if not path.exists():
        available = [item.stem for item in cfg.macros_dir().glob("*.json")]
        raise MacroError(f"No macro named {name!r}. Available: {available or 'none'}")
    return json.loads(path.read_text(encoding="utf-8"))


def listing(cfg: Config) -> list[dict]:
    out = []
    for path in sorted(cfg.macros_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({
            "name": data.get("name") or path.stem,
            "steps": len(data.get("steps") or []),
            "goal": data.get("goal") or "",
            "start_url": data.get("start_url") or "",
            "created": data.get("created") or "",
        })
    return out


def delete(cfg: Config, name: str) -> bool:
    path = _path(cfg, name)
    if not path.exists():
        return False
    path.unlink()
    return True


# ------------------------------------------------------------------- resolve


def _substitute(value, params: dict):
    if isinstance(value, str):
        return TEMPLATE.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)
    return value


@dataclass
class Match:
    element: object
    score: float


def _score(target: dict, element) -> float:
    if target.get("role") and element.role != target["role"]:
        # role families that behave identically for a click
        near = {("button", "link"), ("link", "button"), ("textbox", "searchbox"),
                ("searchbox", "textbox"), ("menuitem", "button"), ("button", "menuitem")}
        if (target["role"], element.role) not in near:
            return 0.0
        score = 0.55
    else:
        score = 0.6
    wanted, actual = _norm(target.get("name")), _norm(element.name)
    if not wanted:
        return score + 0.1
    if wanted == actual:
        score += 0.4
    elif wanted in actual or actual in wanted:
        score += 0.2
    else:
        wanted_tokens, actual_tokens = _tokens(wanted), _tokens(actual)
        if wanted_tokens and actual_tokens:
            overlap = len(wanted_tokens & actual_tokens) / len(wanted_tokens | actual_tokens)
            if overlap >= 0.5:
                score += 0.2 * overlap
            else:
                return 0.0
        else:
            return 0.0
    context = _norm(target.get("context"))
    if context and _norm(element.context):
        context_tokens, element_tokens = _tokens(context), _tokens(element.context)
        if context_tokens and element_tokens:
            overlap = len(context_tokens & element_tokens) / len(context_tokens | element_tokens)
            if overlap >= 0.4:
                score += CONTEXT_WEIGHT
    return score


def resolve(steps: list[dict], observation: Observation, params: dict | None = None,
            *, threshold: float = DEFAULT_THRESHOLD) -> tuple[list[dict], list[dict]]:
    """Map descriptor steps onto the live page. Raises rather than guessing."""
    params = params or {}
    ops: list[dict] = []
    report: list[dict] = []
    for position, step in enumerate(steps, 1):
        op = step.get("op")
        if not op:
            continue
        target = step.get("target")
        entry: dict = {"step": position, "op": op}
        if target:
            candidates = []
            for element in observation.elements:
                score = _score(target, element)
                if score > 0:
                    candidates.append(Match(element, round(score, 3)))
            candidates.sort(key=lambda match: match.score, reverse=True)
            if not candidates or candidates[0].score < threshold:
                best = [
                    {"ref": c.element.ref, "name": c.element.name, "role": c.element.role, "score": c.score}
                    for c in candidates[:5]
                ]
                raise MacroError(
                    f"step {position} ({op} on {target.get('role')} {target.get('name')!r}) "
                    f"did not match anything above {threshold}: best={best or 'none'}"
                )
            # Any near-tie is refused, however good the best match looks: two
            # "Select" buttons that both score 1.0 are exactly the silent
            # misclick this design exists to prevent. Context is what breaks
            # the tie, so a macro recorded on the right row scores higher.
            if len(candidates) > 1 and candidates[0].score - candidates[1].score < AMBIGUITY_MARGIN:
                raise MacroError(
                    f"step {position} is ambiguous between "
                    f"{candidates[0].element.ref} {candidates[0].element.name!r} and "
                    f"{candidates[1].element.ref} {candidates[1].element.name!r}. "
                    "Add distinguishing context to the macro or run the step manually."
                )
            chosen = candidates[0].element
            entry.update(ref=chosen.ref, name=chosen.name, score=candidates[0].score)
            op_body = {"op": op, "ref": chosen.ref}
        else:
            op_body = {"op": op}

        for field in ("text", "value", "url", "key"):
            if field in step:
                op_body[field] = _substitute(step[field], params)
        if step.get("submit"):
            op_body["submit"] = True
        if step.get("state") is not None:
            op_body["state"] = step["state"]
        if step.get("dir"):
            op_body["dir"] = step["dir"]
            op_body["amount"] = step.get("amount", 600)
        if step.get("ms"):
            op_body["ms"] = step["ms"]
        if step.get("paths"):
            op_body["paths"] = [_substitute(path, params) for path in step["paths"]]
        ops.append(op_body)
        report.append(entry)
    return ops, report
