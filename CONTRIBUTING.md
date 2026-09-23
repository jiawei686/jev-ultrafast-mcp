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
.venv/bin/python scripts/smoke.py            # 61 checks against a real browser
.venv/bin/python scripts/mcp_check.py        # 17 checks over real stdio MCP
.venv/bin/python scripts/extension_check.py  # 33 checks: the extension's table vs the server's
```

All five must pass. `smoke.py --headed` lets you watch the browser drive.

`pytest` also runs the Chrome extension's parity test, which needs Node on `PATH`. It is not a
dependency of this package, so that one case skips — loudly, naming `JEVMCP_NODE` — rather than
passing quietly when Node is missing. CI installs Node, so a skip there means something is wrong.

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

`scripts/extension_check.py` is the seventh, and it is the only one that loads the Chrome extension
in `chrome-extension/`. Run it whenever you touch `observe.py`, `js/observer.js`, or anything under
`chrome-extension/`. The extension renders the element table with a port of `observe.py`, so a change
to the renderer has two places to make it and one of them is JavaScript; the unit tests catch a drift
between the port and Python, but only this check proves the manifest loads and that the popup reads a
real page. It is also how both bugs in `observe.py`'s `from_raw` were found — `inViewport` and
`offscreen` were emitted by the observer and never read, so the header advertised flags no row ever
drew. Neither was visible from inside the repository.

The `activeTab` grant is exercised, not assumed. `activeTab` is only granted when the user *invokes*
the extension, so a popup opened by navigating a tab to `popup.html` has no access to the page — and
`chrome.action.openPopup()`, which looks like the answer, is a programmatic API that Chrome
deliberately does not treat as a gesture (it opens the popup and the popup still cannot read the
page). `Extensions.triggerAction` runs the extension's default action at the browser level, which is
the same action a toolbar click runs, and it does grant `activeTab`. The check uses it, so the
manifest under test is the one that ships, with `activeTab`, `scripting` and `storage` and no
`host_permissions`. A Chrome too old for the command falls back to a copy with one added host
permission and says so in its output, so a run that lost that coverage cannot look like one that had
it. The toolbar *button* is still the one thing no automation presses.

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

Releases go to PyPI and to the MCP registry, and a `v*.*.*` tag is the whole trigger for both:

1. **PyPI — live.** Tagging publishes `dist/` through PyPI trusted publishing (an OIDC workflow, so
   no API token is stored in this repository). The one-time setup that made it work is recorded
   below; nobody has to repeat it. `0.1.0` onward are on PyPI, so `pip install jev-ultrafast-mcp`
   and `uvx jev-ultrafast-mcp` both resolve.
2. **The MCP registry — wired, submits on the next tag.** [`server.json`](server.json) carries the
   registry entry — reverse-DNS name `io.github.jiawei686/jev-ultrafast-mcp`, the repository, and the
   `pypi` package with a `stdio` transport. `.github/workflows/registry.yml` validates it against the
   live service and submits it over GitHub OIDC, so there is no token to store; `publish.yml` calls
   that workflow with `needs: publish`, because the registry resolves the package and rejects an
   entry whose package it cannot find.

   That workflow is also dispatchable on its own (`gh workflow run registry.yml`), which is how a
   re-submission is made after a registry-side data reset — the registry warns about those while it
   is in preview — and how the first submission was checked without cutting a release.

   **The limits are enforced only by the service.** `mcp-publisher validate` is the way to find out
   whether an edit to `server.json` is still acceptable; it answers with a `422` and the offending
   field. `description` is capped at 100 characters, which is four times shorter than a sentence of
   this project's prose, and an entry over the cap is rejected outright rather than truncated. The
   caps are also asserted in `tests/test_docs.py`, so a local `pytest` catches the common case before
   a tag does.

   **Ownership is proved by a line in `README.md`, not by `server.json`.** The registry checks that
   the package's *published* description contains `mcp-name: <the name in server.json>`, and the
   description PyPI publishes is this project's `README.md` (`readme` in `pyproject.toml`). The marker
   sits at the bottom of that file in an HTML comment, which is the form the registry documents for
   PyPI. Two consequences worth knowing: `validate` does **not** check it — only `publish` does, and it
   fails with a `400` naming the exact string to add — and because a published PyPI description cannot
   be edited, adding or changing the marker requires a new version rather than a re-submission.

One trap worth knowing before the next release: Actions evaluates a workflow **at the tagged
commit**, so a tag pointing at a commit that predates `publish.yml` publishes nothing — and reports
no failure either. `v0.1.0` was moved to a commit that had the workflow for exactly this reason. The
same applies to `registry.yml`: a tag cut before it existed submits nothing to the registry.

### The one-time setup, step by step (done)

Kept as a record rather than a to-do list: this was carried out once, and the publisher it describes
is live. A new project would need it; this one does not. One web page, once, and after that a tag is
all a release needs.

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

**3. The registry.** Once the package resolves on PyPI, `server.json` is submittable as it stands:

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

Seven version strings across six files name the project or its version, and stale copies are the
ones that mislead a search engine or a client:

- `pyproject.toml` — `version`, and the `description` / `keywords` / `classifiers` that are also what
  a search result shows.
- `__init__.py` and `server.py` — the version the package reports, and the one a client sees in the
  `initialize` reply.
- `server.json` — `version`, and the package version it references. Two strings, one file.
- `chrome-extension/manifest.json` — `version`, which tracks the package because the extension ships
  the package's observer and Chrome shows that number next to the name.
- `mcpb/manifest.json` — `version`, the number Claude Desktop shows on the install dialog and the
  only copy that travels as a downloadable file.

`tests/test_docs.py` fails if any of those disagree. `CHANGELOG.md` is the other thing a release
touches, and it is the one nothing asserts, because prose cannot be compared to a number.

## Security

Do not open a public issue for a sandbox escape, a policy bypass (`JEVMCP_ALLOW_DOMAINS` /
`JEVMCP_DENY_DOMAINS` / `needs_confirmation`) or a way to get a secret field's value into the
observation. Report it privately via GitHub's security advisory tab instead.
