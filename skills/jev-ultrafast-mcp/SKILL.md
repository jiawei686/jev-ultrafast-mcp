---
name: jev-ultrafast-mcp
description: >
  Hand a browser task to the jev-ultrafast-mcp server instead of driving the page
  yourself. Use whenever a task involves a web page or a browser: filling in or
  submitting a form, signing in, a daily check-in, clicking through a flow,
  extracting something from a page, or proving a page reached a given state. Also
  use when the user names a URL together with an action, or says "打开这个网页",
  "帮我填一下", "签到", "在浏览器里点", "browser", "fill this form", "log in to",
  "check in on", "scrape", or "click through".
agent_created: true
---

# Browser work goes to `browser_goal`, in one call

A browser task is handed over **whole**. The loop runs server-side, driven by a
decision model, so the page never enters your context and the task costs one
turn instead of one per click.

```
browser_goal(
  goal="…",                                             # what "done" means, in words
  url="https://…",                                      # omit to continue on the current page
  verify=[{"type": "text_contains", "text": "…"}],      # how the page proves it worked
)
```

`url` + `goal` + `verify` is a complete handoff. **Do not** open the page and
walk it yourself first — that is the thing this server exists to replace.

## Always pass `verify`

`status:` is the model's own summary; `verify` is checked by code afterwards, and
when the two disagree the assertion wins. This is not a rare edge: the ordinary
shape of a successful goal is that the last action *removes* what it acted on —
click a check-in button and the button is gone — so the model, finding nothing
left to do, reports `BLOCKED` on a goal that in fact succeeded. Without `verify`
you cannot tell that apart from a real failure.

Pick checks that are visible on the page afterwards: `text_contains`,
`element_gone`, `url_contains`, `count_at_least`.

Pick a string that exists **only after** the action. A string that is also in the
navigation or the header (a menu label, the site name) will pass before anything
happened, and a string you half-remember will fail on a goal that worked. When
`verify` fails, re-read the element table and check your string first — a wrong
assertion is far more common than a broken engine.

`text_contains` matches the page's **prose**, not the element table. A combobox's
selected value, a button's label, a tab's name exist only in the table — asserting
on them with `text_contains` fails on a page that is exactly right. Assert on
things that appear in the text: a price, a result count, a heading. So "the ticket
type is now One way" is not a `text_contains` check; "SGD 133" is.

## Reading a page is not a task

`browser_open`, `browser_observe` and `browser_assert` are direct, deterministic,
free and need no key. Use them when the user only wants to know what a page says,
or when you need to see why a goal failed. Routing a look through the model
spends money to answer a question the element table already answers.

## When to fall back to the manual loop

Exactly two cases:

1. `browser_goal` answers `turbo_unavailable:` — no model key is configured. Then
   `browser_open` → read the element table → `browser_act` → `browser_assert`.
2. You only want to look (see above).

## Before handing over: is there a browser at all?

`browser_doctor` returning `connected: false` usually means **nothing is
listening on the CDP URL** — not that the server is broken. In `attach` mode the
server deliberately starts nothing (its own hint says "nothing is started for
you"), so `JEVMCP_CDP_URL=http://127.0.0.1:9222` can point at empty air.

Launch one yourself:

```
nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.jev-ultrafast-mcp/chrome-profile" \
  --no-first-run --no-default-browser-check about:blank &
```

- **Headed, never headless** — a headless browser cannot clear Cloudflare and will
  sit on "Just a moment…" forever.
- **Never the default profile** — Chrome 136+ refuses to open a debugging port on
  it. A dedicated `--user-data-dir` is what makes this legal.
- The login you need lives in that profile directory, so signing in once there
  makes every later handoff work.
- Do **not** type the user's password for them. If a goal sits behind a login, have
  the user sign in in that window; then hand the task over.

Two traps when supplying that browser:

- **A 404 from `/json/version` does not mean nothing is listening.** Chrome 144+'s
  `chrome://inspect/#remote-debugging` server is WebSocket-only and answers 404 to
  every `/json/*` path. The port and the browser WebSocket path are in
  `DevToolsActivePort` inside the browser's data directory, and the server reads
  them from there — so a browser started by that toggle (including the user's own
  Chrome, which is where their logins are) works fine. If the data directory is
  unusual, set `JEVMCP_ATTACH_PROFILE_DIR`. Chrome then asks the user to approve
  the new debugging client, so expect a dialog on first connect and a handshake
  that waits for it.
- **One port, one browser.** Two Chromes cannot share 9222: whichever binds IPv4
  answers, and if that is the wrong one you get 404s while the good instance sits
  on `[::1]`. If a launch seems to have silently failed, check
  `lsof -nP -iTCP:9222 -sTCP:LISTEN` for *two* rows before relaunching. Also note
  `open -na "Google Chrome" --args …` can drop the arguments and start the default
  profile — verify the profile from the process's own open files rather than
  trusting the command you typed.

## Reading a failure

Two signatures that look like engine bugs but are not:

- **`status: blocked`, `steps: 0`.** The model could not find anything to act on.
  Check, in order: (1) is your `verify` string actually on the page? (2) is the
  target **in the element table**? `observe` reports `omitted N low-priority` —
  anything not in the table is invisible to the model, and a target it cannot see
  can only produce `blocked`. Both of these are correct behaviour, not failures.
- **`turbo_unavailable: … TYPE_TEXT … needs TEXT_MODEL_API_KEY`.** The model *did*
  answer — it returned a plan that types into a field, and the **policy layer**
  refused it. The optional `TEXT_MODEL_API_KEY` (DeepSeek by default, via
  `TEXT_MODEL_BASE_URL` / `TEXT_MODEL`) only affects writing a value into an
  input; a click-only task needs no key. Do not report this as "the model is
  unavailable". Workaround while no key is set: **put the parameters in the URL**
  and let the model only wait and read. Many sites accept them, including
  natural-language queries — a flight search becomes
  `…/travel/flights?q=One way flights from SIN to PQC on 2026-09-26`, and then no
  typing is needed at all. Adding `hl=en` (or the site's locale parameter) also
  makes the page easier to read and to assert on.

Judge a handoff by `verified: PASS` **and** the final URL actually having changed —
not by `status`.

## Operational gotchas that waste a run

- **The page must be in a foreground tab.** A backgrounded tab is throttled by
  Chrome, and a bot check (Cloudflare's "Just a moment…") then never clears — it
  will sit there for the full timeout while the Ray ID changes every attempt.
  Set `JEVMCP_FOREGROUND=1` in the server's env.
- **The server reads its config once, at import.** After editing the MCP config
  you must restart the client (and in WorkBuddy, click **Trust** again if you
  added or removed an env *key name* — values alone do not change the approval).
- **A long-lived server does not reconnect.** If the machine sleeps, its browser
  socket dies and every browser tool fails with
  `transport closed … keepalive ping timeout` until the server restarts. A fresh
  process attaching to the same browser works fine, which tells you the browser is
  healthy and the server is stale.
- **Login state lives in the browser profile**, not in the server. A task behind
  a login only works while that profile is signed in.

## Reporting

The run reports its own bill (`turbo: N decisions · N tokens · …`). Quote the
`verified: PASS/FAIL` line and the assertion details rather than the model's
summary — and if `verify` failed, say so plainly instead of reporting success.
