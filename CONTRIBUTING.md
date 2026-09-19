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

`scripts/turbo_check.py` is the sixth, and it needs a decision-model key rather than the network: it
lets Jev drive the fixture through a goal and then verifies the page with code. Run it whenever you
touch `policy.py` or the loop in `browser_goal`. Turbo mode is where a bug costs money instead of
time, and it is the one path no other check enters — a `KeyError` on the model's own correct answer
lived through a fully green suite because of exactly that gap.

`scripts/checkin.py` is not a check but the shortest honest exercise of the whole system, and it runs
without a key:

```bash
.venv/bin/python scripts/checkin.py --port 8901   # learn, then replay, then notice it already ran
```

Its three stages are the three things this project claims: the model can work a page out, a macro
replays the result with no model at all, and an assertion — not the model's summary — decides whether
it worked. Run it after touching `macros.py` or the recording path in `browser.py`.

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

## Publishing

Nothing is on PyPI yet, and the repository is the source. The moment that changes, two things happen
at once, because the MCP registry publishes a *package*:

1. **PyPI.** A `v*.*.*` tag is meant to publish `dist/` through PyPI trusted publishing (an OIDC
   workflow, so no API token is stored in this repository). That needs a one-time setup on the PyPI
   side: add a pending publisher for `jev-ultrafast-mcp` pointing at this repository and the workflow
   file.
2. **The MCP registry.** [`server.json`](server.json) carries the registry entry — reverse-DNS name
   `io.github.jiawei686/jev-ultrafast-mcp`, the repository, and the `pypi` package with a `stdio`
   transport. Once the package exists, `mcp-publisher publish` takes it from there, and clients that
   read the registry can find the server without being told about it.

Until then, `pip install "git+https://github.com/jiawei686/jev-ultrafast-mcp"` installs the same code,
and the README says so rather than pointing at a package that does not resolve.

### The one-time setup, step by step

One web page, once, and after that a tag is all a release needs.

**1. The pending publisher.** <https://pypi.org/manage/account/publishing/> → *Add a new pending
publisher* → GitHub. Five fields, and every one of them has to match this repository exactly:

| Field | Value |
|---|---|
| PyPI Project Name | `jev-ultrafast-mcp` |
| Owner | `jiawei686` |
| Repository name | `jev-ultrafast-mcp` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

**A pending publisher does not reserve the name.** It creates the project only when it is first
used, so if someone else registers `jev-ultrafast-mcp` in the meantime the publisher is invalidated
and this whole step has to be redone. Publish promptly after creating it.

**The GitHub environment does not have to exist first.** `publish.yml` declares `environment: pypi`,
and [GitHub creates an environment the first time a workflow references a name that does not exist
yet](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
— so the `pypi` in the table above is a name this repository *declares*, not one you have to go and
reserve beforehand. Create it by hand only when you want protection rules, which is the one thing
that does need it to exist up front: Settings → Environments → New environment, named `pypi` —
<https://github.com/jiawei686/jev-ultrafast-mcp/settings/environments>. PyPI's own docs call this
optional but strongly recommended, because it is what lets you require a manual approval before a
release goes out, later, without editing the workflow.

**2. The tag.** With the publisher in place, publishing is the ordinary thing:

```bash
git tag -a v0.1.0 -m "0.1.0"
git push origin v0.1.0
```

The workflow builds `dist/` and uploads it over OIDC. Watch it under
<https://github.com/jiawei686/jev-ultrafast-mcp/actions>, and if it fails read the job rather than
guessing: trusted publishing fails loudly on purpose, because a release that silently did not
publish is worse than one that did not start.

**4. The registry.** Once the package resolves on PyPI, `server.json` is submittable as it stands:

```bash
mcp-publisher publish
```

None of this needs to be done blind. The build can be checked without publishing anything:

```bash
python -m build                          # writes dist/
python -m pip install dist/*.whl         # into a throwaway venv
python scripts/mcp_check.py              # 17 checks, over real stdio
```

That last command is the one that matters for a *package*: run with the throwaway interpreter it
exercises the installed wheel over the real protocol, so a wheel that dropped `js/observer.js` — the
package-data mistake that leaves a working checkout and a broken install — fails there rather than
in a user's client.

### What to keep in sync when releasing

Three places name the project or its version, and stale copies are the ones that mislead a search
engine or a client:

- `pyproject.toml` — `version`, and the `description` / `keywords` / `classifiers` that are also what
  a search result shows.
- `server.json` — `version`, and the package version it references.
- `CHANGELOG.md` — the release section, which is the page people land on from the releases tab.

## Security

Do not open a public issue for a sandbox escape, a policy bypass (`JEVMCP_ALLOW_DOMAINS` /
`JEVMCP_DENY_DOMAINS` / `needs_confirmation`) or a way to get a secret field's value into the
observation. Report it privately via GitHub's security advisory tab instead.
