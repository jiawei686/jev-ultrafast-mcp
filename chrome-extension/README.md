# Element table — a Chrome extension

See the element table `jev-ultrafast-mcp` would hand a model, for the page you are looking at.

The server's whole pitch is that the model never invents a target: it picks `e7` out of a table that
code built, and a stale `e7` is refused with a reason instead of clicked. That table is the product.
This extension puts a window on it, so you can read the same rows the model reads — including the
ones it is *not* told about, like a field the observer decided was occluded.

## Why it is not a second implementation

The obvious way to build this would be to walk the DOM in a content script and print something that
looks like the table. That would be worthless: a screenshot of a table that might not be the table
is a way to be confidently wrong.

So this ships the server's own parts instead.

| File | What it is |
| --- | --- |
| `lib/observer.js` | A byte-identical copy of `jev_ultrafast_mcp/js/observer.js`. `tests/test_extension.py` fails if the two ever differ. |
| `lib/render.js` | A port of `observe.py` — `ROLE_CODE`, `_short`, `Element.render`, `Observation.render`. |
| `lib/macro.js` | A port of `macros.py` — `_norm`, `_tokens`, `_score`, `_substitute`, `resolve`. The other half of a replay. |
| `test/fixtures.json` | 16 cases whose expected output was produced by the **real Python renderer**. |
| `test/render-parity.mjs` | Renders each fixture with the port and compares character for character. |
| `test/macro-fixtures.json` | 46 cases whose expected output was produced by the **real Python resolver** — refusals included. |
| `test/live-observation.json` | Two raw observer payloads taken off a live page, so the resolver is held to the wire format as it actually arrives. |
| `test/macro-parity.mjs` | Resolves each case with the port and compares, refusing for refusing. |

The port is Python-shaped in two places on purpose, because matching the server matters more than
matching JavaScript: lengths are counted in code points rather than UTF-16 units, so a label ending
in an emoji truncates where Python truncates; and `scroll.get(key, 0)` is reproduced with an
own-property check rather than `??`, so a present `0` is never replaced.

Building this found two real bugs in `observe.py`, both the same shape — a field the observer emits
that the Python model never reads:

- `inViewport` was dropped, so `in_viewport` was permanently `True` and the `»` flag on an element
  was dead code, even though the header counted those elements in `offscreen`.
- `offscreen` was dropped, so the header's "(offscreen N, » = will scroll on act)" note always said
  zero.

Neither was visible from inside the repo. Both showed up as parity failures between the port and
Python, which is the argument for having a port and a parity harness at all.

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

## Permissions

Three, and no host permissions at all:

| Permission | Why |
| --- | --- |
| `activeTab` | Read the tab you clicked the extension on, and only that tab. No access to your history. |
| `scripting` | Inject the observer into that tab. |
| `storage` | Remember the "page text" toggle between opens. |

There is no `<all_urls>` and no `tabs`. An extension that can read every page you visit is one you
have to trust; this one can only see the page you point it at, which is one you can check.

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
```

`tests/test_extension.py` runs the first two as assertions, plus the manifest checks: that every
icon exists at the size the manifest claims, that the permission list is exactly the three above,
and that `popup.js` has not started formatting rows itself. `tests/test_macro_port.py` runs the
second pair, and additionally fails if the committed fixtures are not what their generator produces
— a check that earned its place by catching exactly that, during development.

The parity test needs Node. It is not a dependency of the Python package, so the pytest case skips
with a message rather than passing silently when Node is missing. Point `JEVMCP_NODE` at a binary if
Node is somewhere unusual.

## Versioning

`manifest.json`'s version tracks the package version — `tests/test_docs.py` fails if they disagree,
because the extension ships the server's observer and two numbers describing one thing should not be
able to drift apart.
