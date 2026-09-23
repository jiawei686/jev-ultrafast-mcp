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

## Recurring work: record it once, then replay for free

`browser_macro` records the path a goal discovered and replays it **with no model
calls**. Replay re-resolves each step by role + name against a fresh observation
and refuses to act when the best match is weak or ambiguous (`threshold`, default
`0.7`), so it fails loudly instead of clicking the wrong thing.

A chore that repeats *identically* is the wrong shape for `browser_goal` forever:
pay the model once, `record_stop` it, then `run` the macro on later days. `params`
fills `{{placeholders}}` in text and URLs, which is what lets one macro serve many
days or many inputs. `inspect` shows the recorded steps before you trust them.

**Replay only fits a flow whose steps are the same every time.** Judge a candidate
by whether the *labels it clicks* are stable, not by whether the task is
repetitive. A daily quiz whose question and options are new every morning is the
wrong shape and belongs on `browser_goal` — the resolver refuses rather than
guesses, so a macro there fails safely and uselessly. A check-in form whose buttons
are always in the same place replays; a quiz whose answers change does not.

## Before handing over: is there a browser at all?

`browser_doctor` reports `connection` — `idle`, `attached` or `dropped`. **`idle`
is the normal state, not a fault**: the server connects lazily, so a browser that
has not been needed yet reads `connected: false, connection: idle`. Only `dropped`
means a socket existed and died. Read `connected: false` as "nothing is listening
on the CDP URL" only when something should be listening: in `attach` mode the
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
  unusual, set `JEVMCP_ATTACH_PROFILE_DIR`.
- **One port, one browser.** Two Chromes cannot share 9222: whichever binds IPv4
  answers, and if that is the wrong one you get 404s while the good instance sits
  on `[::1]`. If a launch seems to have silently failed, check
  `lsof -nP -iTCP:9222 -sTCP:LISTEN` for *two* rows before relaunching. Also note
  `open -na "Google Chrome" --args …` can drop the arguments and start the default
  profile — verify the profile from the process's own open files rather than
  trusting the command you typed.

## `attach` costs a click per connection; `launch` costs one login

On Chrome 144+ the `chrome://inspect/#remote-debugging` server shows an
**"Allow remote debugging?" dialog for every incoming connection** — not once per
launch. Attach mode therefore asks the user to approve *every* connection, and a
reconnect **is** a connection, so anything recurring pays a click each time.

`launch` mode with a non-default `--user-data-dir` needs neither the toggle nor
any dialog (measured: launch, connect, `Browser.getVersion` in 4.6s, zero
prompts). The price is that a fresh profile is signed out once. For a recurring
job that is the better trade — sign in in that profile a single time, and every
later run is prompt-free.

Attach to the user's own Chrome only when their existing logins are the point
*and* the run is a one-off.

## Reading a failure

Two signatures that look like engine bugs but are not:

- **`status: blocked`, `steps: 0`.** The model could not find anything to act on.
  Check, in order: (1) is your `verify` string actually on the page? (2) is the
  target **in the element table**? `observe` reports `omitted N low-priority` —
  anything not in the table is invisible to the model, and a target it cannot see
  can only produce `blocked`. Both of these are correct behaviour, not failures.
  A large `omitted` count used to make this non-deterministic — the ranking put
  viewport position above role, so the same goal could reach its target once and
  report `blocked` at step 0 the next time. That is fixed (role now outranks
  position, in both `observer.js` and `policy.reachable_first`), so a repeated
  `blocked` at step 0 is a statement about your goal or your `verify`, not about
  the clock.
  The clock is no longer a candidate at all: after opening a `url` the server
  waits for the page to **stop fetching** as well as for its element table to
  stop changing before it asks the model anything (`JEVMCP_SETTLE_TIMEOUT`, 4s
  by default). An app that has not fetched its bundle yet is a shell, and a shell
  holds perfectly still — so "the table stopped changing" alone used to settle on
  a page with no controls on it and hand back `blocked` at step 0 twice on a page
  that rendered a second later. If a `blocked` at step 0 still survives that,
  raise `JEVMCP_SETTLE_TIMEOUT` before you rewrite the goal.
- **`status: stopped: no progress`.** The page stopped changing for the limit
  `browser.py` defines, so the run ended rather than spending the rest of
  `max_steps` on `WAIT`s. This is the usual shape of a goal that has *already
  succeeded* — the last action removed the thing it acted on — so read `verified`
  before you read `status`: a passing assertion upgrades this to `done`. If
  `verified` is absent or failing, the goal genuinely stalled and needs a
  different goal string, not a retry.
- **`turbo_unavailable: … TYPE_TEXT …`.** The model *did* answer — it returned a plan
  that types into a field, and the **policy layer** refused it. The optional text
  helper only affects writing a value into an input; a click-only task needs no key.
  Do not report this as "the model is unavailable".

  Which dead end it is decides the fix, so read the sentence rather than the code:
  - **Jev's own API** (`JEV_PROVIDER=typesafe`, the default) answers typed questions
    and never writes prose, so it has no chat route for the helper to inherit. Either
    give the helper its own chat provider (`TEXT_MODEL_API_KEY`, plus `TEXT_MODEL`),
    or move the decision model to `JEV_PROVIDER=openrouter` and name a chat model
    there with `TEXT_MODEL`.
  - **OpenRouter** (`JEV_PROVIDER=openrouter`) does serve chat, so with
    `OPENROUTER_API_KEY` set the helper inherits the key *and* the URL, and the only
    thing still missing is `TEXT_MODEL` — `deepseek-chat` is not an OpenRouter slug.
    A message asking for `TEXT_MODEL_API_KEY` while on OpenRouter therefore means one
    of two things: no key at all, or `TEXT_MODEL_BASE_URL` set without its key, which
    is read as the explicit route and will not borrow the decision model's key.
  - Either way **the key and the URL come from the same provider.** Borrowing a key
    across providers earns a 401 that names the company you did not call, which is a
    worse failure than the refusal it replaced. `browser_doctor` reports the resolved
    provider and whether the helper has a route.

  Workaround while no route is configured: **put the parameters in the URL**
  and let the model only wait and read. Many sites accept them, including
  natural-language queries — a flight search becomes
  `…/travel/flights?q=One way flights from SIN to PQC on 2026-09-26`, and then no
  typing is needed at all. Adding `hl=en` (or the site's locale parameter) also
  makes the page easier to read and to assert on.

Judge a handoff by `verified: PASS` **and** the final URL actually having changed —
not by `status`.

**`status: unconfirmed: …` means the model reported `done` and `verify` did not confirm it.** The
assertion wins, so treat it as unfinished — but check your own string first, because a wrong
`verify` produces the same line. Measured on a real daily check-in: the model clicked the submit
button, reported `done` twice, and moved no points either time. Note also that `done` can arrive at
`steps: 0` with the element you named not even on the page — a model that finds its target absent
says `done` about as readily as it says `blocked`, which is the whole reason `verify` is not
optional.

## Operational gotchas that waste a run

- **The page must be in a foreground tab.** A backgrounded tab is throttled by
  Chrome, and a bot check (Cloudflare's "Just a moment…") then never clears — it
  will sit there for the full timeout while the Ray ID changes every attempt.
  Set `JEVMCP_FOREGROUND=1` in the server's env.
- **The server reads its config once, at import.** After editing the MCP config
  you must restart the client (and in WorkBuddy, click **Trust** again if you
  added or removed an env *key name* — values alone do not change the approval).
- **A dropped socket is self-healing, and no longer needs a restart.** The manager
  checks liveness before each call and, on a dead socket, drops it *together with
  the sessions bound to it* (a target id exists only on the socket that minted it)
  and opens a fresh one. `doctor` shows `connection: dropped` for that case instead
  of a stale `connected: true`. If `transport closed … keepalive ping timeout`
  nevertheless stays permanent, the server predates that fix — restart it.
- **Login state lives in the browser profile**, not in the server. A task behind
  a login only works while that profile is signed in.

## Reporting

The run reports its own bill (`turbo: N decisions · N tokens · …`). Quote the
`verified: PASS/FAIL` line and the assertion details rather than the model's
summary — and if `verify` failed, say so plainly instead of reporting success.
