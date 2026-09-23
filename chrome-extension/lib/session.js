/* The execution half: observe the page, then act on it.
 *
 * `lib/render.js` is the port of `observe.py` and `lib/macro.js` is the port of `macros.py`. This is
 * the third and last port — `browser.py`'s `Session`, the part a replayer needs: attach, read the
 * state, guard a ref, put the pointer on it, dispatch real input.
 *
 * Why the debugger and not synthetic events. A replayer's whole job is to be believed by the page.
 * `element.click()` and `dispatchEvent(new MouseEvent(...))` produce `isTrusted: false` events, which
 * a site is entitled to ignore, and the observer's `resolve()` returns coordinates precisely because
 * the intended consumer dispatches input at them. `chrome.debugger` is the only in-extension way to
 * get the same `Input.dispatchMouseEvent`, `Input.insertText` and `Input.dispatchKeyEvent` the server
 * uses, so it is what this uses. The cost is the debugging banner Chrome shows on the tab, which is
 * paid for honestly in the README rather than hidden.
 *
 * One deliberate divergence from `browser.py`: no `Emulation.setDeviceMetricsOverride` and no
 * `setFocusEmulationEnabled`. The server drives a tab it owns and can size; this drives the tab the
 * user is looking at, and resizing somebody's window to take a reading of it is not a trade a tool
 * gets to make silently.
 */

import {
  DEFAULT_SECRET_PATTERNS, isSecret, observationFromRaw, renderObservation,
} from './render.js';
import { DEFAULT_THRESHOLD, pythonRepr, resolve as resolveSteps } from './macro.js';

/* Must equal `browser.py`'s `HELPER_VERSION` *and* the `VERSION` inside `lib/observer.js`, which is
 * the number a page actually announces. The first pair is held together by `act-parity.mjs`; the
 * third is held by `tests/test_helper_version.py`, because a server number above the helper's makes
 * the comparison below permanently true -- the observer is reinstalled on every call, and a page
 * carrying the shipped one is never actually upgraded. */
export const HELPER_VERSION = 8;

/* `browser.py`'s tables, verbatim. They are data, not logic, and getting one key code wrong is a
 * keystroke that lands as the wrong character with nothing in the report to say so. */

export const MODIFIERS = {
  alt: 1, option: 1,
  ctrl: 2, control: 2,
  meta: 4, cmd: 4, command: 4, super: 4,
  shift: 8,
};

export const KEY_SPECS = {
  enter: ['Enter', 'Enter', 13], return: ['Enter', 'Enter', 13],
  tab: ['Tab', 'Tab', 9], escape: ['Escape', 'Escape', 27], esc: ['Escape', 'Escape', 27],
  backspace: ['Backspace', 'Backspace', 8], delete: ['Delete', 'Delete', 46],
  arrowup: ['ArrowUp', 'ArrowUp', 38], arrowdown: ['ArrowDown', 'ArrowDown', 40],
  arrowleft: ['ArrowLeft', 'ArrowLeft', 37], arrowright: ['ArrowRight', 'ArrowRight', 39],
  up: ['ArrowUp', 'ArrowUp', 38], down: ['ArrowDown', 'ArrowDown', 40],
  left: ['ArrowLeft', 'ArrowLeft', 37], right: ['ArrowRight', 'ArrowRight', 39],
  home: ['Home', 'Home', 36], end: ['End', 'End', 35],
  pageup: ['PageUp', 'PageUp', 33], pagedown: ['PageDown', 'PageDown', 34],
  space: [' ', 'Space', 32],
};

export const CLICKABLE_KINDS = new Set(['click', 'type', 'select', 'toggle', 'hover', 'upload']);
export const NAV_KINDS = new Set(
  ['nav', 'back', 'forward', 'reload', 'new_tab', 'close_tab', 'switch_tab']);
export const REQUIRES_REF = new Set([...CLICKABLE_KINDS, 'scroll_to', 'wait_for_ref']);

/** `browser.py`'s `_key_parts` — the key name and modifier mask a combo resolves to. */
export function keyParts(combo) {
  const parts = String(combo).replace(/-/g, '+').split('+')
    .map((part) => part.trim().toLowerCase()).filter(Boolean);
  let modifiers = 0;
  while (parts.length && Object.prototype.hasOwnProperty.call(MODIFIERS, parts[0])) {
    modifiers |= MODIFIERS[parts.shift()];
  }
  return { name: parts.length ? parts[parts.length - 1] : '', modifiers };
}

/** `browser.py`'s `_inserts_text` — whether a payload puts text on the page.
 *
 * `keys` is documented as key presses, and for every named key and every combination it is. A bare
 * single character is not: it goes out as `Input.insertText`, which makes `keys` a second way to
 * type. `dispatchKeys` calls this for its own branch, so the answer and the behaviour cannot drift.
 */
export function insertsText(name, modifiers) {
  return !modifiers && Array.from(name).length === 1;
}

/* Geometry failures a scroll can fix. Frame reasons are here because an element can sit perfectly
 * inside its own frame and still be below the top-level fold. */
export const SCROLLABLE_REASONS = new Set(
  ['out_of_viewport', 'occluded', 'frame_out_of_viewport', 'frame_occluded', 'frame_hidden']);

/* `config.DEFAULT_DENY_PATTERNS`. `tests/test_extension.py` fails if these drift from Python's. */
export const DEFAULT_CONFIRM_PATTERNS = [
  '\\bdelete\\s+(account|workspace|repository|project)\\b',
  '\\bpay\\s+now\\b', '\\bplace\\s+order\\b', '\\bcomplete\\s+purchase\\b', '\\bbuy\\s+now\\b',
  '\\bcancel\\s+(order|subscription|booking)\\b', '\\bunsubscribe\\b',
  '\\b(close|delete)\\s+permanently\\b', '\\bconfirm\\s+(payment|order|transfer)\\b',
  '\\bsend\\s+(money|payment)\\b', '\\bwithdraw\\b', '\\btransfer\\s+funds\\b',
];

/* `browser.py`'s `PageStale`: a ref no longer means what the agent chose. */
export class StaleError extends Error {
  constructor(reason) {
    super(reason);
    this.name = 'StaleError';
    this.reason = reason || 'stale';
  }
}

/* `safety.SafetyError`: the request was outside the configured envelope. */
export class PolicyError extends Error {
  constructor(message) {
    super(message);
    this.name = 'PolicyError';
  }
}

/** `browser.py`'s `_error_code` — the vocabulary the report is written in. */
export function errorCode(error) {
  if (error instanceof StaleError) return error.reason || 'stale';
  if (error instanceof PolicyError) return 'blocked_by_policy';
  if (error && error.browserError) return 'browser_error';
  return 'invalid_request';
}

/** Python's `int()`. `int("6.5")` raises rather than truncating, and so does this. */
export function pythonInt(value) {
  if (value === null || value === undefined) {
    throw new TypeError('int() argument must be a string, a bytes-like object or a real number, '
      + "not 'NoneType'");
  }
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number') {
    if (Number.isNaN(value)) throw new TypeError('cannot convert float NaN to integer');
    if (!Number.isFinite(value)) throw new TypeError('cannot convert float infinity to integer');
    return Math.trunc(value);
  }
  const text = String(value).trim();
  if (!/^[+-]?\d+$/.test(text)) {
    throw new TypeError(`invalid literal for int() with base 10: '${value}'`);
  }
  return Number(text);
}

/** Python's `text[:limit]`, counted in code points rather than UTF-16 units. */
function cpSlice(text, limit) {
  return Array.from(String(text)).slice(0, limit).join('');
}

/** Python's `text[:300]` on an error message, plus a bound on absurd payloads. */
function brief(error) {
  const message = String((error && error.message) || error);
  return cpSlice(message.split('\n')[0], 300);
}

/* ------------------------------------------------------------------ guardrails */

function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch (_) {
    return '';
  }
}

function matchesHost(host, pattern) {
  const cleaned = String(pattern || '').toLowerCase().trim();
  if (!cleaned) return false;
  if (cleaned.startsWith('*.')) return host === cleaned.slice(2) || host.endsWith(cleaned.slice(1));
  return host === cleaned || host.endsWith(`.${cleaned}`);
}

/** `safety.check_url` — reject navigation outside the configured domain envelope. */
export function checkUrl(url, { allowDomains = [], denyDomains = [] } = {}) {
  if (url.startsWith('about:') || url.startsWith('data:') || url.startsWith('blob:')) return;
  const host = hostOf(url);
  if (!host) throw new PolicyError(`Refusing to navigate to an unparseable URL: '${url}'`);
  if (denyDomains.some((pattern) => matchesHost(host, pattern))) {
    throw new PolicyError(`Domain '${host}' is on JEVMCP_DENY_DOMAINS.`);
  }
  if (allowDomains.length && !allowDomains.some((pattern) => matchesHost(host, pattern))) {
    throw new PolicyError(
      `Domain '${host}' is outside JEVMCP_ALLOW_DOMAINS (${allowDomains.join(', ')}). `
      + 'That list is a setting, not a default \u2014 browser_doctor reports the whole envelope.');
  }
}

/**
 * `safety.confirm_reason` — why a click needs explicit confirmation, or null.
 *
 * A replayer that hits one of these stops and says so rather than completing the purchase. The
 * server behaves the same way and refuses to replay a macro containing one, which is why an
 * unattended replay of a login flow fails on the password step by design.
 */
export function confirmReason(name, role, patterns = DEFAULT_CONFIRM_PATTERNS) {
  if (role && !['button', 'link', 'menuitem', 'tab'].includes(String(role).toLowerCase())) {
    return null;
  }
  // Lowercased as well as case-insensitively matched, which looks redundant and is: `RegExp`'s `i`
  // flag already ignores case, so deleting this line changes no result and the mutation run says so.
  // It stays because Python's does the same two things (`name.lower()` plus `re.IGNORECASE`) and
  // this file's job is to be that code in another language, not a tidier version of it.
  const haystack = String(name || '').toLowerCase();
  for (const pattern of patterns) {
    if (new RegExp(pattern, 'i').test(haystack)) {
      // `{pattern!r}` in the server's message, which doubles every backslash — and a confirmation
      // rule is nothing but backslashes, so this is the difference between a report a reader can
      // paste back into a search box and one they cannot.
      return `matches confirmation rule ${pythonRepr(pattern)}`;
    }
  }
  return null;
}

/** `_resolve_ref`: `e12` / `e12:3` into the element ref plus an optional option index. */
export function resolveRef(ref) {
  if (ref.includes(':')) {
    const [head, tail] = ref.split(':', 2);
    return [head.startsWith('e') ? head : `e${head}`, tail];
  }
  return [ref.startsWith('e') ? ref : `e${ref}`, null];
}

/* ------------------------------------------------------------------ the session */

/**
 * Build a session over an injected driver.
 *
 * The driver is the only thing that touches the browser, so this module can be exercised without
 * one. It has to answer:
 *
 *   attach() / detach()                        acquire and release the tab
 *   call(method, params)                       one CDP command
 *   evaluate(expression, { awaitPromise })     `Runtime.evaluate`, value unwrapped, throws on a JS
 *                                              exception — which is what makes `safeEval` meaningful
 *   sleep(ms) / now()                          milliseconds to sleep, and a monotonic clock in
 *                                              seconds so a deadline is never a fixed sleep
 */
export function createSession(driver, {
  platform = 'other',
  allowDomains = [],
  denyDomains = [],
  secretPatterns = DEFAULT_SECRET_PATTERNS,
  confirmPatterns = DEFAULT_CONFIRM_PATTERNS,
  helperSource = '',
  includeText = true,
  maxText = 6000,
  // Two numbers, because the server has two numbers. `readState` is told `cfg.max_text` (6000) and
  // `Observation.render` keeps its own default (4000), so the page text a step *reads* and the page
  // text a step's view *shows* are different lengths. Merging them here would have made the port
  // disagree with the server about a report neither side would call wrong.
  maxRenderText = 4000,
  maxActions = 250,
  navTimeout = 20,
  settleTimeout = 4,
  settlePollMs = 120,
  allowJs = false,
} = {}) {
  const state = {
    last: null,
    sequence: 0,
    fresh: false,
    strict: true,
    noChangeStreak: 0,
    history: [],
    screenshot: null,
    attached: false,
  };

  const sleep = (seconds) => driver.sleep(Math.max(0, seconds) * 1000);

  async function safeEval(expression, options = {}) {
    try {
      return await driver.evaluate(expression, options);
    } catch (_) {
      // Mid-navigation the execution context is gone, which is a moment rather than a failure.
      return null;
    }
  }

  /** `Session._focused` — what the page says has focus, in the shape `isSecret` reads.
   *
   * `{unknown: true}` when the page will not say, because "I could not look" is not "the field is
   * ordinary" and the caller treats the two differently. A page with nothing focused answers
   * `{focused: false}`, which is its own answer rather than a refusal.
   */
  async function focused() {
    const found = await safeEval('window.__jevMcp.active()');
    return (found && typeof found === 'object') ? found : { unknown: true };
  }

  async function ensureHelper() {
    if (!helperSource) return;
    const version = await safeEval('(window.__jevMcp && window.__jevMcp.version) || 0');
    if (version !== HELPER_VERSION) {
      await driver.evaluate(helperSource, { timeout: 15 });
    }
  }

  async function attach() {
    if (state.attached) return;
    await driver.attach();
    await driver.call('Page.enable', {});
    await driver.call('Runtime.enable', {});
    if (helperSource) {
      // The same install the server does: the helper is in the page before its own scripts run, so
      // a client-rendered page is observed by a helper that was already listening.
      await driver.call('Page.addScriptToEvaluateOnNewDocument', { source: helperSource });
    }
    await ensureHelper();
    state.attached = true;
  }

  async function detach() {
    if (!state.attached) return;
    state.attached = false;
    await driver.detach();
  }

  async function readState(withText) {
    const options = { maxActions, maxText: withText ? maxText : 0, includeText: withText };
    const raw = await driver.evaluate(
      `window.__jevMcp.readState(${JSON.stringify(options)})`, { timeout: 20 });
    if (raw === null || raw === undefined) {
      throw new StaleError('Page produced no snapshot (still navigating?)');
    }
    return JSON.parse(raw);
  }

  async function pageHasNodes() {
    const count = await safeEval('document.body ? document.body.childElementCount : 0');
    return Number.isInteger(count) && count > 0;
  }

  /** `Session.observe` — one atomic read, with the same patience for a page still painting. */
  async function observe({ includeText: withText = includeText } = {}) {
    await ensureHelper();
    let data = await readState(withText);
    if (!(data.actions || []).length && await pageHasNodes()) {
      // Content but nothing actionable: on a client-rendered page this is what the first read looks
      // like. Wait for evidence (an element appearing), not for a quiet period — a page that is
      // still for three seconds before it paints would otherwise be given up on exactly when
      // patience was the thing needed. Bounded, so an inert page costs one timeout.
      const deadline = driver.now() + settleTimeout;
      while (driver.now() < deadline) {
        await sleep(settlePollMs / 1000);
        data = await readState(withText);
        if ((data.actions || []).length) break;
      }
    }

    state.sequence += 1;
    const observation = observationFromRaw(data, {
      // The observer flags what it recognises; this is the second net the server also casts.
      maskSecrets: (name, role) => isSecret(name, role, secretPatterns),
      sequence: state.sequence,
    });
    // Not in `observationFromRaw` on purpose: Python's is `str(hash(...))`, which has no JS
    // equivalent, so the port carries the observer's own digest alongside instead of inventing one.
    observation.rawDigest = data.digest || '';
    observation.previous = (state.last && state.last.url === observation.url) ? state.last : null;

    state.last = observation;
    state.fresh = true;
    return observation;
  }

  /* ---- refs, geometry, input ---- */

  /**
   * `_guard` — ask the page whether this ref still means what was chosen.
   *
   * `strict` compares the full observed page state; the relaxed form only re-checks that the ref is
   * still the same control, which is what the second and later ops in a batch need, because the
   * batch's own earlier ops legitimately moved the page on.
   */
  async function guard(ref, strict) {
    const result = strict
      ? await safeEval(`window.__jevMcp.verify(${JSON.stringify(ref)}, `
        + `${JSON.stringify(state.last ? state.last.page_key : '')})`)
      : await safeEval(`window.__jevMcp.reinspect(${JSON.stringify(ref)})`);
    if (!result || typeof result !== 'object') return { ok: false, reason: 'page_unavailable' };
    return result;
  }

  async function geometry(ref) {
    const result = await safeEval(`window.__jevMcp.resolve(${JSON.stringify(ref)})`);
    if (!result || typeof result !== 'object') return { ok: false, reason: 'page_unavailable' };
    return result;
  }

  /**
   * `_ensure_reachable` — verify, nudge into view, verify again.
   *
   * Scrolling before input is not a retry of a mutation, because nothing has been dispatched yet.
   * That is why it is allowed to happen here and nowhere else.
   */
  async function ensureReachable(ref, strict) {
    const checked = await guard(ref, strict);
    if (!checked.ok) return { ok: false, reason: checked.reason || 'stale' };
    let where = await geometry(ref);
    if (where.ok) return where;
    if (SCROLLABLE_REASONS.has(where.reason)) {
      await safeEval(`window.__jevMcp.scrollTo(${JSON.stringify(ref)})`);
      await safeEval('window.__jevMcp.settle(\'fast\')', { awaitPromise: true, timeout: 4 });
      where = await geometry(ref);
      if (where.ok) return where;
    }
    return { ok: false, reason: where.reason || 'unreachable' };
  }

  async function labelOf(ref) {
    const value = await safeEval(`window.__jevMcp.label(${JSON.stringify(ref)})`);
    return typeof value === 'string' ? value : '';
  }

  /**
   * `browser.py`'s `_observed` — the element the last observation recorded for `ref`.
   *
   * Not a stale read, which is why the rails may use it where they may not use the caller's own op.
   * Every op carrying a ref has already run `guard`, and the page's `verify`/`reinspect` compare this
   * element's role and name against what was observed, so a ref that reaches a rail is still the same
   * control the observation described — or the op was refused as `target_changed` first.
   *
   * Python has a `by_ref` map for this; this port keeps its observation as an array, so it scans.
   * Same answer, and `act-parity.mjs`'s scripted page reads no actions at all, which is what keeps
   * the element-derived half of both rails inert on both sides of the fixture comparison.
   */
  function observed(ref) {
    if (!state.last || !ref) return null;
    return state.last.elements.find((element) => element.ref === ref) || null;
  }

  /** `browser.py`'s `_click_refusal` — why clicking `ref` must be confirmed first, or null. */
  function clickRefusal(ref, target) {
    const element = observed(ref);
    return confirmReason(target, element ? element.role : '', confirmPatterns);
  }

  /** `browser.py`'s `_typing_refusal` — whether this field's value needs an explicit confirm. */
  function typingRefusal(ref, target) {
    const element = observed(ref);
    if (element && element.secret) return true;
    return isSecret(target, element ? element.role : '', secretPatterns);
  }

  async function mouse(type, x, y, extra = {}) {
    await driver.call('Input.dispatchMouseEvent', { type, x, y, ...extra });
  }

  async function doClick(ref) {
    const where = await ensureReachable(ref, state.strict);
    if (!where.ok) throw new StaleError(where.reason || 'unreachable');
    // A moved event first: hover-only menus are opened by the move, not by the press, and a press
    // with no preceding move lands on a menu that is not there yet.
    await mouse('mouseMoved', where.x, where.y);
    for (const type of ['mousePressed', 'mouseReleased']) {
      await mouse(type, where.x, where.y, { button: 'left', clickCount: 1 });
    }
  }

  async function selectAll() {
    // macOS swaps the select-all modifier for the command key, exactly as `browser.py` does.
    const modifier = platform === 'mac' ? 4 : 2;
    await driver.call('Input.dispatchKeyEvent', {
      type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65,
      modifiers: modifier, commands: ['selectAll'],
    });
    await driver.call('Input.dispatchKeyEvent', {
      type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: modifier,
    });
  }

  async function doType(ref, text, clear, slow) {
    const where = await ensureReachable(ref, state.strict);
    if (!where.ok) throw new StaleError(where.reason || 'unreachable');
    for (const type of ['mousePressed', 'mouseReleased']) {
      await mouse(type, where.x, where.y, { button: 'left', clickCount: 1 });
    }
    if (clear) await selectAll();
    if (slow) {
      for (const character of Array.from(text)) {
        await driver.call('Input.dispatchKeyEvent', { type: 'keyDown', text: character });
        await driver.call('Input.dispatchKeyEvent', { type: 'keyUp' });
      }
      return;
    }
    if (text) await driver.call('Input.insertText', { text });
  }

  async function dispatchKeys(combo) {
    const { name, modifiers } = keyParts(combo);
    if (!name) return;
    if (insertsText(name, modifiers)) {
      await driver.call('Input.insertText', { text: name });
      return;
    }
    let key;
    let code;
    let virtual;
    if (Object.prototype.hasOwnProperty.call(KEY_SPECS, name)) {
      [key, code, virtual] = KEY_SPECS[name];
    } else if (Array.from(name).length === 1) {
      key = name;
      code = `Key${name.toUpperCase()}`;
      virtual = name.toUpperCase().codePointAt(0);
    } else {
      key = name;
      code = name;
      virtual = 0;
    }
    const payload = {
      key, code, windowsVirtualKeyCode: virtual, nativeVirtualKeyCode: virtual, modifiers,
    };
    await driver.call('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...payload });
    await driver.call('Input.dispatchKeyEvent', { type: 'keyUp', ...payload });
  }

  /** `_after_input` — let the page settle, then absorb a navigation if one was triggered. */
  async function afterInput(settleKind) {
    await safeEval(`window.__jevMcp.settle(${JSON.stringify(settleKind)})`,
      { awaitPromise: true, timeout: 5 });
    if (await safeEval('document.readyState') === 'complete') return;
    const deadline = driver.now() + navTimeout;
    while (driver.now() < deadline) {
      if (await safeEval('document.readyState') === 'complete') break;
      await sleep(0.03);
    }
    await ensureHelper();
  }

  async function waitLoaded(timeout) {
    const deadline = driver.now() + (timeout || navTimeout);
    let settled = false;
    while (driver.now() < deadline) {
      if (!settled) {
        // Provoke the context into existing: a navigation that has not committed yet answers
        // nothing at all, and the first successful evaluate is what says it is live.
        await safeEval('1', { timeout: 2 });
        settled = true;
      }
      if (await safeEval('document.readyState') === 'complete') break;
      await sleep(0.02);
    }
    await ensureHelper();
    return (await safeEval('document.readyState')) === 'complete';
  }

  async function navigate(url) {
    checkUrl(url, { allowDomains, denyDomains });
    await driver.call('Page.navigate', { url });
    await waitLoaded();
    state.last = null;
  }

  async function history(delta) {
    const entries = await driver.call('Page.getNavigationHistory', {});
    const index = entries.currentIndex + delta;
    if (index < 0 || index >= entries.entries.length) {
      throw new StaleError('No history entry in that direction');
    }
    await driver.call('Page.navigateToHistoryEntry', { entryId: entries.entries[index].id });
    await waitLoaded();
    state.last = null;
  }

  /* ---- the op dispatcher ---- */

  function stepOf(fields) {
    const step = {};
    for (const [key, value] of Object.entries(fields)) {
      if (value !== null && value !== undefined) step[key] = value;
    }
    if (step.ms === undefined) step.ms = 0;
    return step;
  }

  async function runOp(rawOp, { dryRun, strict }) {
    state.strict = strict;
    const started = driver.now();
    if (!rawOp || typeof rawOp !== 'object' || Array.isArray(rawOp)) {
      return stepOf({ op: '?', ok: false, error: 'bad_op', detail: 'each op must be an object' });
    }
    const op = String(rawOp.op === undefined || rawOp.op === null ? '' : rawOp.op)
      .trim().toLowerCase();
    let ref = null;
    let target = null;

    // A ref is only read for the ops that require one. That is why `scroll` with a ref scrolls the
    // viewport centre instead: the field is never read for it. Reproduced rather than fixed, because
    // fixing it is a change to `browser.py` and to this in the same commit or in neither.
    //
    // `Math.round` where the server writes `int(...)`, which truncates: the two can differ by one
    // millisecond on a step that ran for a fractional number of them. This is a measurement, not a
    // decision -- nothing reads it, and the fixtures drop it for that reason -- so the difference is
    // written down rather than closed. Closing it would mean `Math.trunc`, which would round a step
    // that took 311.9ms down to 311 to match a number nobody compares.
    const millis = () => Math.round((driver.now() - started) * 1000);

    try {
      if (REQUIRES_REF.has(op)) {
        if (!rawOp.ref) throw new TypeError(`op '${op}' needs a ref`);
        [ref] = resolveRef(String(rawOp.ref));
        const checked = await guard(ref, state.strict);
        if (!checked.ok) {
          throw new StaleError(checked.reason || 'stale');
        }
      }

      if (op === 'click') {
        target = await labelOf(ref);
        const blocked = clickRefusal(ref, target);
        if (blocked && !rawOp.confirm) {
          return stepOf({ op, ref, target, ok: false, error: 'needs_confirmation',
            detail: `${blocked}; re-send with "confirm": true to proceed` });
        }
        if (dryRun) return stepOf({ op, ref, target, ok: true, detail: 'dry run' });
        await doClick(ref);
        await afterInput(rawOp.kind === 'combobox' ? 'options' : 'fast');

      } else if (op === 'type') {
        target = await labelOf(ref);
        if (typingRefusal(ref, target) && !rawOp.confirm) {
          return stepOf({ op, ref, target, ok: false, error: 'needs_confirmation',
            detail: 'field looks sensitive; re-send with "confirm": true' });
        }
        if (dryRun) return stepOf({ op, ref, target, ok: true, detail: 'dry run' });
        const clear = rawOp.clear === undefined ? true : Boolean(rawOp.clear);
        const text = String(rawOp.text === undefined || rawOp.text === null ? '' : rawOp.text);
        await doType(ref, text, clear, rawOp.slow);
        if (rawOp.submit) await dispatchKeys('Enter');
        await afterInput('fast');

      } else if (op === 'select') {
        // `raw_op.get("value", raw_op.get("label"))` is presence-based: a present `value` wins even
        // when it is empty, and only an absent one falls through to the label.
        const wanted = Object.prototype.hasOwnProperty.call(rawOp, 'value')
          ? rawOp.value : rawOp.label;
        if (wanted === undefined || wanted === null) {
          throw new TypeError("select needs 'value' or 'label'");
        }
        if (dryRun) return stepOf({ op, ref, ok: true, detail: 'dry run' });
        const selected = await safeEval(
          `window.__jevMcp.selectOption(${JSON.stringify(ref)}, ${JSON.stringify(String(wanted))})`);
        if (!selected || typeof selected !== 'object' || !selected.ok) {
          const reason = (selected && selected.reason) || 'select_failed';
          throw new TypeError(`could not select '${wanted}': ${reason}`);
        }
        target = String(wanted);
        await afterInput('fast');

      } else if (op === 'toggle') {
        const node = pythonInt(ref.slice(1));
        const current = await safeEval(
          `(() => { const e=window.__jevRefs.nodes.get(${node}); return e ? !!e.checked : null; })()`);
        const want = rawOp.state === undefined ? null : rawOp.state;
        if (want !== null && Boolean(want) === Boolean(current)) {
          // Nothing is clicked here, so there is nothing to confirm -- and no label is fetched,
          // because this path costs no page work at all today and should not start.
          return stepOf({ op, ref, ok: true, detail: 'already in requested state' });
        }
        target = await labelOf(ref);
        const blocked = clickRefusal(ref, target);
        if (blocked && !rawOp.confirm) {
          return stepOf({ op, ref, target, ok: false, error: 'needs_confirmation',
            detail: `${blocked}; re-send with "confirm": true to proceed` });
        }
        if (dryRun) return stepOf({ op, ref, target, ok: true, detail: 'dry run' });
        await doClick(ref);
        await afterInput('fast');

      } else if (op === 'hover') {
        if (dryRun) return stepOf({ op, ref, ok: true, detail: 'dry run' });
        const where = await ensureReachable(ref, state.strict);
        if (!where.ok) throw new StaleError(where.reason || 'unreachable');
        await mouse('mouseMoved', where.x, where.y);
        await afterInput('fast');

      } else if (op === 'upload') {
        // `DOM.setFileInputFiles` takes absolute paths on disk. An extension cannot read one, so
        // this refuses in words instead of half-doing it — the safe direction, and the only one
        // available without asking the user to hand over a path that cannot be resolved anyway.
        // A TypeError, not a PolicyError, because that is the bucket `browser.py` puts a bad
        // argument in, and the two reports have to be written in the same vocabulary.
        throw new TypeError(
          'upload needs a path on disk, which an extension cannot read; run this step through the '
          + 'server, or drop the file on the input yourself');

      } else if (op === 'keys') {
        // `raw_op.get("keys") or raw_op.get("key")` -- Python's `or` takes the right side for any
        // falsy value, and an empty array is falsy there and truthy here.
        const given = rawOp.keys;
        const empty = given === undefined || given === null || given === ''
          || (Array.isArray(given) && !given.length);
        const sequence = empty ? rawOp.key : given;
        if (!sequence) throw new TypeError("keys needs 'key' or 'keys'");
        let keys;
        if (typeof sequence === 'string') keys = [sequence];
        else if (Array.isArray(sequence)) keys = sequence.map(String);
        else throw new TypeError('keys must be a string or a list of strings');
        // A bare single character is not a key press; see `insertsText`. This op is a second way to
        // type and it carries no ref, so the field it writes into is whatever has focus. `type`
        // demands `confirm` for a field whose value must not leave the page and records it as a
        // placeholder; a `keys` step can do neither, so this is refused rather than confirmed.
        const typing = keys.some((key) => {
          const parts = keyParts(key);
          return insertsText(parts.name, parts.modifiers);
        });
        if (typing) {
          const where = await focused();
          if (where.unknown) {
            throw new PolicyError('keys would type into whatever has focus, and the page will '
              + "not say what that is. Use `type` with the field's ref.");
          }
          if (where.focused && (where.secret
              || isSecret(where.name || '', where.role || '', secretPatterns))) {
            throw new PolicyError('keys would type into a field whose value must not leave the '
              + "page. Use `type` with the field's ref and \"confirm\": true, which records it "
              + 'as a placeholder rather than in the clear.');
          }
        }
        if (dryRun) return stepOf({ op, ok: true, detail: 'dry run' });
        for (const key of keys) {
          await dispatchKeys(key);
        }
        target = String(sequence);
        await afterInput('fast');

      } else if (op === 'scroll') {
        const direction = String(rawOp.dir || rawOp.direction || 'down');
        const amount = pythonInt(rawOp.amount || 600);
        const delta = (direction === 'down' || direction === 'right') ? amount : -amount;
        if (dryRun) return stepOf({ op, ok: true, detail: `dry run ${direction} ${amount}` });
        // The ref branch `browser.py` has here is unreachable — `ref` is only assigned for ops in
        // REQUIRES_REF and `scroll` is not one of them — so the wheel event is the only path, and
        // the centre comes from the tab's real viewport rather than a configured window size.
        const width = Number(await safeEval('window.innerWidth')) || 800;
        const height = Number(await safeEval('window.innerHeight')) || 600;
        await mouse('mouseWheel', Math.trunc(width / 2), Math.trunc(height / 2), {
          deltaX: (direction === 'left' || direction === 'right') ? delta : 0,
          deltaY: (direction === 'up' || direction === 'down') ? delta : 0,
        });
        await afterInput('fast');
        target = direction;

      } else if (op === 'nav' || op === 'goto') {
        const url = String(rawOp.url || '');
        if (!url) throw new TypeError("nav needs 'url'");
        if (dryRun) return stepOf({ op, ok: true, detail: `dry run -> ${url}` });
        await navigate(url);

      } else if (op === 'back') {
        if (!dryRun) await history(-1);

      } else if (op === 'forward') {
        if (!dryRun) await history(1);

      } else if (op === 'reload') {
        if (!dryRun) {
          await driver.call('Page.reload', {});
          await waitLoaded();
          state.last = null;
        }

      } else if (op === 'wait') {
        const ms = pythonInt(rawOp.ms || 500);
        if (!dryRun) await sleep(Math.max(0, Math.min(ms, 15000)) / 1000);
        target = `${ms}ms`;

      } else if (op === 'wait_for_ref') {
        const timeout = Number(rawOp.timeout_ms || 8000) / 1000;
        const deadline = driver.now() + timeout;
        let found = false;
        while (driver.now() < deadline) {
          const checked = await guard(ref, false);
          if (checked.ok) { found = true; break; }
          await sleep(0.05);
        }
        if (!found) throw new StaleError(`ref ${ref} did not become ready within ${timeout}s`);

      } else if (op === 'wait_for_text') {
        const needle = String(rawOp.text || '');
        if (!needle) throw new TypeError("wait_for_text needs 'text'");
        const timeout = Number(rawOp.timeout_ms || 8000) / 1000;
        const deadline = driver.now() + timeout;
        let found = false;
        while (driver.now() < deadline) {
          const body = await safeEval("document.body ? document.body.innerText : ''");
          if (body && body.toLowerCase().includes(needle.toLowerCase())) { found = true; break; }
          await sleep(0.08);
        }
        if (!found) throw new StaleError(`text '${needle}' never appeared`);

      } else if (op === 'wait_for_load') {
        if (!dryRun) await waitLoaded(Number(rawOp.timeout_ms || 20000) / 1000);

      } else if (op === 'screenshot') {
        if (dryRun) return stepOf({ op, ok: true, detail: 'dry run' });
        // One call, and deliberately no retry, which is a difference from the server: `_capture`
        // there retries once when `Page.captureScreenshot` stalls instead of failing, because CI
        // intermittently times that command out. That stall is a headless-Chrome behaviour -- the
        // page reports itself visible either way, it reproduces on Chrome 152 and not 153, and it
        // hit one matrix entry out of three on the same runner image -- and this runs in the tab a
        // person is looking at, so there is nothing here to reproduce. Recorded rather than added
        // to the divergence list because that list feeds the parity harness, which has no
        // screenshot case: the extension has no disk to write to, so this op's report already
        // differs from the server's in what it puts in `target`.
        const shot = await driver.call('Page.captureScreenshot', {
          format: rawOp.format === 'png' ? 'png' : 'jpeg',
          captureBeyondViewport: Boolean(rawOp.full),
          ...(rawOp.format === 'png' ? {} : { quality: 80 }),
        });
        const bytes = Math.round(String(shot.data || '').length * 0.75);
        // There is no disk to write to, so the newest shot rides back on the payload and the popup
        // shows it. A replayer you cannot watch is worth being able to look at afterwards.
        state.screenshot = `data:image/${rawOp.format === 'png' ? 'png' : 'jpeg'};base64,`
          + `${shot.data}`;
        target = `${Math.round(bytes / 1024)} KB`;

      } else if (op === 'eval') {
        if (!allowJs) {
          // The server refuses this with a ValueError and reports `invalid_request`, so this does
          // too. `blocked_by_policy` is for the domain envelope, where the sentence is about what
          // the caller asked for rather than about a switch being off.
          throw new TypeError(
            'eval is disabled in the extension; it has no setting to enable it');
        }
        const expression = String(rawOp.js || '');
        if (!expression) throw new TypeError("eval needs 'js'");
        if (dryRun) return stepOf({ op, ok: true, detail: 'dry run' });
        target = cpSlice(JSON.stringify(await safeEval(expression)), 200);

      } else if (op === 'tab') {
        throw new TypeError(
          'tab actions are not available in the extension, which drives the one tab it was '
          + 'pointed at');

      } else {
        throw new TypeError(`unknown op '${op}'`);
      }
    } catch (error) {
      // `browser.py`'s `_run_op` catches PageStale, CdpError, SafetyError and ValueError, and lets
      // everything else out. Same four here, with the CDP one arriving tagged by the driver because
      // a lost connection has to be a reported step rather than a thrown exception.
      if (error instanceof StaleError || error instanceof PolicyError
          || error instanceof TypeError || (error && error.browserError)) {
        return stepOf({ op, ref, target, ok: false, error: errorCode(error),
          detail: brief(error), ms: millis() });
      }
      throw error;
    }

    return stepOf({ op, ref, target, ok: true, ms: millis() });
  }

  /** `Session.act` — run a batch, loosening the guard after the first op that lands. */
  async function act(ops, { dryRun = false, stopOnError = true, observeAfter = true } = {}) {
    if (!Array.isArray(ops) || !ops.length) {
      throw new TypeError('act() needs a non-empty list of ops');
    }
    if (state.last === null) await observe();

    const results = [];
    let strict = state.fresh;
    for (const rawOp of ops) {
      const step = await runOp(rawOp, { dryRun, strict });
      results.push(step);
      state.history.push(step);
      if (step.ok && !dryRun) {
        strict = false;
        state.fresh = false;
      }
      if (!step.ok && stopOnError) break;
    }

    const payload = {
      ops: results,
      ok: results.every((step) => step.ok),
      steps: state.history.length,
    };
    if (observeAfter && !dryRun) {
      let observation = null;
      try {
        observation = await observe();
      } catch (error) {
        if (!(error instanceof StaleError)) throw error;
      }
      if (observation) {
        const previous = observation.previous;
        const changed = previous === null || observation.rawDigest !== previous.rawDigest;
        state.noChangeStreak = changed ? 0 : state.noChangeStreak + 1;
        payload.page_changed = changed;
        payload.view = renderObservation(observation, {
          previous, mode: 'auto', includeText, maxText: maxRenderText,
        });
      }
    }
    if (state.noChangeStreak >= 3) {
      payload.stuck = 'Three consecutive actions changed nothing. Do not retry the same ref: '
        + 're-read the observation, look for a covering dialog, or change strategy.';
    }
    if (state.screenshot) {
      payload.screenshot = state.screenshot;
      state.screenshot = null;
    }
    return payload;
  }

  /**
   * Replay a stored macro: navigate to where it started, then resolve and act.
   *
   * The whole macro is resolved against one observation before anything moves, which is what the
   * server does. `lib/macro.js` refuses rather than guessing, so the failure mode of a page that
   * changed is a refusal sentence with the page untouched, not a click on whatever matched best.
   */
  async function runMacro(macro, params = {}, { threshold = DEFAULT_THRESHOLD, startUrl = '' } = {}) {
    const start = startUrl || (macro && macro.start_url) || '';
    if (start) await navigate(start);
    const observation = await observe();
    const { ops, report } = resolveSteps(
      (macro && macro.steps) || [], observation, params, { threshold });
    const payload = await act(ops, { stopOnError: true, observeAfter: true });
    return { ops, report, payload };
  }

  /**
   * Whether the attachment this session believes it has is still answering.
   *
   * A service worker can be terminated with an attachment still in place, so "I hold a session for
   * this tab" is a claim worth one cheap command rather than a fact worth trusting.
   */
  async function probe() {
    try {
      await driver.evaluate('1', { timeout: 2 });
      return true;
    } catch (_) {
      return false;
    }
  }

  return {
    attach, detach, observe, act, runMacro, navigate, ensureHelper, probe,
    resolveRef, checkUrl, guard, errorCode,
    get last() { return state.last; },
    get history() { return state.history; },
    get sequence() { return state.sequence; },
  };
}
