# jev-ultrafast-mcp

[![CI](https://github.com/jiawei686/jev-ultrafast-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jiawei686/jev-ultrafast-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

**English** · [简体中文](README.zh-CN.md)

Fast, guarded browser control for agents, over MCP.

The calling agent is the policy. The server makes the page cheap to read and impossible to mis-aim
at: **no second model, no API keys, no screenshots in the loop.**

```
browser_open  →  element table  →  browser_act [refs]  →  browser_assert
```

Inspired by [`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast) and
TypeSafe's typed-question API. This is an independent project, not affiliated with browser-use or
TypeSafe. See [`docs/DESIGN.md`](docs/DESIGN.md) for what is different and why.

## Quick start

```bash
git clone https://github.com/jiawei686/jev-ultrafast-mcp.git
cd jev-ultrafast-mcp
python3 -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip install -e .

python scripts/install.py           # finds your MCP clients, writes their config
```

Restart the client, then ask it:

> Open example.com and tell me the headline.

That's the whole setup. `install.py` probes for WorkBuddy, Claude Code, Claude Desktop, Codex CLI,
Cursor, VS Code, Cline, Windsurf and Gemini CLI, and writes the dialect each one expects. It merges
into your existing config instead of overwriting it, copies anything it touches to `*.bak`, and
never invents a path.

```bash
python scripts/install.py --list              # what is installed, and the file each one reads
python scripts/install.py --print             # show the config it would write, change nothing
python scripts/install.py -c cursor,codex     # only these two
python scripts/install.py --headed            # keep a visible browser window
python scripts/install.py --allow-domains example.com,*.example.org
python scripts/install.py --uninstall         # take the entry back out
```

Requires Python ≥ 3.10 and a Chromium-family browser (Chrome, Chromium, Edge or Brave). Runtime
dependencies: `mcp`, `websockets`, `httpx`. No Playwright, no Selenium, no `browser-harness`.

## Connecting an agent

| Client | Config file `install.py` writes | After installing |
|---|---|---|
| **WorkBuddy** | `~/.workbuddy/mcp.json` | Connectors → Custom connectors → **Trust** |
| **Claude Code** | `~/.claude.json` (user scope) | or `claude mcp add --scope user …` |
| **Claude Desktop** | `~/Library/Application Support/Claude/claude_desktop_config.json` | quit the app fully and reopen |
| **Codex CLI** | `~/.codex/config.toml` | `codex mcp list` to confirm |
| **Cursor** | `~/.cursor/mcp.json` | reload the window |
| **VS Code (Copilot)** | `…/Code/User/mcp.json` | Agent mode only — not Ask/Edit |
| **Cline** | `…/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` | reload the window |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | reload the window |
| **Gemini CLI** | `~/.gemini/settings.json` | `gemini mcp list` to confirm |

<details>
<summary><b>Manual setup</b> — if you would rather not run the installer</summary>

Every client below needs the same three facts: an absolute interpreter path, the module, and one
environment variable. Substitute your own path for `/ABS/PATH`.

**WorkBuddy** — `~/.workbuddy/mcp.json`

```json
{
  "mcpServers": {
    "jev-ultrafast-mcp": {
      "command": "/ABS/PATH/jev-ultrafast-mcp/.venv/bin/python",
      "args": ["-m", "jev_ultrafast_mcp"],
      "env": { "JEVMCP_HEADLESS": "1" }
    }
  }
}
```

**Claude Code**

```bash
claude mcp add --scope user jev-ultrafast-mcp \
  --env JEVMCP_HEADLESS=1 \
  -- /ABS/PATH/jev-ultrafast-mcp/.venv/bin/python -m jev_ultrafast_mcp
```

Or write the same `mcpServers` object by hand: `~/.claude.json` for user scope, `.mcp.json` in a
project for team scope (committed to git).

**Codex CLI** — `~/.codex/config.toml`. Codex uses TOML, and the table is `mcp_servers`, not
`mcpServers`:

```toml
[mcp_servers.jev-ultrafast-mcp]
command = "/ABS/PATH/jev-ultrafast-mcp/.venv/bin/python"
args = ["-m", "jev_ultrafast_mcp"]
startup_timeout_sec = 20

[mcp_servers.jev-ultrafast-mcp.env]
JEVMCP_HEADLESS = "1"
```

The same entry works as `codex mcp add jev-ultrafast-mcp --env JEVMCP_HEADLESS=1 -- /ABS/PATH/…/python -m jev_ultrafast_mcp`.

**Cursor** — `~/.cursor/mcp.json` for every project, `.cursor/mcp.json` for one. Same `mcpServers`
object as WorkBuddy.

**VS Code (Copilot)** — `.vscode/mcp.json`, or Command Palette → *MCP: Open User Configuration* for
all workspaces. VS Code is the odd one out twice over: the key is `servers`, and every entry must
declare `"type": "stdio"` or it is silently skipped.

```json
{
  "servers": {
    "jev-ultrafast-mcp": {
      "type": "stdio",
      "command": "/ABS/PATH/jev-ultrafast-mcp/.venv/bin/python",
      "args": ["-m", "jev_ultrafast_mcp"],
      "env": { "JEVMCP_HEADLESS": "1" }
    }
  }
}
```

**Claude Desktop** — `claude_desktop_config.json` (`%APPDATA%\Claude\` on Windows), same
`mcpServers` object. Restart the app from the tray, not just the window.

</details>

The browser does not start until the first `browser_open`, and the tab it drives is a **background
tab it owns** — focus emulation keeps animations and menus running without stealing your window.

## What the agent actually reads

`browser_open` and `browser_observe` hand back an element table, not a DOM dump and not a
screenshot. Each row is a `ref`, a role code, flags, and the accessible name — plus the current
value for anything editable, and the options for anything selectable:

```
[obs#1] http://127.0.0.1:54409/fixture.html  "Ultrafast Fixture"  scroll=0/860  reachable=16/16
e1   lnk    Home
e2   lnk    About
e3   lnk    Open popup
e4   inp*   Where from? ▸ ""
e5   cmb*   Where to? ▸ ""
e6   cmb    Passengers ▸ 1 adult opts{1 adult=1 | 2 adults=2 | 3 adults=3 | 4 adults=4}
e7   chk·   Nonstop only
e8   btn    Search
e10  inp*   Password ▸ ""
e11  file    CV accept=.pdf,.txt
e12  btn    Delete account
```

Flags: `*` editable · `»` off-screen (the server scrolls it into view) · `⊘` covered by something
else · `▾` expanded · `✓`/`·` checked state. `reachable=16/19` means three controls exist but are
covered or off-screen right now.

Then `browser_act` takes a batch of ops aimed at those refs and reports **only what changed**:

```
[delta#2] http://127.0.0.1:54409/fixture.html  "Ultrafast Fixture"  reachable=16/16
~ e4   inp*   Where from? ▸ "Zurich"   (was "")
~ e7   chk✓   Nonstop only
  2 changed, 0 new, 0 gone
```

An action that accomplished nothing is the single most expensive thing in an agent loop, because
the model retries it. So it is spelled out in one line:

```
[delta#3] … 16 elements
  = no change (16 elements)
```

New rows appear with `+` and disappear with `-`. When two controls share a name, the row carries the
context that tells them apart:

```
+ e17  btn    Select  @Zurich → Anywhere Option 1 · 1 adult · nonstop Select
+ e18  btn    Select  @Zurich → Anywhere Option 2 · 1 adult · nonstop Select
```

And a ref that no longer points at anything is refused, with a reason instead of a wrong click:

```json
[{"op": "click", "ok": false, "ref": "e999", "error": "detached"}]
```

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

Batching and deltas are not cosmetic. In the bundled end-to-end run, 26 ops and their follow-up
observations cost **13.4 KB** of context, of which **12.6 KB was deltas and 0.8 KB full tables** — the
model re-reads only the part of the page that moved.

### The honest cost

The agent still spends tokens reading the element table, and a page that needs genuine visual
judgement — a captcha, a chart, a canvas app — is out of scope, because this server deliberately
never looks at pixels. For those, reach for a screenshot-and-vision agent instead, or use
`screenshot` here to capture evidence for a human rather than for the model.

## Tools

### `browser_open(url, session="default", hint="")`
Opens a URL in a new owned tab and returns the full element table.

### `browser_observe(session="default", mode="auto", include_text=True, include_json=False)`
Re-reads the page. `auto` emits a delta; `full` forces the whole table. `= no change` means the last
action did nothing — change strategy, do not retry.

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
| `tab` | `action`=`list\|new\|switch\|close`, `target_id`, `index`, `url` |
| `eval` | `js` — only when `JEVMCP_ALLOW_JS=1` |

```json
{"ops": [
  {"op": "type",   "ref": "e4", "text": "Zurich"},
  {"op": "select", "ref": "e6", "value": "3 adults"},
  {"op": "toggle", "ref": "e7"},
  {"op": "click",  "ref": "e8"}
]}
```

A failing op reports why: `occluded`, `detached`, `target_changed`, `page_changed`,
`needs_confirmation`, `blocked_by_policy`. Reach for `browser_observe`, not a retry.

For tabs, prefer `target_id` over `index`. Indexes are positional and get renumbered whenever the
tab list changes, so an index read one call ago can address a different tab.

### `browser_assert(checks, session="default")`
Deterministic checks — no model judgement about whether it worked.

```json
{"checks": [
  {"type": "url_matches",    "pattern": "*/checkout*"},
  {"type": "text_contains",  "text": "Order confirmed"},
  {"type": "element_exists", "role": "button", "name": "Continue"},
  {"type": "value_equals",   "ref": "e4", "value": "Zurich"},
  {"type": "count_at_least", "role": "link", "min": 3}
]}
```

### `browser_macro(action, session="default", name="", params={}, ...)`
`record_start` → drive the task → `record_stop` → `run`. Replay costs **no model calls**; steps are
re-resolved by role + accessible name, and replay refuses weak or ambiguous matches rather than
clicking the wrong thing.

### `browser_goal(goal, session="default", max_steps=20, verify=[...])`
Runs the whole loop server-side using TypeSafe speculative fan-out (one request per step). Needs
`TYPESAFE_API_KEY`; returns `verified: PASS/FAIL` when `verify` checks are supplied.

### `browser_tabs` · `browser_sessions` · `browser_close` · `browser_doctor`
Tab management (list / new / switch / close), session listing, teardown, and a self-check that
reports which browser was found and whether it is reachable.

## Configuration

All optional; the defaults are the point.

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

Two of these are worth setting before you point an agent at your own accounts:
`JEVMCP_ALLOW_DOMAINS` pins the browser to a set of hosts and refuses everything else, and a persistent
`JEVMCP_PROFILE_DIR` means you log in once by hand instead of teaching the model your password.

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
scripts/
  install.py       writes the right config for each MCP client on this machine
  smoke.py         end-to-end proof against a real browser
  mcp_check.py     drives the server over real stdio MCP
```

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke.py        # 51 checks, real browser
.venv/bin/python scripts/mcp_check.py    # 17 checks, real stdio MCP
```

All of it runs in CI on Python 3.10, 3.12 and 3.13 against headless Chrome. Read
[`CONTRIBUTING.md`](CONTRIBUTING.md) before changing how targets are resolved — that logic is the
whole point of the project.

## License

MIT — see [`LICENSE`](LICENSE).
