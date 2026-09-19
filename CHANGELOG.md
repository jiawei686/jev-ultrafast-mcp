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
- **Chinese README** — [`README.zh-CN.md`](README.zh-CN.md), switchable from the English one.
- The README now shows real observation output: the element table, a delta, a `= no change` line,
  duplicate controls carrying `@context`, and a detached-ref refusal.

### Fixed

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
