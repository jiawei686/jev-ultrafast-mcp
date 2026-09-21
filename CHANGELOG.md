# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The macro resolver the extension replays with — ported, and held to Python.** A replay needs a
  resolver and the server already has one, so `chrome-extension/lib/macro.js` is a port of
  `macros.py` rather than a second opinion about it: the same scoring rules, the same three
  thresholds, the same two refusal sentences. The alternative is the failure this project keeps
  running into — two implementations that agree until the day they do not, with no model in the loop
  to notice, because the whole point of a replay is that nothing is watching.
  `test/macro-parity.mjs` compares the port against 46 fixtures produced by the real
  `macros.resolve`, and a refusal counts as a result: raising is what leaves the page untouched, so
  "did it raise, and with what sentence" is part of the contract. Four of the fixtures come off a
  live page — `test/live-observation.json`, two reads through the real observer, one either side of
  a hover — because the wire format has shape a hand-written action list does not: `context` appears
  only on labels that repeat, `hoverable` only on the trigger, and menu items sort into the middle
  of the list, where they entered the DOM (`e8`, `e9`, then `e2`). Building it also settled what the
  scoring rules *cannot* do, and the fixtures now say it rather than a comment: a CJK label
  tokenises to nothing, because `_tokens` splits on `[^a-z0-9]+`, so a repeated Chinese label cannot
  be disambiguated at all; and the context bonus is all-or-nothing at 0.4, so `Post C Check in`
  against `Post D Check in` fails for the same reason `甲帖 签到` against `乙帖 签到` does — only a
  context whose tokens do not overlap at all (帖子 A 打卡 against 帖子 B 打卡) breaks the tie. Both
  are limits of `macros.py` rather than of the port, so fixing either means changing both in one
  commit, and the fixtures fail if they are changed apart. 182 tests, 6 of them here. The port was
  checked by mutation rather than by the fixtures passing — 30 mutations, all 30 caught, plus one
  deliberate survivor documenting that `toFixed(3)` and Python's `round` cannot disagree on any
  input this scorer produces — which matters because the fixtures were green long before they were
  worth anything: the first mutation run passed against a fixture file that had not been
  regenerated.
- **The extension can now replay a macro, with no model in the loop.** The other half of a replay is
  the half that clicks, and `chrome-extension/lib/session.js` is a port of `browser.py`'s `Session`:
  the same op dispatcher, the same three refusal rules, the same tables that turn `ctrl+shift+k` into
  a virtual key code. `lib/report.js` ports `server.py`'s `_render_act` and the replay header so a
  report reads the same wherever it was produced, `lib/store.js` keeps macros in
  `chrome.storage.local` under the server's own `{{placeholder}}` rules, and `background.js` is the
  service worker — the only file in the extension that calls the debugger API. The popup gained a
  Replay panel: pick a macro, fill in the placeholders it asks for, press Run, and the table above
  refreshes as a delta against where you started.
  It takes the `debugger` permission, and that is the point rather than a shortcut: `element.click()`
  and `dispatchEvent(new MouseEvent(...))` produce `isTrusted: false` events a site is entitled to
  ignore, and the observer hands back *coordinates* precisely because the intended consumer
  dispatches input at them. `Input.dispatchMouseEvent` is the only in-extension way to produce
  trusted input. It is held for the length of a run and released in a `finally`, including on
  failure, so Chrome's banner is bounded by the replay rather than by how long the popup is open —
  and `tests/test_extension.py` fails if a second file starts *calling* the API, which is deliberately
  not the same test as one that greps for the word: `lib/session.js` opens with a paragraph naming it,
  and a substring search read that explanation as a second holder.
  Four ops behave differently from the server, pinned as fixtures rather than left for a reader to
  notice: `scroll` scrolls the viewport centre of the user's tab instead of one sized by a config
  file, and `upload`, `tab` and `eval` are refused outright, because an extension cannot read a path
  off the disk, has no business reaching a tab it was not pointed at, and cannot hold a page
  evaluated by a script it cannot inspect. The three rules that keep an unattended replay away from
  password fields and "Buy now" are *not* divergences and are compared in full, refusal sentence
  included — a port that got one of those subtly wrong would not report a problem, it would click and
  look exactly like success. `test/act-parity.mjs` runs 32 operations through both dispatchers and
  compares the step report *and* the CDP commands each side issued, because a dispatcher that ignores
  an argument still reports `ok`; 214 checks, and the port was mutated rather than merely observed —
  19 mutations, the 3 that escaped were all real gaps and are now closed, one of them a host match
  that let `notexample.com` pass an `example.com` allow list.
  `scripts/extension_check.py` gained a sixth section for the two things fixtures cannot reach: it
  records a macro with the server's own recorder, replays it in a real Chrome, calls the real
  `browser_macro` tool on the same macro, and compares the two replies character for character —
  masking only the per-step stopwatch, because the two replays are two runs. That section found a
  genuine bug on its first green-adjacent run: the extension printed a resolve score as `(1)` where the
  tool prints `(1.0)`. A score is a float in Python however integral it looks, JSON keeps no trace of
  that, and nothing in the repo could see it — every score the fixtures happened to carry was
  non-integral, where the two agree — so it surfaced only because a real replay of a macro that matched
  *perfectly* printed both. Fixed with a `pythonFloat` primitive, and pinned twice: a `float` fixture
  family holding it to Python's own `str(round(v, 3))` over every score the matcher can return, and an
  assertion that the header reaches for it. 197 tests, 15 of them here — four new ones in
  `tests/test_extension.py` for who may call the debugger, and `tests/test_act_port.py` for the
  execution layer, which also pins two genuine `browser.py` oddities rather than quietly improving on
  them: `scroll` documents a `ref` it never reads, because `scroll` is not in the set that reads one,
  so a `scroll` at a ref scrolls the viewport centre instead and reports `ok`; and the two report
  writers disagree on their default `max_text` — `Observation.render` defaults to 4000 while
  `readState` passes 6000 — so the port has to carry both numbers.
- **Both READMEs now say how to make the handoff actually arrive.** Pointing a client at the server
  is half of it; an agent that never hears the rule drives the page itself, one call per click. The
  new section under *Connecting an agent* names the measured failure (WorkBuddy ships a server's
  tools and drops its `instructions`), gives the two-line skill install that closes it, and — more
  useful than a claim — says how to tell it took: a `browser_goal` call with a `url` inside it means
  the handoff is live. The tool table now leads with handoffs and demotes reading to "a look is not a
  task", which is what `instructions` and the skill already said.
- **Search metadata brought in line with the pitch.** `server.json`'s description — the registry caps
  that field at 100 characters, which `tests/test_docs.py` asserts — now reads "Hand a whole browser
  task off in one call: a server-side decision model drives the page." `pyproject.toml` gained a
  `PyPI` project URL so the package page and the repository link to each other.
- **`skills/jev-ultrafast-mcp/SKILL.md`** — the handoff rule as a skill, for clients that do not
  surface a server's `instructions` (WorkBuddy does not; measured under `### Changed` below). It
  carries the one-call signature, why `verify` is mandatory rather than tidy, the two cases that
  justify the manual loop, and the operational traps that waste a run: a backgrounded tab never
  clears a bot check, the server reads its config once at import, and a machine that sleeps kills its
  browser socket for good. It also says what `attach` mode does not do — start a browser — with the
  headed, non-default-profile launch that supplies one, and how to read the two failures that are
  not failures: `blocked` with 0 steps means the model could not see the target, so suspect your
  `verify` string and the element table's `omitted N` before suspecting the engine, while a
  `TYPE_TEXT needs TEXT_MODEL_API_KEY` refusal means the model answered and the policy layer
  declined, which only ever affects writing a value into an input.
- **One-command client setup** — `scripts/install.py` detects the MCP clients on the machine
  (WorkBuddy, Claude Code, Claude Desktop, Codex CLI, Cursor, VS Code, Cline, Windsurf, Gemini CLI)
  and writes the dialect each one expects. Merges rather than overwrites, backs up to `*.bak`, and
  supports `--list`, `--print`, `--uninstall`. Stdlib only, so it runs before the dependencies exist.
- **`scripts/live_check.py`** — the same end-to-end drive, but against real websites (Bing,
  DuckDuckGo) over real stdio MCP: it types into a real search box, submits, reads 40-odd result
  links, asserts on the live URL, records a macro and replays it with a different `{{query}}`.
  Needs the network, so it is deliberately not in CI; unreachable sites report as *skipped* and the
  summary says so, so a fully-skipped run cannot be mistaken for a passing one.
- **Chinese README** — [`README.zh-CN.md`](README.zh-CN.md), switchable from the English one.
- **The bill, in both READMEs** — `assets/openrouter-spend.png`. "4 decisions, 14,626 tokens" is the
  one claim in the project a reader cannot check by reading the code, so the panel it came from
  travels with it: a cent for the whole exploration, nothing at all for the replay after it.
- Both READMEs now open with what a session actually looks like (a real element table, a real delta,
  a real `= no change` line, and a verbatim search run), then a plain-language "what you can ask it
  to do" / "what it cannot do" / FAQ, before the technical reference.
- `JEVMCP_SETTLE_TIMEOUT` and `JEVMCP_SETTLE_POLL_MS` — how long to wait for a late-rendering page,
  and how often to re-read while waiting.
- **`scripts/turbo_check.py`** — runs turbo mode end to end: it serves `tests/fixture.html`, lets the
  decision model drive it through a goal (set a dropdown, tick a checkbox, submit), and verifies the
  page the model left behind with code rather than trusting the model's claim of success. Every other
  check stops short of `browser_goal` — `smoke.py` drives the browser directly, `mcp_check.py` never
  enters the loop, and the unit tests fake the provider — which left the one path that spends money as
  the one path nothing ran. Deliberately not in CI: without a key it prints `skipped` and exits 0, so
  the exit code and the word agree.
- **`scripts/checkin.py` and `examples/checkin.html`** — a daily check-in, which is what a macro is
  actually for: the same two or three clicks every day, on a page whose shape barely moves. It runs
  three stages, cheapest first — read the page and stop if today is already done; replay the saved
  macro (zero model calls, and no key at all); and only then hand the goal to the decision model,
  which records what it did so the replay stage takes over tomorrow. Only the first run ever costs
  anything. The demo page is honest about the thing being automated: the button is gone once clicked.
- `scripts/smoke.py`'s fixture server takes a `port`, because a page's origin includes its port. A
  demo that came up on a different port every run was a different site as far as the browser was
  concerned: empty `localStorage`, no memory of the previous run, so "already checked in today" could
  not be demonstrated at all on a loopback address.
- **`browser_goal` reports what it cost** — `turbo: 4 decisions · 14,626 tokens · 1.8s model + 1.1s
  page · 3.3s wall`. The case for handing a flow off is that the caller spends one turn instead of
  one per action, and that case is only checkable if the server says what it spent. It also splits
  the wall time, which tells you whether the next optimisation belongs in the prompt or in the page.
  Absent when no decision was ever made, so a refusal does not print a budget implying it ran.

- **`HOVER` is in the vocabulary, so a hover-only menu is reachable.** The decision
  model could only click, type, select, toggle, scroll and wait — so a header trigger
  that opens its menu on hover (1point3acres' 「今日任务」) was unreachable: its items
  are not in the DOM until a pointer rests on the trigger, the model clicked the
  trigger instead, the menu toggled, and the goal looped to `max_steps`. Three layers
  now agree, and the executor already knew how: the observer reports `hoverable` from
  `aria-haspopup` / `aria-expanded` — never from a Tailwind `hover:` class, which is a
  style, not a signal — `Element.target_kinds()` **appends** `HOVER` beside `CLICK` so
  a trigger keeps both, and `OPERATION_LABELS` / `OPERATION_TO_ACT` carry `HOVER` →
  `hover`. Triggers render with a `⋮` and the observation header counts them, and each
  one's criteria say `opens_on: hover, not click`. Measured on a real page (W3C's ARIA
  menubar example), cold start with empty history: `HOVER` chosen at **p=0.98**, where
  the same page previously took eight consecutive `CLICK`s on one trigger.
- **`browser_doctor` separates "never connected" from "the socket died."** It gained a
  `connection` field — `attached` / `dropped` / `idle` — and `connected` now means the
  socket we hold is *usable* rather than merely *present*. `dropped` also gets its own
  hint, because the existing attach-mode advice ("point `JEVMCP_CDP_URL` at a browser
  exposing CDP") would send the caller off to fix a config that was never wrong.

### Fixed

- **A disabled control was invisible to the model, which is not the same as being unusable.** The
  observer dropped every disabled candidate while *collecting*, before ranking, so an element that
  never entered the table could not be shown however the offers were computed. On the daily-question
  page the submit button 「提交答案」 was in the page text and absent from the element table: the model
  could select an option and then have nothing on the page it was allowed to press, and reported the
  form as having no way to submit it. It is the ordinary shape of a form — a button that only enables
  once something is chosen — so the fix is to keep it and mark it `⊗`, not to drop it.
  Everything downstream was already right, which is what made this a visibility bug rather than an act
  bug: `resolve` refused a disabled element with `reason: 'disabled'`, and the guard-reason vocabulary
  already classified that as terminal rather than stale, so keeping one costs a clear refusal instead
  of a wrong click. What changed is the collection filter, the flag on the built element, and the three
  places the flag has to be honoured: `_operation_heads` leaves disabled elements out of the offered
  targets exactly as it already did for occluded ones — a target that cannot be acted on buys a step
  that cannot execute — `reachable` no longer counts them, and they rank in a tier of their own below
  every actionable element, so on a page that fills the table cap they are the first to go and the
  table a model sees is never narrower than it was before. `disabled` is in `signature()` as well, or
  the transition renders as an unchanged line: the model would never be told that the button it could
  not press is now pressable, and the goal would end with the form filled in and nothing submitted.
  Measured on a fixture of twelve candidate shapes rather than argued: 3 of 12 reached the table
  before, 6 of 12 after, and the five genuinely invisible ones — zero-size, `visibility: hidden`,
  `opacity: 0`, behind `aria-hidden`, and a cursor-styled `div` with no role — are still dropped. The
  four new tests were checked by mutation, each failing when its own fix is reverted.
  Adding the flag to the legend turned up a second stale sentence: `docs/DESIGN.md` still described
  the ranking as "in-viewport first, then interactive controls", which is the order it had *before*
  role was moved above position. The paragraph now says what the code does and why position is last,
  because a design note that argues for the arrangement the code deliberately reversed is worse than
  no note at all.
- **The extension rendered an element differently from the server, and no fixture had ever looked.**
  `render.js` had no `FLAG_HOVER`, never mapped `hoverable`, and never emitted the header segment that
  counts menu triggers — so the popup showed a menu trigger without the `⋮` the server puts on it, and
  without the `(N ⋮ = menu trigger, hover before choosing)` line that says what the glyph means. It
  survived because `hoverable` was the one flag the fixture set never exercised, so the parity harness
  had nothing to disagree with; it surfaced only when the disabled flag was added and the header line
  happened to be compared. Both flags are now ported, the `expanded === 'true'` suppression comes with
  them, and the fixtures gained the three rows that would have caught it — including the suppressed
  case, which is the one a reader is most likely to "simplify" away.
  The gap itself is now guarded rather than remembered: `test_every_flag_the_renderer_can_emit_is_exercised_by_a_fixture`
  derives the flag vocabulary from `observe.py` and fails if `fixtures.json` does not exercise all of
  it. Regenerating the fixtures removes the author's-belief problem for each *case*, but which cases
  exist is still a choice, and that is what let this one go uncompared for months — so the choice is
  now checked against the renderer instead of against memory.
- **The extension check read the browser's debugger count once and called the detach's latency a
  leak.** `chrome.debugger.detach` is asynchronous: the worker's own list is empty the moment its
  `finally` runs, but `chrome.debugger.getTargets()` is the browser's view and trails it. On run
  35557546471 the check reported "2 before the run, 3 after" while the assertion immediately above it
  — the worker reporting what it still holds — passed, and the same pair read 2/2 or 3/3 across the
  eleven other runs in the window, so the reading was a race rather than a defect. It polls now, and
  the timeout does not soften the assertion: a count that never returns to the baseline still fails,
  ten seconds later and with the same numbers in the message. The helper is new rather than a reuse
  of `wait_until`, which returns the first *truthy* value — a count of zero is falsy, so it would
  poll straight through the one answer a count of zero is entitled to give. 232 tests, 3 of them
  here, and the one that matters is the count that never settles: it is what says polling is a
  narrowing of the race and not a way to stop noticing a real leak.
- **An empty path is not a path, in two more places where `exists()` said it was.** `Path("")`
  resolves to the current directory, which exists, so the natural way to write "is this file there?"
  accepts an empty string — the same trap that let a screenshot check pass for a file that was never
  written. The upload op had it worst: `paths: [""]` sailed past the `file not found` check,
  `Path.resolve()` turned it into the working directory, and the step came back `ok: true` with that
  directory's *name* as its target. A caller asking to upload nothing was told it had uploaded the
  project. Blank entries are now refused by name. The fix is deliberately **not** `is_file()`, which
  is the right answer for the screenshot instance: a directory is a legitimate upload target for a
  `webkitdirectory` input, and `tests/test_path_arguments.py` pins that, so the next person to spot
  the shape does not tighten it into a regression. `tests/test_docs.py` had the same hole in the
  guard for the absolute-URL rule: `ROOT / ""` is `ROOT`, so a README link with no path after the
  blob prefix satisfied "is in the tree" by naming the whole tree, and `..` would have satisfied it
  by leaving. The check now resolves and requires the result to be inside the repository. Verified
  by control rather than by passing: the old guard accepted a path-less link the new one rejects.
- **A stalled screenshot capture is retried once, and says so.** The intermittent failure below now
  has a name: CI reports `Page.captureScreenshot: timed out after 30.0s` — the command is accepted
  and no frame comes back inside the client's own 30s timeout, so it is a stall rather than a
  refusal. `Session._capture` now retries exactly once, only for a timeout, and records that it
  happened in the step, so the condition cannot settle into a slow green build nobody hears about;
  `scripts/smoke.py` prints it as `[note] … needed a second attempt`. The mechanism is **not**
  established and this is mitigation, not a fix. The first guess — that it was the *first* capture
  after a tab is closed and another promoted — is disproved: in the run that exercised the retry,
  both captures stalled, the second one sent immediately after the first had succeeded. What
  survives is something about the state or the moment rather than the ordinal. It reproduces on CI's
  Chrome 152 and never on the local 153 across a dozen runs, the page reports itself visible either
  way, and it hit one matrix entry out of three on the same commit and runner image, which reads as
  scheduling noise. The retry is the part that can be justified without knowing which it is, and the
  note is what keeps the question open rather than closed — it is also how the run above was found,
  and a silent retry would have let a two-stall run read as a fix.
- **A screenshot check that could pass without a screenshot.** `scripts/smoke.py` built the path from
  the op's `target` with `Path(target or "")`, and the empty path resolves to the current directory —
  which exists, and on Linux is 4096 bytes. A screenshot op that failed therefore read as
  "4096B, exists" and satisfied `shot.exists() and shot.stat().st_size > 1000`. It stayed hidden
  because the failure is intermittent and the passing branch is silent: in one CI run the JPEG op
  reported no file and the check still went green, and in the next the PNG op did the same and the
  check failed — on its `.png` suffix test, reporting the directory's own size as the file's. The
  path is now `None` rather than the empty path, both halves are asserted, and the op's own
  `error`/`detail` is printed, because a missing file and a check that never looked are otherwise
  indistinguishable. Printing that detail is what produced the diagnosis above; before it, the same
  failure reported only `browser_error`.
- **A goal that had stopped making progress kept spending steps until `max_steps`.** `Session.act` has
  counted consecutive no-change actions for as long as it has existed, and reports them as `stuck`;
  the goal loop never read it, so the only bound on a stalled goal was the caller's step budget.
  Measured on a real daily check-in: the submit succeeded, the page never changed again, and the
  model spent four `WAIT`s discovering it before the run ended `stopped: hit max_steps=6` — a status
  that reads as "still working" when the truth is "there is nothing left to do". The loop now stops
  once the count reaches the limit `browser.py` already defines, and reports `stopped: no progress`.
  The observation is refreshed *before* the break on purpose: `verify` judges the page as it stands,
  so a goal whose last action removed the thing it acted on still ends `done` when the assertion
  proves it.   The count also belongs to a run rather than to the session — it is only ever
  incremented, so a goal that inherited the previous goal's streak would call itself stuck on step 1.
  The README names the stop statuses as well, because it described `status` as the model's own
  summary and that stopped being the whole truth the moment the loop started writing it.
- **The candidate list was cut by where an element sat rather than by what it was.** Both truncations
  — `observer.js`'s 250-element table cap and `policy.reachable_first`'s 120-candidate question cap —
  ordered by viewport position with no notion of role, so a submit button below the fold lost its
  place to a hundred navigation links that happened to be on screen. Worse than losing it, the cut
  depended on how far the page had been scrolled: on 1point3acres `/home` the same goal reached its
  target page once and answered `BLOCKED` twice, because the element it needed was in one read and
  not the next. Role now outranks position in both places, position still separates candidates inside
  a tier, and the role tiers in `policy.py` are compared against the ones in `observer.js` by a test
  — two hand-maintained lists that have to agree is exactly the kind of invariant that quietly stops
  holding.
- **Every browser check left its browser — and its profile — behind.** `BrowserManager.shutdown`
  called `terminate()` on the process it had started, and `launch_chrome` passes
  `start_new_session=True`, so Chrome leads its own process group and its renderers, GPU process and
  utility processes are in that group with it. Signalling the leader alone leaves the rest running:
  measured on this machine, 49 processes on `jev-smoke-*` profiles were still alive, holding 50 MB of
  temporary profiles nothing would ever remove. `cdp.stop_chrome` now signals the group, waits, and
  escalates — with a guard that refuses to signal this process's own group, because a caller that
  ever launched without `start_new_session` would otherwise take itself down along with the browser.
  Two of the three scripts never got as far as stopping anything: `smoke.py`'s `main` loops over
  `LIVE_MANAGERS` and nothing ever put a manager in it, and `extension_check.py` never called
  `shutdown` at all. Both now reclaim the browser and the profile in a `finally`, which is where it
  has to be — the checks return early when they fail, and a failed run is exactly when a browser is
  most likely to be left behind.
- **`status: done` was reported over a page that did not prove it.** The reconciliation between the
  model's summary and `verify` only ever ran one way: an assertion that passed upgraded `BLOCKED` to
  `done`, while an assertion that failed left `status` reading `done` with a `verified: FAIL` line
  underneath it. That asymmetry is the wrong one to leave open, because the two errors do not cost the
  same — a false "failed" invites redoing work that is already finished, but a false "done" invites
  the caller to stop on a task that is not. Measured on a real daily check-in: `status: done` after
  four steps, and again at step 0 with the element it had been told to click not even on the page,
  while the points balance had not moved either time. A model that reports DONE over a page that does
  not prove it has not finished the goal, it has run out of ideas; the status now reads
  `unconfirmed: the model reported done, the page does not prove it`, and the trace records which way
  the assertion won. With no `verify` there is no second opinion, so `done` still stands — also
  pinned, so the rule stays scoped to a disagreement rather than to doubting the model in general.
- **The execution fixtures were generated for one platform.** `browser.py::_select_all` sends Meta
  (4) on a Mac and Ctrl (2) everywhere else, so the modifier travelled from the generating machine
  into `chrome-extension/test/act-fixtures.json`. This machine is a Mac; CI is Linux; so CI
  regenerated the file as `2` and `test_the_committed_fixtures_are_what_the_generator_produces`
  refused a file that was perfectly correct where it was written. Everything else was green — the
  parity run, the whole suite, and the real-browser check — because none of them regenerate the file
  on the other platform, and the JS side had hardcoded `platform: 'mac'` so both halves agreed here
  and disagreed there. The platform is now an input per case rather than an inherited fact, the clear
  path is generated once for each value, and the property is asserted rather than the mechanism:
  build the file pretending to be three platforms and require one answer. Covering only the
  generator's own platform is exactly the state that hid this, so the Ctrl/Meta pair is asserted as a
  pair — which also means the branch that decides what the user's keyboard does has coverage for the
  first time.
- **A red CI run could not be asked why.** The four browser checks run with `continue-on-error`, so
  GitHub reports every one of them as passing — a step that fails but is allowed to has
  `conclusion: success` and only `outcome: failure`, which the API does not expose. The single detail
  went to the step summary, which is rendered in the browser and returned by no API, so the comment
  above it ("a failing run here is diagnosable from an API call") was only half true. The tails now go
  to `::error` annotations as well, which the Checks API does return, along with an annotation naming
  the four outcomes outright. That is how the failure above was found after the first attempt to read
  it failed. A branch that could never fire is fixed too: it watched for `scenario failed`, a string
  that only appears in `smoke.py`, while `pytest -q` says `1 failed` for a failing test and `1 error`
  for a collection problem.
- **A dropped browser socket is rebuilt instead of wedging the server forever.**
  `websockets` never reconnects, and the manager kept the `Cdp` object regardless of
  its state — so a quit browser, a slept machine or an unanswered keepalive turned the
  first hiccup into a permanent one: every later call failed with
  `transport closed … keepalive ping timeout` until the process was restarted, with
  `connected: true` reported throughout because it only meant "the object is not None".
  The manager now checks liveness before each call, drops a dead socket along with the
  sessions bound to it (their target ids only exist on that socket), and opens a fresh
  one. In `launch` mode it **adopts the browser it already started** via
  `reattach_chrome` rather than launching a second one and orphaning the first. This is
  the shape of failure that reads as "this tool is hard to use": one silent break, then
  every attempt failing with the same cryptic error.
- **What the page just added is no longer the first thing cut.** Candidate targets were
  truncated in document order, so a menu that only enters the DOM on hover sorted last
  and fell outside the 120-candidate window — measured on 1point3acres, where the
  check-in item sat at index 189 of 195. `reachable_first` now picks the first `limit`
  candidates by `(occluded, not in viewport)` and then restores document order, which
  keeps the newly-revealed item and the model's ordering predictable at the same time.
- **A retried operation stops being offered for that element.** The model has no memory
  between steps beyond the history it is shown, and an operation already in the history
  is evidence for repeating it — clicking a hover-only trigger toggles its `aria-expanded`,
  which the model read as progress, so eight clicks looked like eight steps forward.
  Measured distribution: `HOVER 0.65 / CLICK 0.28` cold, `CLICK 0.35 / HOVER 0.28` after
  three clicks on the same trigger. `stalled_targets` / `withdraw_stalled` now withdraw
  the third repeat of one `(operation, ref)` pair while leaving other operations on the
  same element on offer — `HOVER` on a clicked trigger survives.
- **A goal no longer plans against a page that is still mounting.** `load` fires long before a
  page's JavaScript has finished, and the observer's settle pass only waits while there is
  *nothing* actionable at all — so on a content-rich page it answers immediately with a table
  that is missing exactly the late half. `browser_goal` and `browser_open` now read until two
  consecutive reads agree on the ref set, bounded by `JEVMCP_SETTLE_TIMEOUT`, so a page that is
  already rendered costs one extra read and nothing more.
- **A first `BLOCKED` is re-read once before it is believed.** "Nothing to act on" about a page
  that has not finished rendering is an answer about the clock, not about the goal. The re-read
  is allowed only while nothing has been attempted yet, only once per goal, and only out of the
  goal-wide recovery budget, so `max_steps` stays a bound on billed requests.

  Both are honest about their reach: they cover the page that is *late*, not the element the
  observer never reports. On 1point3acres the daily-task menu is absent from every read of
  `/home` even after settling — that one is a hover-only menu, not a slow page, and is what
  the `HOVER` entry under *Added* addresses.

### Changed

- **A docstring claimed one key configures both models, and the loader does not do that.** The
  comment above `_turbo_backend` said pointing `TYPESAFE_BASE_URL` at OpenRouter "lets a single
  `OPENROUTER_API_KEY` drive both the decision model and the text helper". The first half is true —
  the decision model falls back to that key, because every decisions route speaks the same contract.
  The second half is not: the text helper is resolved from `TEXT_MODEL_API_KEY` with no fallback, and
  posts to `TEXT_MODEL_BASE_URL`, which is DeepSeek by default. A reader who believed the sentence
  would set one key, find `TYPE_TEXT` refused, and have no reason to look at the variable that was
  actually missing — which is the exact shape of a live run that failed to type into a field. The
  comment now says which key configures which model and that covering both with one key is a
  configuration rather than something the loader arranges. A test pins the separation, because
  adding the fallback looks like a kindness and is not one: an OpenRouter key sent to DeepSeek earns
  a 401 whose message names the wrong provider, and a run diagnosed from the wrong provider's error
  is a run nobody diagnoses.
- **The two ways to attach a page now share one setup block, and the reason is written down.** A
  `Session` reaches a page either by attaching to a target it just created (`_attach_page`, via
  `browser_open`) or by attaching to a tab that already exists (`switch_tab`). CDP documents
  `Emulation.setDeviceMetricsOverride`, `Emulation.setFocusEmulationEnabled` and
  `Page.addScriptToEvaluateOnNewDocument` as session-scoped, which made the second route look like it
  was missing three calls — the shape of a bug where a switched-to tab is read and photographed
  through a viewport the config never asked for. It was investigated as the likely cause of the
  intermittent `Page.captureScreenshot` failure, and **it is not that**: with `switch_tab` skipping
  the block entirely, a switched-to tab still reported `window.innerWidth` as `cfg.window`, still
  answered `document.hasFocus()` true, still ran rAF, and still fired a document-start script after
  navigating itself. Chrome applies these to the *target*, so a re-attach inherits them and the
  missing calls cost nothing observable. The block is shared anyway — the scoping is undocumented, a
  silent failure here would look like an ordinary observation rather than an error, and one block
  means the next per-session setting cannot be added to one path and forgotten in the other — and
  `tests/test_session_prep.py` pins that sharing rather than any pixel. The screenshot flake stays
  open; nothing here closes it.

- **Handing the task over is now the design, not an option.** `browser_goal` gained `url`, so a whole
  browser task is **one call** — open, drive, verify — instead of requiring the caller to open the
  page first and then hand over. The server's own `instructions` were rewritten around that rule: a
  task goes to `browser_goal`; the manual loop is the fallback for the two cases that need it (no
  model key, or a page you want to look at yourself); and reading is explicitly *not* a task, so the
  free keyless tools remain the right answer for a look. Both READMEs, `llms.txt` and the tool
  reference were brought in line.

  This is where the previous pass stopped short. The READMEs, `llms.txt`, `docs/DESIGN.md` and
  `browser_goal`'s own docstring had all been corrected to sell the handoff — but `instructions` is
  the one text a *connecting host* receives before it picks a tool, and it still described the manual
  loop (`browser_open -> observe -> act -> assert`) without ever naming `browser_goal`. A host that
  read it did exactly that, one call per click, so delegation only ever happened when a human asked
  for it by name.

  The guards pin the properties that were missing rather than the presence of a word:
  `tests/test_instructions.py` checks the handoff is stated *first* and stated *as the rule* (a guard
  that only checked "`browser_goal` is mentioned" passes while the text still reads as "drive it
  yourself", which is how the drift survived a docs pass in the first place), and
  `tests/test_turbo_resilience.py` checks the goal navigates *before* it observes — a goal that plans
  against the page it just left is the failure `url` exists to remove.

  One measurement decided the rest of the change. **WorkBuddy delivers a server's *tools* to the
  model and drops its *instructions*.** Across the recorded model requests, the MCP tool schemas are
  plainly there — in the newest one, `browser_goal` sits at offset 43,594 of the payload and
  `browser_observe` at 78,394 — while the instructions appear nowhere. Absence is meaningful rather
  than truncation: the system prompt begins at offset 13, so anything appended to it lands far above
  the payload cap. Fixing `INSTRUCTIONS` therefore helps every client that honours it (Claude Code,
  Claude Desktop, Cursor, …) and does **nothing** for WorkBuddy. That is why the rule also ships as
  `skills/jev-ultrafast-mcp/SKILL.md`, in a channel that client does render.

- **Both READMEs, `docs/DESIGN.md` and the one-line summaries now lead with the handoff.** They used
  to sell the project as a keyless server with no second model — descriptionally true of the browser
  tools and a bad account of the project, which ships a decision model precisely so a long flow does
  not cost the caller a turn per click. The opening, the session walkthrough, the comparison table,
  the `browser_goal` reference and the FAQ now say which of the two jobs is being done and who does
  it, and `tests/test_docs.py` checks that the handoff stays in the pitch rather than migrating back
  down to the tool reference.

- **The READMEs no longer claim the project needs no key at all.** They opened with "No API keys" and
  "No second model", which was true of the browser tools and false of the project: turbo mode sends
  your goal and the current element table to a decision model. Both READMEs now separate the two
  paths — the browser tools never call out, `browser_goal` is the opt-in exception and says so where
  it is described — and the FAQ answers "does anything leave my machine?" with the actual answer
  instead of a comfortable one. The comparison table, the `README.zh-CN.md` mirror, the package
  docstring and the heading in `docs/DESIGN.md` were corrected the same way.
- **The configuration table listed two of the seven variables that exist.** It gained
  `TYPESAFE_BASE_URL`, `OPENROUTER_API_KEY`, `TYPESAFE_MODEL`, `TEXT_MODEL_API_KEY`,
  `TEXT_MODEL_BASE_URL`, `TEXT_MODEL` and `JEVMCP_WINDOW`, and now says plainly that only the
  decision-model group reaches the network, and only while `browser_goal` runs.

### Added

- **Discoverability, in the places a reader actually arrives from.** `pyproject.toml` carries a
  keyword-bearing `description`, `keywords`, classifiers and project URLs, so the PyPI page and the
  packaging indexes describe the project in the words people search for. [`server.json`](server.json)
  is the [MCP registry](https://github.com/modelcontextprotocol/registry) entry — reverse-DNS name,
  repository, and the `pypi` package with a `stdio` transport — and
  [`.github/workflows/publish.yml`](.github/workflows/publish.yml) publishes a `v*.*.*` tag through
  PyPI trusted publishing, which is the step that unblocks the registry. Both READMEs gained a
  contents index, links to the projects this one is measured against, and a hero card
  (`assets/social-preview.png`, rendered by `assets/make_social_preview.py` rather than drawn by a
  model, so its text is the right text).
- **`llms.txt`** — a machine-readable index of what this server is, what its ten tools do, and what
  it does and does not send over the network, for agents that read a repository before recommending
  it.
- **An install path that needs no checkout**, and it is verified rather than assumed:
  `pip install "git+https://github.com/jiawei686/jev-ultrafast-mcp"` into a fresh venv produces the
  console script and a working MCP handshake — ten tools listed, on Python 3.13 against the current
  `mcp` SDK. Both READMEs say where the package is not on PyPI yet, instead of pointing at a name
  that does not resolve.
- A **Publishing** section in `CONTRIBUTING.md`: the two one-time steps, and the three files that
  name a version and therefore have to move together.

### Fixed

- **`install.py` deleted the settings it does not write.** The script's promise is that it merges
  into a client's config rather than overwriting it, and that held for the file but not for the
  entry: `_entry()` rebuilt the server entry from scratch, so every key it does not itself write — a
  hand-added `cwd`, a `disabled` flag, and all of `env` except `JEVMCP_HEADLESS` — was gone after one
  run, with the `.bak` as the only place it survived. On the machine this was found on, that meant
  losing `JEVMCP_ALLOW_DOMAINS`, which is a safety setting rather than a preference. An install now
  merges one level down as well: the keys the script owns (`command`, `args`, `env`, and `type` for
  VS Code) are refreshed, `env` is merged key by key, and everything else is left as it was found.
  Codex's TOML dialect had the same defect in a different shape — the whole `[mcp_servers.*]` table
  was regenerated, which also reset a `startup_timeout_sec` someone had raised back to the default —
  and now carries unowned lines across verbatim, in the table and in its `.env` sub-table alike. A
  root key that exists but is not an object (`"mcpServers": []`) is refused with a sentence instead
  of a `TypeError`.
- **Attach mode could not connect to Chrome 144+.** The debugging server started from
  `chrome://inspect/#remote-debugging` is WebSocket-only: it answers 404 to `/json/version` and to
  every other `/json/*` path, by design, so "404" does not mean "nothing is listening" — a client
  that only speaks the HTTP discovery API concludes exactly that, and there is no way to attach to a
  current Chrome through the toggle. `attach_chrome` now falls back to the port and browser
  WebSocket path in the browser's `DevToolsActivePort` file (searched in the platform's usual browser
  data directories, or wherever `JEVMCP_ATTACH_PROFILE_DIR` points), accepts an explicit `ws://` URL
  without probing at all, and reports which of the three it used when none works. The socket is also
  opened with a human-sized `open_timeout`, because Chrome gates each client behind an approval
  dialog and the handshake waits on that click rather than on the network. That approval is per
  browser session, not per connection and not per action: after one click, reconnects from fresh
  processes are accepted with no prompt (verified: three new processes twenty minutes later), and
  no action ever prompts. Both READMEs now say so, because the existing "the first connection waits
  on that click" reads as "every connection does".
- **Attach mode quit the user's own browser on shutdown.** `JEVMCP_MODE=attach` drives the browser
  the user is already using, but `shutdown()` sent `Browser.close` unconditionally — correct in
  `launch` mode, where the browser belongs to this process, and wrong in `attach`, where it means
  closing every window the user has open. `browser_close(shutdown_browser=True)` reached it, and so
  did `atexit`, so the same mistake also fired when the server exited for any reason. Teardown now
  detaches in attach mode (`BrowserManager.owns_browser`), `browser_close` reports
  "detached (your browser is still running)" instead of "browser stopped", and `browser_doctor` no
  longer promises to launch a browser it will not launch.
- **`browser_goal` reported `status: blocked` for goals that had visibly succeeded.** `status` is the
  model's own summary and `verify` is checked by code, but the two were reported side by side with no
  rule for which wins when they disagree — and they disagree in the ordinary case where a goal's last
  action removes the thing it acted on. Click a check-in button and the button is gone; the model,
  finding nothing left to act on, reports `BLOCKED` on a goal that in fact succeeded, leaving the host
  to read `status: blocked` next to `verified: PASS` and work out which one to believe. The assertion
  decides now: a passing `verify` reports `status: done`, and the trace records the disagreement.
  Both directions are pinned by tests, so the rule cannot be satisfied by optimism alone.
- **Turbo mode raised `KeyError` / `JSONDecodeError` instead of reporting a failure.** The decision
  model is reachable through more than one route, and none of them is guaranteed to honour the
  contract: a gateway can answer 200 with an error envelope, a route can rename a field, a proxy can
  answer with an HTML error page. All of those mean the same thing — no decision was made, so nothing
  was executed — but they arrived as exceptions from inside the loop, which the host reads as a bug in
  the server rather than a provider that did not answer. A response is now unwrapped through one
  helper (`policy._answers`) that names the question left unanswered and what the envelope did
  contain, and a non-JSON body is reported as such. Covered by `tests/test_turbo_resilience.py`,
  which fakes the provider and needs no key.
- **A provider failing mid-goal erased the steps already taken.** `browser_goal` caught the failure
  outside the loop, so a run that died on step five reported only the error — the host could not tell
  a goal that was one click from done from one that never started. The failure is now handled where
  it happens, keeping the trace, and every way the model can fail answers under the single
  `turbo_unavailable:` prefix the tool already used for "no key".
- **Turbo mode's stale-ref recovery was bounded per goal while its comment claimed per step.** The
  counter was never reset, so a page that invalidated a ref once per step spent the whole goal's
  recovery on its first few steps and then failed a goal that was working. Recovery is now per step,
  with a goal-wide ceiling so per-step recovery cannot multiply the request count by
  `STALE_REF_RETRIES` — `max_steps` still bounds what a run can cost. Both ends are pinned by tests.
- **`install.py` could write a config the client cannot start.** It names a repo-local
  `.venv/bin/python`, or falls back to whatever interpreter is running the script — and that
  interpreter may not have the package installed, only the checkout. The client then spawns it from
  its own directory, finds no module, logs nothing, and the tools simply never appear: the same
  silent failure the installer exists to remove, one level down. It now probes the interpreter from a
  neutral directory in isolated mode before writing, refuses with the exact `pip install -e` command
  to run, still warns rather than blocking under `--print`, and never blocks `--uninstall` — a
  deleted venv is a reason to remove the entry, not a reason to be stuck with it.
- **The fixture server's per-request log was never silenced.** `scripts/smoke.py` assigned
  `log_message` to the `functools.partial` handed to the HTTP server, which sets the attribute on the
  partial object and never reaches the handler class, so every request printed a line interleaved with
  the check output the suppression was meant to keep readable. It is a handler subclass now.
- **`browser_goal` crashed on the model's own answer.** The operation offered to the decision model
  and the operation the server dispatched on were different strings: `Element.target_kinds()` reported
  `TYPE` while the dispatch table was keyed on `TYPE_TEXT` — the name upstream uses and this project's
  own label table already used. Nothing was mis-configured and no request failed; the model answered
  correctly and the server raised `KeyError`. The vocabulary now lives in one place
  (`policy.OPERATION_TO_ACT`), the server dispatches through it, and anything not in it is never
  offered. Turbo mode had never been run, which is why this survived:
  `tests/test_turbo_vocabulary.py` compares the two ends without a browser or a network.
- **`browser_goal` treated a stale ref as a dead end.** Between observing and acting, a page that
  rewrites itself invalidates the refs; the guard is right to refuse, but the loop ended the goal
  instead of re-observing. The same goal therefore succeeded or failed depending on whether the page
  happened to be still while it was read — and pages with live regions are never still: typing into
  Wikipedia's search box replaces the whole search area, taking the submit button's node with it.
  A step now re-observes (bounded) on `detached` / `page_changed` / `target_changed` / `unknown_ref`,
  while reasons that mean "this element cannot be acted on" still end the goal. A test forces every
  reason the observer can return to be classified into one of those two buckets, so a new one added
  in JavaScript cannot land in the fatal bucket by default.
- **The text helper's failure did not say what the helper answered.** A chat model fills field values
  (Jev only chooses), and must answer exactly `{"text": "..."}`. When it does not, the run stops with
  nothing typed — correct, since typing a guess is worse — but *"returned no usable value"* cannot be
  acted on: a wrong shape and an empty answer read identically and need different fixes. The answer is
  now carried in the error. `tests/test_text_helper.py` covers both without a network.
- **Client-rendered pages reported themselves as empty.** `readyState === 'complete'` says the HTML
  parser finished, not that the page is drawn: Bing's home page is 89 nodes at that moment and 620
  three seconds later. The first read after navigation therefore saw nothing and reported
  *"no actionable elements visible"* about a page full of controls — the agent changes strategy for
  no reason and spends a round trip learning it was wrong. `browser_observe` now waits for evidence
  (an element appearing, bounded by `JEVMCP_SETTLE_TIMEOUT`) instead of trusting `readyState`. Pages
  that are simply empty are not waited on at all. Covered by a new smoke section against
  `tests/csr.html`, which draws its controls 1.2s late on purpose.
- **PNG screenshots failed.** `Page.captureScreenshot` takes `quality` only for JPEG and rejects an
  explicit `null` for it, so `{"op": "screenshot", "format": "png"}` errored while the default JPEG
  path worked. The parameter is now omitted rather than set to `None`.
- **A macro recorded where the task ended as its `start_url`.** A search flow finishes on the results
  page, so replay began there and could not find the first step's target. `start_url` is now captured
  when recording starts. Found by running the macro against a real site, not by the fixture.
- **Closing the tab you were driving could attach to it while it was dying.** `Target.closeTarget`
  is a request, not a fact: the target keeps appearing in `Target.getTargets` for a moment
  afterwards, so "the target list is non-empty" is not "there is a tab left to drive". `close_tab`
  now waits for a survivor instead of taking index 0, which is a race the CI runner won and a warm
  laptop lost. The smoke suite asserts the new contract directly.
- **Tab references were positional and short-lived.** `switch_tab` / `close_tab` took a list index,
  and that list is renumbered whenever it changes — activating a tab alone can reorder it. Carrying
  an index across calls could close the wrong tab, and an out-of-range index raised a bare
  `IndexError`. Tab ops and the `browser_tabs` tool now accept a stable `target_id`, `list` prints a
  `#handle` for each tab, and the new-tab warning suggests the stable reference. `index` still works
  when it is read in the same breath as the action.
- The smoke suite clicked a `target=_blank` link and then slept a fixed 0.5s before looking for the
  new tab — tuned for a warm laptop, and a race on a loaded CI runner. It polls now.
- `scripts/mcp_check.py` hard-coded the child environment, so any machine whose Chrome is not in a
  default location (or any CI runner) failed. It inherits `os.environ` and overrides only the keys it
  controls.
- CI captures smoke / MCP / pytest output and republishes failures as annotations and a step summary,
  because downloading job logs requires repo admin rights and a red X on a public repo was otherwise
  undiagnosable. Also bumped to `actions/checkout@v7` / `actions/setup-python@v7`.
- **`install.py` wrote WorkBuddy's config into the wrong directory.** The client entry hardcoded
  `~/.workbuddy/mcp.json`, but WorkBuddy resolves its config directory from `WORKBUDDY_CONFIG_DIR`
  and one machine can carry two installs: an older app keeps `~/.workbuddy` while the current one is
  pointed at `~/.workbuddy-ai`. Writing the directory the running app does not read registers nothing
  and logs nothing — the server is simply absent, and since nothing is pending there is no approval
  prompt either, so the one piece of UI that would have explained it never appears. The entry now
  resolves the directory the way the app does: an explicit `WORKBUDDY_CONFIG_DIR` is authoritative
  rather than merely a candidate, and the remaining candidates are ordered by how recently the app
  wrote to them (`workbuddy.db-wal` is held open by a running app, so its mtime is the tell). The
  file is chosen by *directory*, not by "the first path that exists" — the directory in use is
  precisely the one that has no `mcp.json` yet, so the old rule skipped straight past it. When two
  directories are present the installer says which one it chose instead of choosing silently.

### Changed

- The in-page helper version is now stated once and matched on both sides (`HELPER_VERSION` in
  `browser.py` had drifted from the `version` the injected script declares, so a page carrying an
  older observer was not always replaced).
- Smoke is 58 checks; the README, DESIGN and CONTRIBUTING figures are refreshed from an actual run.
- **Tab references were positional and short-lived.** `switch_tab` / `close_tab` took a list index,
  and that list is renumbered whenever it changes — activating a tab alone can reorder it. Carrying
  an index across calls could close the wrong tab, and an out-of-range index raised a bare
  `IndexError`. Tab ops and the `browser_tabs` tool now accept a stable `target_id`, `list` prints a
  `#handle` for each tab, and the new-tab warning suggests the stable reference. `index` still works
  when it is read in the same breath as the action.
- The smoke suite clicked a `target=_blank` link and then slept a fixed 0.5s before looking for the
  new tab — tuned for a warm laptop, and a race on a loaded CI runner. It polls now.
- `scripts/mcp_check.py` hard-coded the child environment, so any machine whose Chrome is not in a
  default location (or any CI runner) failed. It inherits `os.environ` and overrides only the keys it
  controls.
- CI captures smoke / MCP / pytest output and republishes failures as annotations and a step summary,
  because downloading job logs requires repo admin rights and a red X on a public repo was otherwise
  undiagnosable. Also bumped to `actions/checkout@v7` / `actions/setup-python@v7`.

### Changed

- **The extension check invokes the extension instead of patching it, so the `activeTab` grant is
  tested rather than assumed.** `scripts/extension_check.py` used to run its table comparison against
  a throwaway copy of the extension carrying one added `host_permissions`, because a popup opened by
  navigating a tab to `popup.html` has no access to the page and the grant was believed to need a
  toolbar click no automation could produce. It does not: `Extensions.triggerAction` runs the
  extension's default action at the browser level, which is the same action the toolbar button runs,
  and it grants `activeTab`. The comparison now runs against the manifest that ships, with
  `activeTab`, `scripting` and `storage` and no host permissions — 20 checks rather than 18. The copy
  survives only as a fallback for a Chrome too old to have the command, and it says so in its output,
  so a run that lost the coverage cannot look like one that had it.

  Worth recording why the obvious answer was wrong: `chrome.action.openPopup()` opens the popup and
  the popup still cannot read the page. Measured, not assumed — Chrome deliberately does not treat a
  programmatic popup open as a gesture. `Popup` now has two documented ways in for the same reason:
  navigating a target is what the refusal path needs, and adopting the browser-opened popup is what
  the happy path needs.

- `tests/test_extension.py` guards that decision behaviourally rather than by grepping the script for
  a command name — a stub drives `invoke_action` and asserts the first call is the invocation, with
  the tab as its target, and that only an unavailable command falls back. The grep version of this
  test passed while the invocation had been disabled, which is exactly the kind of guard that is
  worse than none.

## [0.1.5] — 2026-09-20

The release that makes the registry entry submittable. `0.1.4` could not be: the registry proves
ownership of a PyPI package by finding a marker line in the package's README, and a published PyPI
description cannot be edited, so the marker had to arrive with a new version.

### Added

- **`mcp-name: io.github.jiawei686/jev-ultrafast-mcp` in `README.md`** — the line the registry looks
  for to prove the package is ours. It is hidden in an HTML comment, which is the form the registry
  documents for PyPI (and which crates.io strips, hence the note there). `tests/test_docs.py` asserts
  it matches `name` in `server.json`, because a validator's token is invisible to a reader and fatal
  to forget.
- **The MCP registry entry is submitted by the release workflow** —
  [`.github/workflows/registry.yml`](.github/workflows/registry.yml), which `publish.yml` calls with
  `needs: publish`. It authenticates over GitHub OIDC (`mcp-publisher login github-oidc`), so the
  namespace is proved by the workflow's own identity and there is no token to store. It is a separate
  workflow rather than a job so it can be dispatched on its own: a job that only exists on a tag push
  can only be tested by cutting a release, and the registry — still in preview, and warning about
  data resets — is exactly the thing that needs re-submitting without one. It runs
  `mcp-publisher validate` before publishing, which is the only place the registry's real limits are
  enforced.

### Fixed

- **`server.json`'s description was over the registry's limit, so the entry would have been
  rejected.** The registry caps `description` at 100 characters and answers with `422 expected length
  <= 100`; the file carried 309 — a paragraph written for a README, in a field that stores one
  sentence. `mcp-publisher validate` against the live service is what found it, and
  `tests/test_docs.py` now asserts the caps so a local `pytest` catches it first. The replacement is
  capability-focused, which is what the schema asks for: *"Hand browser work off to a server-side
  agent: read the page as a table, act, then verify."*
- Both READMEs said the registry submission was "the step still outstanding". It is wired to happen
  on every tag now, so they say that instead of describing a manual step nobody had run.
- **The registry rejected the submission for a missing marker, which only a real submission could
  reveal.** `mcp-publisher validate` checks the schema and passed; `publish` then failed with
  `400 ... must appear as 'mcp-name: io.github.jiawei686/jev-ultrafast-mcp' in the package README`,
  because ownership is verified against the *published* description and not the file. The workflow
  and the marker are the two halves of that fix, and neither was visible from inside the repository.

## [0.1.4] — 2026-09-20

### Added

- **A Chrome extension that shows the element table for the page you are on** —
  [`chrome-extension/`](chrome-extension/README.md). Click it on any page and you get the same rows
  the model gets, drawn by the server's own observer and a port of `observe.py`, so a `ref` in the
  popup means what it means in a session. The second read of a page renders as a delta, which is how
  you watch a page change. Three permissions and no host access: `activeTab` (the tab you clicked it
  on, and nothing else), `scripting`, `storage`. Nothing in it acts on the page.
- **`scripts/extension_check.py`** — loads the extension into a real Chrome and compares its table
  with the server's, character for character, then does it again after opening the fixture's modal,
  so the delta path and the overlay warning are covered too. It loads the shipped manifest first and
  checks that it declines a page it has no access to in words rather than throwing: `activeTab` is
  granted by a real toolbar click, which no automation can produce, so the comparison then runs
  against a throwaway copy of the extension with one added host permission. The script says so in its
  own output rather than leaving it implied.
- `tests/test_extension.py` — the extension's contracts: the vendored observer is byte-identical to
  the server's, the fixtures are what the generator produces, the manifest asks for no more than it
  uses, the port still agrees with Python, and `popup.js` has not started formatting rows itself.

### Fixed

- **Two fields the observer emitted were dropped before the table was rendered.** `inViewport` was
  never read, so `in_viewport` was permanently `True` and the `»` flag the header documents was
  unreachable code; `offscreen` was never read, so "(offscreen N, » = will scroll on act)" always
  printed a zero. Both are the same shape — a field the observer emits and `from_raw` ignores — and
  neither was visible from inside the repository. They surfaced as parity failures between the port
  and the real renderer, which is the whole argument for having a port and a parity harness.
- **`launch_chrome` can now load an extension**, via `allow_extensions=True`. `--disable-extensions`
  does not merely deprioritise extensions, it blocks their pages outright: a browser started with it
  answers a navigation to `chrome-extension://…/popup.html` with `ERR_BLOCKED_BY_CLIENT` and no
  explanation of why.

### Changed

- Both READMEs and `llms.txt` describe the extension, and `CONTRIBUTING.md`'s check list grew from six
  to seven. `test_every_version_in_the_tree_agrees` now covers `chrome-extension/manifest.json` too,
  so the two numbers describing one thing cannot drift apart.

## [0.1.3] — 2026-09-20

### Fixed

- **The READMEs no longer say the project is not on PyPI.** They said it in four places — two per
  README — and had since before 0.1.0 shipped, so for three releases the install instructions routed
  readers through a `git+https://…` install for something `pip install jev-ultrafast-mcp` already
  did. Nothing failed, because a stale claim about your own distribution is invisible from inside
  the repository: no test read it, and the only way to see it was to read the published page. Both
  READMEs now offer `uvx jev-ultrafast-mcp` — verified against the published package over real stdio,
  which answers `initialize` with `serverInfo.version` `0.1.2` — and a guard fails if either README
  stops naming the package or the old claim comes back.
- **`server.py` is covered by the version test now.** `test_every_version_in_the_tree_agrees` checked
  four of the five places a version is written down, and the one it missed is the one a client
  actually sees: it is what the server answers `initialize` with. A release could have bumped the
  four and left that one behind with every test still green.
- **`CONTRIBUTING.md` describes publishing as it is, not as it was planned.** The one-time PyPI
  setup was still written in the future tense — "a tag is *meant* to publish", "that *needs* a
  pending publisher" — long after it had been carried out. It is now marked as a record rather than
  a to-do list, the registry half is named as the one outstanding step, and the numbering gap
  (1, 2, then 4) is closed. Also written down: the trap that made `v0.1.0` publish nothing, because
  Actions evaluates a workflow at the tagged commit.

## [0.1.2] — 2026-09-20

### Fixed

- **Every README reference is absolute, because PyPI resolves nothing relative.** 0.1.1 moved the
  billing panel into the hook; on the published PyPI page it arrived as a broken-image icon with the
  alt text sitting where the evidence should be. GitHub fills a relative path in and PyPI does not,
  and it does not warn either: a relative link became
  `https://pypi.org/project/jev-ultrafast-mcp/docs/DESIGN.md` — a 404 page — and a relative image
  cannot be routed through the camo proxy that PyPI's Content-Security-Policy allows images from, so
  it renders as a broken box rather than as a 404 anyone would notice. Nine references in each README
  were affected: both images, the language switch, and the links to `docs/DESIGN.md`, `server.json`,
  `CONTRIBUTING.md`, `LICENSE` and `pyproject.toml`. All now address the repository directly, and a
  guard fails if a relative reference comes back. In-page `#anchor` links are untouched — PyPI
  rewrites those to `user-content-…` and they already worked.

## [0.1.1] — 2026-09-20

### Changed

- **The bill now comes before the install steps** — `assets/openrouter-spend.png` moved from below the
  verbatim `browser_goal` trace up to directly under the opening hook, in both READMEs. "A cent for the
  whole day" is the one claim a reader cannot check by reading the code, and on the PyPI page it sat
  past the setup block; it now lands where the claim is made.

## [0.1.0] — 2026-09-19

First public release.

### Added

- **MCP surface** — `browser_open`, `browser_observe`, `browser_act`, `browser_assert`,
  `browser_macro`, `browser_goal`, `browser_tabs`, `browser_sessions`, `browser_close`,
  `browser_doctor`.
- **Zero-key architecture** — the calling agent is the policy. No second model, no API keys, no
  screenshots in the loop. `browser_goal`'s TypeSafe turbo path is opt-in and needs
  `TYPESAFE_API_KEY`.
- **Stable refs** — `WeakMap`-backed, monotonically increasing, never recycled. A `ref` stays valid
  across observations, unlike models that renumber the page on every read.
- **Delta observations** — `+` added, `~` changed, `-` removed, and `= no change` when nothing moved.
- **Batched `browser_act`** — many ops per round trip, with first-op-strict / later-op-loose freshness
  so a batch is not invalidated by its own earlier actions.
- **Guard layer** — `verify`, `reinspect` and `resolve` run in the page. The model never emits a
  selector, a coordinate or JS.
- **Macros** — record semantic descriptors (role + accessible name + context), replay at zero model
  cost, re-resolved at run time. Weak or ambiguous matches raise instead of guessing.
- **Shadow DOM, same-origin iframes and multi-tab** support, including frame-offset-aware scrolling and
  shadow-piercing hit tests.
- **Deterministic assertions** — `url_matches`, `url_contains`, `title_matches`, `text_contains`,
  `text_absent`, `element_exists`, `element_gone`, `value_equals`, `checked`, `count_at_least`, `js`.
- **Safety rails** — `JEVMCP_ALLOW_DOMAINS` / `JEVMCP_DENY_DOMAINS` envelope, secret-field redaction,
  `needs_confirmation` for destructive clicks, upload gating, and `eval` off by default.
- **Self-contained CDP client** — no Playwright, no Selenium, no wrapper library. Chrome launch with
  `--no-sandbox` fallback and post-port liveness confirmation.
- **Verification** — 51-check end-to-end smoke suite against a real browser, 17-check real-stdio MCP
  suite, and 5 pytest unit tests.

[Unreleased]: https://github.com/jiawei686/jev-ultrafast-mcp/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.5
[0.1.4]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.4
[0.1.3]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.3
[0.1.2]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.0
