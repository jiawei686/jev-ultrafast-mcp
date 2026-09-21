# Element table — a Chrome extension

See the element table `jev-ultrafast-mcp` would hand a model, for the page you are looking at.

The server's whole pitch is that the model never invents a target: it picks `e7` out of a table that
code built, and a stale `e7` is refused with a reason instead of clicked. That table is the product.
This extension puts a window on it, so you can read the same rows the model reads — including the
ones it is *not* told about, like a field the observer decided was occluded.

It also runs a recorded macro, with no model in the loop at all. That means re-resolving each step's
target against the page in front of you and dispatching real input at it, and both halves are ports of
the server's own code rather than a second opinion about it — see [Running
one](#running-one-and-why-it-takes-the-debugger) for what that costs in permissions and why it is
still the smallest way to do it.

## Why it is not a second implementation

The obvious way to build this would be to walk the DOM in a content script and print something that
looks like the table. That would be worthless: a screenshot of a table that might not be the table
is a way to be confidently wrong.

So this ships the server's own parts instead.

| File | What it is |
| --- | --- |
| `lib/observer.js` | A byte-identical copy of `jev_ultrafast_mcp/js/observer.js`. `tests/test_extension.py` fails if the two ever differ. |
| `lib/render.js` | A port of `observe.py` — `ROLE_CODE`, `_short`, `Element.render`, `Observation.render`. |
| `lib/macro.js` | A port of `macros.py` — `_norm`, `_tokens`, `_score`, `_substitute`, `resolve`. The half of a replay that decides. |
| `lib/session.js` | A port of `browser.py`'s `Session` — the op dispatcher. The half of a replay that does. |
| `lib/report.js` | A port of `server.py`'s `_render_act` and the replay header, so a report reads the same wherever it was produced. |
| `lib/store.js` | Macro storage in `chrome.storage.local`, with the same `{{placeholder}}` rules as the server's. |
| `background.js` | The service worker, and the only file that calls the debugger API. |
| `test/fixtures.json` | 16 cases whose expected output was produced by the **real Python renderer**. |
| `test/render-parity.mjs` | Renders each fixture with the port and compares character for character. |
| `test/macro-fixtures.json` | 46 cases whose expected output was produced by the **real Python resolver** — refusals included. |
| `test/live-observation.json` | Two raw observer payloads taken off a live page, so the resolver is held to the wire format as it actually arrives. |
| `test/macro-parity.mjs` | Resolves each case with the port and compares, refusing for refusing. |
| `test/act-fixtures.json` | 115 cases from the **real Python dispatcher** — the op tables, the three refusal rules, 32 dispatched ops, 9 rendered reports. |
| `test/act-parity.mjs` | Runs the same ops through both dispatchers and compares the step report *and* the CDP commands each one issued. |

The port is Python-shaped in two places on purpose, because matching the server matters more than
matching JavaScript: lengths are counted in code points rather than UTF-16 units, so a label ending
in an emoji truncates where Python truncates; and `scroll.get(key, 0)` is reproduced with an
own-property check rather than `??`, so a present `0` is never replaced.

Building this found three real divergences, all the same shape — a field one side carries and the
other never reads:

- `inViewport` was dropped, so `in_viewport` was permanently `True` and the `»` flag on an element
  was dead code, even though the header counted those elements in `offscreen`.
- `offscreen` was dropped, so the header's "(offscreen N, » = will scroll on act)" note always said
  zero.
- `hoverable` was never mapped at all, so the port had no `⋮` on a menu trigger and never emitted the
  "(N ⋮ = menu trigger, hover before choosing)" line the server writes above the table. This one
  survived the harness rather than being caught by it: `hoverable` was the single flag the fixture
  set never exercised, so there was nothing for the two sides to disagree about. It surfaced only
  when a new flag was added and the header happened to be compared line for line.

The first two were invisible from inside the repo and showed up as parity failures, which is the
argument for having a port and a parity harness at all. The third is the argument for the other half
of the job: the fixtures are *generated* from Python, but which shapes they cover is still a choice
someone makes, and a flag nobody thinks to include is a flag nobody compares.

## Replay, and the second half of the port

A macro is a recorded path stored as semantics — `{role, name, context}`, never a ref and never a
selector — so it survives a reload, a redesign that keeps the labels, and a different browser
instance. Replaying one means scoring every element on the page in front of you against those
descriptors and acting only when one wins clearly. Two buttons that both score 1.0 are exactly the
silent misclick this design exists to prevent, so the resolver refuses and leaves the page untouched.

That refusal is why `lib/macro.js` is a port rather than a fresh implementation. A replayer needs a
resolver, the server already has one, and a second one that agrees "most of the time" would be worse
than none: the disagreement would surface as a click on the wrong row, silently, with no model in the
loop to notice. So the port carries the same scoring rules, the same three thresholds and the same
refusal sentences, and `test/macro-parity.mjs` holds it to fixtures the real `macros.resolve` wrote.

Four of those fixtures are not written by hand. They come from `test/live-observation.json` — two
reads off a live page through the real observer, one before a hover and one after — because the wire
format has shape an action list does not capture: `context` is emitted *only* on labels that repeat,
`hoverable` only on the trigger, and menu items enter the list wherever they entered the DOM, so
`e8` and `e9` sort between `e1` and `e2`. The descriptors are the ones `macros.describe` produced
from those same reads, which is the path a recording takes.

Two limits are pinned as fixtures rather than described in prose, because a port that quietly
improved on the original would be a second resolver again:

- **A CJK label has no tokens.** `_tokens` splits on `[^a-z0-9]+`, which matches every CJK character
  outright, so a Chinese context contributes nothing to the score and a repeated Chinese label
  cannot be disambiguated. The step refuses, which is the right outcome of a wrong situation.
- **The context bonus is all-or-nothing.** It is added when the overlap clears 0.4 and not
  otherwise, so two contexts that differ by one word both clear it and the tie survives. This is not
  about CJK: `Post C Check in` against `Post D Check in` overlaps by three words out of five and
  fails the same way. `帖子 A 打卡` against `帖子 B 打卡` succeeds, because `{a}` and `{b}` share
  nothing — one Latin character is the whole difference.

Both are limits of the scoring rules rather than of the port, so fixing either means changing
`macros.py` and the port in the same commit. The fixtures will say so if they are changed apart.

## Running one, and why it takes the debugger

Deciding what to click is half a replay. The other half is clicking it, and that half has one
non-obvious requirement: the page has to believe the input came from a person.

`element.click()` does not. Neither does `dispatchEvent(new MouseEvent(...))` — both produce events
with `isTrusted: false`, which a site is entitled to ignore, and the observer's `resolve()` hands back
*coordinates* precisely because the intended consumer dispatches input at them rather than calling
into the node. So the extension asks for the `debugger` permission and drives the tab through
`Input.dispatchMouseEvent`, `Input.insertText` and `Input.dispatchKeyEvent`. That is the only
in-extension way to produce trusted input, and it is what makes a replayed click a real click.

The attachment is held by `background.js` and by nothing else, because two holders of one attachment
is how a run detaches a debugger another run is using. `lib/session.js` is handed a driver object and
never calls the API, which is what lets it have a browser-free test at all. Chrome shows a banner
while a debugger is attached, so the run detaches in a `finally` — including when it fails.

### What it will not do

Four ops behave differently here than on the server, and each is a case where matching the server
would mean doing something to your browser that an extension has no business doing. They are pinned
as fixtures rather than left to a reader to notice:

| Op | On the server | Here |
| --- | --- | --- |
| `scroll` | Scrolls the viewport centre at a configured window size. | Scrolls the viewport centre of *your* tab, because resizing the user's window to match a config file is not the extension's call. |
| `upload` | `DOM.setFileInputFiles` with a path off the disk. | Refused, with a sentence saying why: an extension cannot read a path, and reporting success while attaching nothing is the failure worth avoiding. |
| `tab` | Opens and switches between tabs. | Refused. The extension acts on the tab you invoked it on and reads no others. |
| `eval` | Runs JavaScript and returns the value. | Refused. There is no way for the extension to hold a page evaluated by a script it cannot inspect. |

The other three rules — the confirmation patterns, the secret patterns and the URL envelope — are
*not* divergences and are ported exactly, because they are the decisions that keep an unattended
replay from typing into a password field or clicking "Buy now". A port that got one of those subtly
wrong would not report a problem: it would click, and look exactly like success. So
`test/act-fixtures.json` compares all three against Python in full, refusal sentence included.

### How it was checked

The port is held to Python by fixtures the real dispatcher wrote, and `test/act-parity.mjs` runs the
same 32 operations through both — comparing not just "did the step report `ok`" but the CDP commands
each side issued, because a dispatcher that ignores an argument still reports `ok`. That equivalence
is not measurable from the outside: a click on the wrong element and a click on the right one produce
the same reply.

Two things that parity cannot reach are covered by `scripts/extension_check.py`, which drives a real
Chrome:

- **The report's wording.** The replay header is an f-string inside the `browser_macro` tool rather
  than a function, so reproducing it in a fixture would be writing the expectation twice. Instead the
  check records a macro with the server's own recorder, replays it in the extension, calls the real
  tool with the same macro and compares the two replies character for character.
- **The toolbar button.** `Extensions.triggerAction` runs the same action a click does, so the check
  exercises the `activeTab` grant — but a human click on the icon is still the only thing that proves
  the button itself.

That check earned its place immediately: it caught the extension printing a resolve score as `(1)`
where the tool prints `(1.0)`. A score is a float in Python however integral it looks, and JSON keeps
no trace of that. Nothing in the repo could see it — every score the fixtures happened to carry was
non-integral, where the two agree — and it surfaced only because a real replay of a macro that
matched *perfectly* put the two replies side by side.

## Install it

It is not in the Chrome Web Store; it is a development tool that ships with the repository.

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. **Load unpacked** → pick this `chrome-extension` directory.
4. Pin it, then click it on any page.

The popup observes on open, so the first thing you see is the table. **Observe** reads again;
because the second read of the same page renders as a delta, that is how you watch a page change —
the same `[delta#N]` output the server produces. **Copy** puts the table on the clipboard, so it can
be pasted into a conversation next to whatever the model said.

The **Replay** panel runs a macro with no model in the loop. Point it at the page the macro was
recorded on, pick a macro from the list, fill in any `{{placeholders}}` it asks for, and press Run.
The report under it is the tool's own report format, and the table above refreshes as a delta
against where you started. Macros come from the server's own state directory, so a recording made
with `browser_macro` in a conversation is one you can replay here; the panel also takes a pasted
macro for a machine the extension cannot read.

## Permissions

Four, and no host permissions at all:

| Permission | Why |
| --- | --- |
| `activeTab` | Read the tab you clicked the extension on, and only that tab. No access to your history. |
| `scripting` | Inject the observer into that tab. |
| `storage` | Remember the "page text" toggle between opens, and keep your macros. |
| `debugger` | Dispatch real input during a replay. See [above](#running-one-and-why-it-takes-the-debugger) for why synthetic events will not do. |

There is no `<all_urls>` and no `tabs`. An extension that can read every page you visit is one you
have to trust; this one can only see the page you point it at, which is one you can check.

`debugger` is the one worth reading twice, because it is the strongest permission here: while it is
attached, Chrome shows a banner on the tab and the extension has the same access DevTools does. It is
requested for the duration of a replay and released in a `finally`, so the banner is bounded by the
run rather than by how long the popup is open. Nothing else in the extension touches it, and
`tests/test_extension.py` fails if a second file starts calling the API.

## Develop it

```bash
# Regenerate the icons (needs Pillow, which is in the dev extras).
.venv/bin/python chrome-extension/icons/make_icons.py

# Regenerate the parity fixtures after changing the renderer on either side.
.venv/bin/python chrome-extension/test/make_fixtures.py

# Compare the port against those fixtures.
node chrome-extension/test/render-parity.mjs

# Regenerate the macro fixtures after changing the resolver on either side.
.venv/bin/python chrome-extension/test/make_macro_fixtures.py

# Compare the resolver port against those fixtures.
node chrome-extension/test/macro-parity.mjs

# Regenerate the execution fixtures after changing browser.py, safety.py, config.py or the port.
.venv/bin/python chrome-extension/test/make_act_fixtures.py

# Compare the dispatcher port against those fixtures.
node chrome-extension/test/act-parity.mjs

# Drive a real Chrome: load the extension, read a page, replay a recorded macro, and compare the
# extension's report with the reply the real browser_macro tool gives for the same macro.
.venv/bin/python scripts/extension_check.py          # add --headed to watch it
```

`tests/test_extension.py` runs the first two as assertions, plus the manifest checks: that every
icon exists at the size the manifest claims, that the permission list is exactly the four above, and
that `popup.js` has not started formatting rows itself. `tests/test_macro_port.py` runs the second
pair, and `tests/test_act_port.py` the third — both additionally failing if the committed fixtures
are not what their generator produces. That check earned its place by catching exactly that during
development, and again in the execution layer: a fixture carrying a stopwatch reading is a fixture
that never matches, so `ms` is asserted absent and the port's `ms` is asserted present elsewhere.

The parity tests need Node. It is not a dependency of the Python package, so each pytest case skips
with a message rather than passing silently when Node is missing. Point `JEVMCP_NODE` at a binary if
Node is somewhere unusual.

`scripts/extension_check.py` is the one that needs a browser, and it is not part of `pytest` — a test
that launches Chrome is a test nobody runs on a train. It is what covers the two things fixtures
cannot: the wording of the reply the `browser_macro` tool builds, and the `activeTab` grant that only
a real invocation exercises.

## Versioning

`manifest.json`'s version tracks the package version — `tests/test_docs.py` fails if they disagree,
because the extension ships the server's observer and two numbers describing one thing should not be
able to drift apart.
