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
- Both READMEs now open with what a session actually looks like (a real element table, a real delta,
  a real `= no change` line, and a verbatim search run), then a plain-language "what you can ask it
  to do" / "what it cannot do" / FAQ, before the technical reference.
- `JEVMCP_SETTLE_TIMEOUT` and `JEVMCP_SETTLE_POLL_MS` — how long to wait for a late-rendering page,
  and how often to re-read while waiting.

### Fixed

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

[Unreleased]: https://github.com/jiawei686/jev-ultrafast-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jiawei686/jev-ultrafast-mcp/releases/tag/v0.1.0
