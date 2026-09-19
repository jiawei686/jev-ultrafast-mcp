# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
