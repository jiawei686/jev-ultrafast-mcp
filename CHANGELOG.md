# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

### Changed

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

[Unreleased]: https://github.com/jiawei686/jev-ultrafast-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.0
