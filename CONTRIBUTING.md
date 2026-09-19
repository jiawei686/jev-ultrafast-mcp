# Contributing

Thanks for looking. This project has one strong opinion, and it is worth reading before you change
anything.

## The design constraint

**The calling agent is the policy. The server is not.**

That means the server is allowed to be *fast*, *precise* and *safe*. It is not allowed to be *smart*.
Concretely, the server must never:

- call a second model to decide what to do next (the optional `browser_goal` turbo path is the single,
  explicitly opt-in exception, and it must stay opt-in);
- ask the model to produce a CSS selector, an XPath, a pixel coordinate, or a JavaScript snippet in
  order to act (with the same exception for `browser_goal`, plus `eval` when `JEVMCP_ALLOW_JS=1`);
- guess when a target is ambiguous. **Refuse and report. Never pick the nearest match.**

If a change makes the server cleverer at the cost of any of the above, it will be declined. The value
here is that an agent driving this server cannot silently mis-aim.

## Setting up

```bash
git clone git@github.com:jiawei686/jev-ultrafast-mcp.git
cd jev-ultrafast-mcp
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

You need a Chromium-family browser on `PATH`, or point `JEVMCP_CHROME` at one.

## Before you open a PR

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke.py        # 58 checks against a real browser
.venv/bin/python scripts/mcp_check.py    # 17 checks over real stdio MCP
```

All four must pass. `smoke.py --headed` lets you watch the browser drive.

`scripts/live_check.py` is the fifth, and it is the only one that is optional: it drives real
websites (Bing, DuckDuckGo) over real stdio MCP and therefore needs the network. Run it when you
change anything about how targets are resolved, how pages are read, or how macros replay — the
fixture is well-behaved, the open web is not, and every one of the bugs it has caught so far was
invisible locally.

## What good changes look like

- **A new op** → add it to the dispatch table in `browser.py`, give it a failure reason from the
  existing vocabulary, and add a labelled section to `scripts/smoke.py`. `tests/fixture.html` is where
  you add the page you need — it is a single self-contained file on purpose.
- **A new observation field** → it must earn its tokens. The element table is the hot path; a field
  that is rarely read is a regression for every caller. Put it behind a mode if it is niche.
- **A new assertion** → `assertions.py`, plus a smoke check that exercises both the pass and the fail
  case. An assertion that can only pass is not an assertion.
- **Anything touching freshness** → be careful. The distinction between strict verification (first op
  after an observation: whole-page key plus per-element guard) and loose reinspection (later ops in the
  same batch: same node, still visible and enabled and hit-testable) exists because strictness applied
  to every op makes batching useless. Read `_guard`, `verify` and `reinspect` before changing it.

## Adding a test fixture

`tests/fixture.html` is deliberately one file with no dependencies, served over local HTTP by
`scripts/smoke.py`. If you need a same-origin frame, add `tests/frame.html` and reference it by path —
do not inline it via `srcdoc`, because the escaping between `srcdoc`, inline `onclick` and the
observer has bitten us before.

## Reporting a mis-aim

The most valuable bug report is "it acted on the wrong element". Please include the observation
(element table) the agent saw, the `ref` it used, and what actually happened. That is the failure mode
this project exists to prevent, so it gets priority.

## Security

Do not open a public issue for a sandbox escape, a policy bypass (`JEVMCP_ALLOW_DOMAINS` /
`JEVMCP_DENY_DOMAINS` / `needs_confirmation`) or a way to get a secret field's value into the
observation. Report it privately via GitHub's security advisory tab instead.
