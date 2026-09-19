# jev-ultrafast-mcp

[![CI](https://github.com/jiawei686/jev-ultrafast-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jiawei686/jev-ultrafast-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Fast, guarded browser control for agents, over MCP.

The calling agent is the policy. The server makes the page cheap to read and impossible to
mis-aim at: **no second model, no API keys, no screenshots in the loop.**

```
browser_open  → indexed element table → browser_act [refs] → browser_assert
```

Inspired by [`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast) and
TypeSafe's typed-question API. This is an independent project, not affiliated with browser-use or
TypeSafe. See [`docs/DESIGN.md`](docs/DESIGN.md) for what is different and why.

## Why another browser MCP?

Most browser MCP servers expose CDP primitives — `click_at_xy`, a CSS selector, `evaluate`. That is
maximum flexibility and minimum safety: every step depends on the model inventing a selector or a
coordinate, and a wrong one fails silently or, worse, succeeds on the wrong element.

`jev-ultrafast-mcp` takes the opposite trade. The model only ever says *which* element by `ref` and
*what* to do; resolving that ref to a real click is the server's problem, and the server refuses
rather than guesses.

| | primitives-based browser MCP | **jev-ultrafast-mcp** |
|---|---|---|
| How the agent aims | writes a selector / coordinate / JS | picks a `ref` from an element table |
| Extra model calls | none | **none — your agent is the policy** |
| API keys required | none | **none** |
| Ref lifetime | n/a (agent re-invents each step) | **stable across observations** |
| Re-reading the page | full dump every time | **delta** — `+` added, `~` changed, `-` removed, `= no change` |
| Round trips | one per action | **batched — many ops per call** |
| Ambiguous target | agent guesses | **server refuses with a reason** |
| Shadow DOM / iframes | usually unsupported | **traversed, with frame-offset-aware scrolling** |
| Repeating a flow | re-runs the model | **macro replay at zero model cost** |
| Knowing it worked | the model eyeballs the page | **deterministic `browser_assert`** |
| Destructive clicks | whatever the model decides | **`needs_confirmation`, domain envelope, secret redaction** |

The honest cost: the agent still spends tokens reading the element table, and a page that needs
genuine visual judgement (a captcha, a chart) is out of scope — this server deliberately never looks
at pixels. For those, reach for a screenshot-and-vision agent instead.

## Install

```bash
git clone git@github.com:jiawei686/jev-ultrafast-mcp.git
cd jev-ultrafast-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

Requires Python ≥ 3.10 and a Chromium-family browser. Runtime dependencies: `mcp`, `websockets`,
`httpx`. No `playwright`, no `browser-harness`, no Selenium.

## Wire it into an MCP client

Add to `~/.workbuddy/mcp.json` (or any MCP client config):

```json
{
  "mcpServers": {
    "jev-ultrafast-mcp": {
      "command": "/ABSOLUTE/PATH/jev-ultrafast-mcp/.venv/bin/python",
      "args": ["-m", "jev_ultrafast_mcp"],
      "cwd": "/ABSOLUTE/PATH/jev-ultrafast-mcp",
      "env": {
        "JEVMCP_HEADLESS": "1"
      }
    }
  }
}
```

The browser does not start until the first `browser_open`, and the tab it drives is a **background
tab it owns** — focus emulation keeps animations and menus running without stealing your window.

Type the loop out for the agent once:

> Open example.com, find the sign-in form, fill it and submit. Then prove you got in.

## Try it without an agent

```bash
.venv/bin/python scripts/smoke.py            # headless, 51 checks
.venv/bin/python scripts/smoke.py --headed   # watch it drive
```

This launches Chrome, serves `tests/fixture.html`, and drives the real code paths: batch execution,
autocomplete, a covering modal, shadow DOM, a same-origin iframe, a file upload, a password field, a
destructive-click guard, stale refs, macro record/replay, tab handoff and screenshots.

```
1. Observation — one atomic read, indexed refs
  [ok  ] element table is not empty  — 16 elements
  [ok  ] shadow DOM element indexed  — Shadow action -> e14
...
5. Occlusion — precomputed, not discovered by a failed click
  [ok  ] covered control flagged before any click  — e8 occluded=True
  [ok  ] click on a covered control is refused with a reason  — occluded
...
  51/51 checks passed
```

## Tools

### `browser_open(url, session="default", hint="")`
Opens a URL in a new owned tab and returns the full element table.

### `browser_observe(session="default", mode="auto", include_text=True, include_json=False)`
Re-reads the page. `auto` emits a delta when the page is unchanged enough to allow it; `full` forces
the whole table. `= no change` means the last action did nothing — change strategy, do not retry.

### `browser_act(ops, session="default", dry_run=False, stop_on_error=True, observe_after=True)`
Executes ops in order in **one round trip**, then returns a delta.

| op | fields |
|---|---|
| `click` | `ref` |
| `type` | `ref`, `text`, `clear`=true, `submit`=false, `slow` |
| `select` | `ref`, `value` (option value or label) |
| `toggle` | `ref`, `state` (omit to flip) |
| `hover` / `upload` | `ref` / `ref`, `path` |
| `keys` | `key` (`"Enter"`, `"Meta+A"`, `"ArrowDown"`) |
| `scroll` | `dir`, `amount`, `ref` |
| `nav` / `back` / `forward` / `reload` | `url` (for `nav`) |
| `wait` / `wait_for_ref` / `wait_for_text` / `wait_for_load` | `ms` / `ref`,`timeout_ms` / `text` / `timeout_ms` |
| `screenshot` | `path`, `full`, `format` |
| `tab` | `action`=`list\|new\|switch\|close`, `index`, `url` |
| `eval` | `js` — only when `JEVMCP_ALLOW_JS=1` |

```json
{"ops": [
  {"op": "type",   "ref": "e7", "text": "Zurich"},
  {"op": "select", "ref": "e8", "value": "3 adults"},
  {"op": "toggle", "ref": "e9"},
  {"op": "click",  "ref": "e12"}
]}
```

A failing op reports why: `occluded`, `detached`, `target_changed`, `page_changed`,
`needs_confirmation`, `blocked_by_policy`. Reach for `browser_observe`, not a retry.

### `browser_assert(checks, session="default")`
```json
{"checks": [
  {"type": "url_matches",    "pattern": "*/checkout*"},
  {"type": "text_contains",  "text": "Order confirmed"},
  {"type": "element_exists", "role": "button", "name": "Continue"},
  {"type": "value_equals",   "ref": "e7", "value": "Zurich"},
  {"type": "count_at_least", "role": "link", "min": 3}
]}
```

### `browser_macro(action, session="default", name="", params={}, ...)`
`record_start` → drive the task → `record_stop` → `run`. Replay costs **no model calls**; steps are
re-resolved by role + accessible name and replay refuses weak or ambiguous matches.

### `browser_goal(goal, session="default", max_steps=20, verify=[...])`
Runs the whole loop server-side using TypeSafe speculative fan-out (one request per step). Needs
`TYPESAFE_API_KEY`; returns `verified: PASS/FAIL` when `verify` checks are supplied.

### `browser_tabs` · `browser_sessions` · `browser_close` · `browser_doctor`

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `JEVMCP_CHROME` | auto-detected | Chrome/Chromium/Edge/Brave executable |
| `JEVMCP_MODE` | `launch` | `launch` a browser, or `attach` to a running CDP endpoint |
| `JEVMCP_CDP_URL` | — | `http://127.0.0.1:9222` when `mode=attach` |
| `JEVMCP_HEADLESS` | `1` | `0` for a visible window |
| `JEVMCP_FOREGROUND` | `0` | `1` activates the owned tab |
| `JEVMCP_SANDBOX` | `auto` | `auto` retries with `--no-sandbox` if the browser aborts on startup |
| `JEVMCP_PROFILE_DIR` | `~/.jev-ultrafast-mcp/chrome-profile` | persistent profile — log in once, stay logged in |
| `JEVMCP_ALLOW_DOMAINS` | *(all)* | comma-separated; navigation elsewhere is refused |
| `JEVMCP_DENY_DOMAINS` | *(none)* | comma-separated blocklist |
| `JEVMCP_CONFIRM_PATTERNS` | pay / delete / unsubscribe … | clicks matching these need `"confirm": true` |
| `JEVMCP_ALLOW_JS` | `0` | enables `eval` and `js` assertions |
| `JEVMCP_ALLOW_UPLOADS` | `1` | gates the `upload` op |
| `JEVMCP_MAX_ACTIONS` | `250` | element-table cap, applied by usefulness |
| `JEVMCP_MAX_TEXT` | `6000` | visible-text cap per observation |
| `JEVMCP_STATE_DIR` | `~/.jev-ultrafast-mcp` | profile, macros and screenshots |
| `TYPESAFE_API_KEY` | — | optional; enables `browser_goal` |
| `TEXT_MODEL_API_KEY` | — | optional; only for typing in `browser_goal` mode |

## Layout

```
jev_ultrafast_mcp/
  js/observer.js   in-page observer: stable refs, shadow/frame traversal, verify/resolve
  cdp.py           synchronous CDP client + Chrome launch (no wrapper library)
  browser.py       sessions, guarded execution, op dispatch, macro recording
  observe.py       element model, compact renderer, delta computation
  macros.py        semantic descriptors, scored re-resolution, storage
  assertions.py    deterministic checks
  policy.py        optional TypeSafe turbo policy (speculative fan-out)
  safety.py        domain envelope, redaction, confirmation rules
  config.py        environment-driven configuration
  server.py        the MCP surface
```

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke.py        # 51 checks, real browser
.venv/bin/python scripts/mcp_check.py    # 17 checks, real stdio MCP
```

All four run in CI on Python 3.10, 3.12 and 3.13 against headless Chrome. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) before changing how targets are resolved — that logic is the
whole point of the project.

## License

MIT — see [`LICENSE`](LICENSE).
