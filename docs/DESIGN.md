# jev-ultrafast-mcp — design and differentiation

## The three things this is built from

| | What it is | What it is good at | Where it stops |
|---|---|---|---|
| **`browser-use/jev-ultrafast`** | A Python library + local inspector for a browser agent driven by TypeSafe's Jev and a small text model | 7.1 s Google Flights run; ~101 browser protocol calls instead of ~1092; a dynamic operation/target action space chosen in one speculative request | No MCP surface. Requires **two** API keys (`TYPESAFE_API_KEY` + `TEXT_MODEL_API_KEY`). Viewport-only element table. Refs are renumbered every observation. Its own docs list shadow roots, frames, uploads, pop-ups and nested scrolling as out of scope |
| **`browser_harness.mcp_server`** (ships inside `browser-harness`) | One MCP tool per CDP helper: `js()`, `click_at_xy()`, `query_selector`, `press_key`, `upload_file`, … | Thin, complete, honest primitives. Reuses the user's real Chrome | The **agent** must author selectors and coordinates. Nothing checks that a target still exists, is visible, or is not covered. Raw `js()` is exposed by default |
| **TypeSafe (`docs.typesafe.ai`)** | An evaluation endpoint: `state` + typed `questions` (`noul` / `choice` / `score`) → structured `answers`, with speculative fan-out in one call | Turning "what should happen next" into a bounded, validated multiple choice. Distributions and confidences, not prose | It chooses; it does not generate text, and it does not touch a browser. It has no concept of a page, a ref, or a guard |

## The gap we fill

jev-ultrafast proves that *choosing* beats *generating* for browser control. But its speed comes
bundled with a second model on the critical path, and an MCP client **already is a capable model**.
Pointing that architecture at MCP as-is means paying twice for intelligence and adding a round trip.

So the thesis here is different:

> **The calling agent is the policy. The server's job is to make the page cheap to read and
> impossible to mis-aim at.**

Everything below follows from that one decision.

## Seven differentiators

### 1. Zero extra model calls, zero API keys

The default path needs no TypeSafe key and no text model. `browser_open` returns an indexed element
table; the host agent picks a ref; `browser_act` executes it. One model call per step — the one the
host was already making.

jev's architecture cannot do this: without `TYPESAFE_API_KEY` it has no policy at all.

Measured over the full 14-section scenario (29 browser ops, 58 assertions): **15,443 bytes of
observation reached the model — about 133 tokens per browser operation, page text included.**
A screenshot-per-step loop spends 1,000–1,500 tokens per step on the image alone.

The TypeSafe path is still implemented (`browser_goal`) for when you *want* the server to drive:
one speculative request carries the operation head plus a target head for every available
operation, exactly as jev does.

### 2. Refs are stable; the action space is not

jev renumbers elements on every observation, so `[7]` can mean a different control three steps
later. Here a ref is a code-owned handle on a live DOM node, assigned once and never recycled:

```
e7  inp* Where from? ▸ "Zurich"     ← still e7 after five more observations
```

A plan written at step 1 is still valid at step 9. This is the single largest source of
multi-step agent errors in an indexed-ref design, and it is removed by construction.

### 3. Guarded execution, with the guard in the page

Before any input the server asks the page three questions, answered **in-page**, so a step costs a
few hundred bytes instead of a guard table:

- `verify(ref, page_key)` — is this still the page and still the element the agent chose?
- `reinspect(ref)` — is this still the same control? (used for the 2nd..nth op of a batch)
- `resolve(ref)` — visibility, enabled state, geometry, occlusion, frame chain — *immediately*
  before dispatch

The model never produces a selector, a coordinate, or a snippet of JavaScript. `click_at_xy` and
`js()` are not in the tool surface at all.

### 4. Batches, and the freshness semantics a batch needs

`browser_act` takes a list of ops. Each extra op in a call is a removed round trip.

Batching forces a design question jev never faces: after op 1 types into a field, the page
legitimately differs from what the agent observed, so a full-state freshness check would reject
op 2 as "stale". The rule implemented here is:

- **first op after an observation** → strict (full page-state + element guard match);
- **later ops in the same batch** → identity only (same node, same role, same accessible name,
  still visible, still enabled, still hit-testable).

Scroll position is deliberately excluded from the page key: geometry is re-resolved before every
input anyway, so a scroll — ours or the page's — is not a semantic change.

### 5. Occlusion is precomputed, not discovered by a failed click

Every element is hit-tested at observation time and carries a flag:

```
e30 btn⊘ Submit          ← covered right now
e11 fil» CV accept=.pdf,.txt   ← off-screen; act will scroll it into view
```

jev checks occlusion at execution time, which costs the agent an entire round trip per covered
control. Reporting it up front turns a wasted step into a line of text.

Two related fixes fall out of the same hit-test work:

- **Shadow DOM is pierced.** `document.elementFromPoint` stops at a shadow host, so a control inside
  a web component looks permanently covered. The observer descends `shadowRoot` chains, so
  `<shadow-widget>` internals are indexed, resolvable and clickable.
- **Same-origin frames are indexed and executable.** Elements inside an iframe get viewport
  coordinates by accumulating frame offsets, and the frame chain is itself hit-tested so a covered
  iframe is not mistaken for a covered button. `scrollIntoView` does not cross frame boundaries, so
  the scroll helper walks the chain explicitly.

### 6. Truncation by usefulness, not by DOM order

The element table is capped. jev truncates the first 250 candidates in DOM order, which can drop a
form in favour of forty footer links. Here candidates are ranked — in-viewport first, then
interactive controls, then secondary roles — the top N are kept, and they are **re-rendered in
document order** so the table stays readable. Off-screen controls are included rather than dropped;
they are actionable because `act` scrolls them into view, and their count is reported in the header.

### 7. Deterministic verification and zero-token replay

jev's own docs say it plainly: *"A `DONE` choice still requires independent outcome verification."*
Two features act on that:

- **`browser_assert`** — `url_matches`, `text_contains`, `element_exists`, `value_equals`,
  `count_at_least`, … evaluated by code against the live page. `pass: true` is a fact.
- **Macros** — record a discovered path, replay it with **no model calls at all**. Replay stores
  semantics, never refs or selectors:

  ```json
  {"op": "type", "target": {"role": "textbox", "name": "Where from?"}, "text": "Lyon"}
  ```

  Each step is re-resolved against a fresh observation by scoring role + accessible name + context.
  A weak match (below threshold) or an ambiguous one (two candidates within 0.06) **fails loudly**
  instead of clicking whatever is nearby. `{{placeholders}}` are filled from `params`, which makes
  one macro reusable across inputs.

## What the policy envelope adds

A browser handed to an autonomous agent needs its blast radius bounded by the server, not by prompt
text.

| Control | Behaviour |
|---|---|
| `JEVMCP_ALLOW_DOMAINS` / `JEVMCP_DENY_DOMAINS` | Navigation outside the envelope is refused with `blocked_by_policy` |
| Confirmation rules | Clicks whose accessible name matches `pay now`, `delete account`, `unsubscribe`, … return `needs_confirmation` instead of executing; re-send the op with `"confirm": true` |
| Secret fields | Password inputs stay **usable** (jev drops them, so it cannot log in) but their value is never serialised: rendered as `«hidden»`, required `confirm` to type into, and never written into a macro |
| `JEVMCP_ALLOW_JS` | JS evaluation is off by default. `js` assertions and the `eval` op are unreachable without it |
| `dry_run` | `browser_act(..., dry_run=True)` reports what would happen and executes nothing |
| Screenshots | Written to `~/.jev-ultrafast-mcp/shots/` and reported as a **path**. Base64 never enters the context |

## Limits, stated honestly

- Deltas pay off on large pages and idle polls. On the 19-element test page a full table is ~900 B;
  a no-op re-read is ~110 B (8×) but a re-read after several changes is close to the full table.
- Cross-origin frames are counted and reported, never silently dropped — but not observable.
- Canvas, WebGL, and arbitrary keyboard widgets remain opaque. `<select multiple>` and native
  pickers are handled at the value level, not the pixel level.
- Macro replay fails when a page's accessible names change between runs. That is the intended
  behaviour; the alternative is a silent misclick.
- `elementFromPoint` occlusion on a transformed or animated element can produce a false negative,
  in which case the click is refused and the agent re-observes.
- One browser profile, shared across sessions. Sessions are separate tabs, not separate browsers.

## Tool surface

| Tool | Purpose |
|---|---|
| `browser_open` | Open a URL in an owned tab, return the element table |
| `browser_observe` | Re-read: delta by default, `full` / `delta` forced, optional JSON copy |
| `browser_act` | Batch of ops (`click` `type` `select` `toggle` `hover` `upload` `keys` `scroll` `nav` `back` `wait` `wait_for_ref` `wait_for_text` `screenshot` `tab` `eval`) then a delta |
| `browser_assert` | Deterministic checks → `pass` / `fail` |
| `browser_macro` | `record_start` · `record_stop` · `run` · `list` · `inspect` · `delete` |
| `browser_goal` | Run a whole goal server-side via TypeSafe (needs the key) |
| `browser_tabs` / `browser_sessions` / `browser_close` | Tab and session management |
| `browser_doctor` | Browser binary, connection, keys, policy envelope |

## Element table format

```
[obs#3] https://example.com/flights  "Flights"  scroll=0/1240  (offscreen 6, » = will scroll on act)
e12 btn   Search
e7  inp*  Where from? ▸ "Zurich"
e8  sel*  Passengers ▸ "3 adults" opts{1 adult | 2 adults | 3 adults | 4 adults}
e9  chk✓  Nonstop only
e10 inp*  Password ▸ «hidden»
e16 btn»  Iframe action
e19 btn   Select  @Zurich → London Option 1 · 3 · any stops
e20 btn   Select  @Zurich → London Option 2 · 3 · any stops
  ! dialog open: Confirm something before continuing. [modal]

[delta#4] https://example.com/flights  "Flights"  scroll=0/1240
~ e7  inp*  Where from? ▸ "Lyon"   (was "Zurich")
+ e34 btn   Continue
  1 changed, 1 new, 0 gone
```

`*` accepts `type` · `»` off-screen · `⊘` covered · `▾` expanded · `✓`/`·` checked state ·
`@context` appears only when a label repeats, which is when it is actually needed to disambiguate.
