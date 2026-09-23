# jev-ultrafast-mcp — design and differentiation

## The three things this is built from

| | What it is | What it is good at | Where it stops |
|---|---|---|---|
| **`browser-use/jev-ultrafast`** | A Python library + local inspector for a browser agent driven by TypeSafe's Jev and a small text model | 7.1 s Google Flights run; ~101 browser protocol calls instead of ~1092; a dynamic operation/target action space chosen in one speculative request | No MCP surface. Requires **two** API keys (`TYPESAFE_API_KEY` + `TEXT_MODEL_API_KEY`). Viewport-only element table. Refs are renumbered every observation. Its own docs list shadow roots, frames, uploads, pop-ups and nested scrolling as out of scope |
| **`browser_harness.mcp_server`** (ships inside `browser-harness`) | One MCP tool per CDP helper: `js()`, `click_at_xy()`, `query_selector`, `press_key`, `upload_file`, … | Thin, complete, honest primitives. Reuses the user's real Chrome | The **agent** must author selectors and coordinates. Nothing checks that a target still exists, is visible, or is not covered. Raw `js()` is exposed by default |
| **TypeSafe (`docs.typesafe.ai`)** | An evaluation endpoint: `state` + typed `questions` (`noul` / `choice` / `score`) → structured `answers`, with speculative fan-out in one call | Turning "what should happen next" into a bounded, validated multiple choice. Distributions and confidences, not prose | It chooses; it does not generate text, and it does not touch a browser. It has no concept of a page, a ref, or a guard |

## The gap we fill

jev-ultrafast proves that *choosing* beats *generating* for browser control, and it does the whole
job in one process: a 7.1 s Google Flights run, ~101 protocol calls instead of ~1092. What it does
not offer is a choice about **who drives**. It always spends a decision model on the critical path,
needs two keys, and has no MCP surface — while the agent calling it is already a capable model
sitting idle.

So the thesis here is that the choice belongs to the caller, and both halves have to be good:

> **Hand the loop over when the flow is long enough to be worth it; drive it yourself when it is
> not. Either way the server makes the page cheap to read and impossible to mis-aim at.**

Everything below follows from that. The handoff is `browser_goal` — one tool call, the loop runs
here, and a decision model picks each step from refs the page actually has. Driving it yourself is
`browser_open` → `browser_observe` → `browser_act`, where the host agent is the policy and the
server needs no key and no second model.

One more difference is worth naming because it is invisible until you configure it: jev-ultrafast
requires **two** keys, because its text helper posts to one fixed provider. Here the decision model's
API is a choice — Jev's own, or the same model through OpenRouter's decisions route — and the text
helper inherits whichever of those also serves chat. On OpenRouter that is one key for both models.
On Jev's own API there is nothing to inherit, because an evaluation endpoint does not write prose, so
the helper needs a chat provider of its own. Either way the key and the URL are taken from the same
provider, and that invariant is what keeps a configuration mistake from presenting itself as a bad
key.

## Seven differentiators

### 1. The handoff is a tool call, not an architecture

A browser flow is a loop. Where that loop lives shapes everything else, and it should not be decided
once for all tasks: a two-click login is not worth delegating, and a twenty-step checkout is not
worth twenty turns of a frontier model's context.

`browser_goal` runs the loop server-side. Your agent sends the goal — not the page — and pays one
turn: **measured 4 decisions, 14,626 tokens, 1.8 s model + 1.1 s page, 3.3 s wall** for a three-step
goal, and every run prints that line so the handoff can be priced rather than argued about.

Driving it yourself is the other half, and it needs no key and no second model at all: `browser_open`
returns an indexed element table, the host agent picks a ref, `browser_act` executes it. One model
call per step — the one the host was already making.

`browser-harness` makes the second half available and leaves the first out entirely; jev-ultrafast
makes the first available and the second impossible. Having both is the point, because the guard
that makes delegating safe is the same guard that makes driving yourself safe: the model never emits
a selector, only a ref that the server verifies against the page before acting on it.

Measured over the full 15-section scenario (30 browser ops, 61 checks): **16,435 bytes of
observation reached the model — about 137 tokens per browser operation, page text included.**
A screenshot-per-step loop spends 1,000–1,500 tokens per step on the image alone.

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
e12 btn⊗ Submit answer   ← present but disabled; shown, never offered as a target
```

jev checks occlusion at execution time, which costs the agent an entire round trip per covered
control. Reporting it up front turns a wasted step into a line of text.

A control that exists but cannot be used *yet* is the same kind of fact, and the observer used to
throw it away: disabled candidates were dropped while **collecting**, before ranking, so a submit
button that only enables once an option is chosen never entered the table at all. The model could
select the option and then have nothing it was allowed to press, and reported the form as having no
way to submit it. It is now kept and marked `⊗`, and left out of the *offers* instead — the same
place an occluded element is left out, for the same reason. The distinction is the point: "not there"
and "there, but not usable yet" are different messages, and only the second one tells the model what
the page is waiting for.

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
form in favour of forty footer links. Here candidates are ranked — actionable before disabled,
interactive controls before secondary roles, and only then in-viewport before off-screen — the top N
are kept, and they are **re-rendered in document order** so the table stays readable. Off-screen
controls are included rather than dropped; they are actionable because `act` scrolls them into view,
and their count is reported in the header. Position is deliberately the *last* term: putting it first
let a submit button below the fold lose its slot to a hundred navigation links that happened to be on
screen, and made the kept set a function of how far the page was scrolled, so the same page produced
a different table on the next read.

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

## Whose browser it is

`launch` and `attach` look like a one-line config difference and are not, because they differ in
ownership. In `launch` the browser is this process's: it starts on the first `browser_open`, and
stopping it on the way out is housekeeping. In `attach` the browser is the user's own — their tabs,
their logins, possibly their work — and only the tab jev opened is in scope.

That distinction is a safety property, not a nicety, so it is stated in the code
(`BrowserManager.owns_browser`) rather than inferred at each call site: only an owned browser is sent
`Browser.close`. Teardown detaches otherwise, and `browser_close` reports the difference instead of
claiming a stop that did not happen. `atexit` goes through the same path, because a server exiting is
not a reason to quit a browser it never started.

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
| `browser_goal` | Hand the whole goal over: run the loop server-side through Jev — TypeSafe's own API, or OpenRouter's decisions route under `JEV_PROVIDER=openrouter` (needs that provider's key) |
| `browser_tabs` / `browser_sessions` / `browser_close` | Tab and session management |
| `browser_doctor` | Browser binary, connection, which provider is paying, keys, policy envelope |

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

`*` accepts `type` · `»` off-screen · `⊘` covered · `⊗` disabled · `⋮` menu trigger · `▾` expanded ·
`✓`/`·` checked state ·
`@context` appears only when a label repeats, which is when it is actually needed to disambiguate.
