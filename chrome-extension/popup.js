/* The popup.
 *
 * It does exactly what the server does, in the same order:
 *
 *   1. inject `lib/observer.js` — a byte-identical copy of `jev_ultrafast_mcp/js/observer.js`,
 *      so the refs, the geometry and the occlusion decisions are the server's, not a reimplementation;
 *   2. call `readState()` once, which is the same atomic read the server's `browser_observe` uses;
 *   3. turn that JSON into an `Observation` and render it with `lib/render.js`, the port of
 *      `observe.py` that `test/render-parity.mjs` holds to the real Python renderer.
 *
 * The point is to be a window onto the table rather than a second opinion about it, so nothing here
 * decides what an element looks like. If this popup and the server ever disagree, the parity test
 * fails rather than a user noticing.
 *
 * The Replay panel is the one thing the reading half cannot do. It hands a macro to the service
 * worker, which owns the debugger; `lib/session.js` does the observing, the resolving and the
 * dispatching, and all this does is say which macro and print what came back — in the server's own
 * vocabulary, because `chrome-extension/README.md` promises the report is the one the server would
 * have given.
 */

import {
  DEFAULT_SECRET_PATTERNS, isSecret, observationFromRaw, renderObservation,
} from './lib/render.js';
import { renderMacroReplay } from './lib/report.js';
import { STORAGE_KEY } from './lib/store.js';

/* `config.Config().max_text`, which `server.py::_view` passes to the renderer. Keeping the
 * extension's number equal to the server's is the difference between the same table and a
 * truncated version of it. */
const MAX_TEXT = 6000;
const PREF_KEY = 'includeText';

const ui = {
  page: document.getElementById('page'),
  observe: document.getElementById('observe'),
  copy: document.getElementById('copy'),
  text: document.getElementById('text'),
  note: document.getElementById('note'),
  table: document.getElementById('table'),
  stats: document.getElementById('stats'),
  select: document.getElementById('macro-select'),
  run: document.getElementById('macro-run'),
  stop: document.getElementById('macro-stop'),
  forget: document.getElementById('macro-forget'),
  params: document.getElementById('macro-params'),
  paste: document.getElementById('macro-paste'),
  macroText: document.getElementById('macro-text'),
  name: document.getElementById('macro-name'),
  save: document.getElementById('macro-save'),
  macroNote: document.getElementById('macro-note'),
  report: document.getElementById('macro-report'),
};

/* One popup session is one conversation with the page. `previous` is what lets the second
 * Observe render a delta, which is how the server behaves; a fresh popup starts from the full
 * table because the popup's module state does not survive closing it. */
let sequence = 0;
let previous = null;
let rendered = '';
let tabLabel = '';
let macros = [];

function setNote(message, { error = false } = {}) {
  ui.note.textContent = message;
  ui.note.classList.toggle('error', error);
  ui.note.hidden = !message;
}

function setMacroNote(message, { error = false, ok = false } = {}) {
  ui.macroNote.textContent = message;
  ui.macroNote.classList.toggle('error', error);
  ui.macroNote.classList.toggle('ok', ok);
  ui.macroNote.hidden = !message;
}

function setTable(text) {
  rendered = text;
  ui.table.textContent = text;
  ui.copy.disabled = !text;
}

/** The injected read. Runs in the page's isolated world, so it can see what we injected there. */
function readStateInPage(options) {
  if (!window.__jevMcp) {
    return { error: 'The observer did not install on this page (it only installs on a top-level window).' };
  }
  return { state: window.__jevMcp.readState(options), stats: window.__jevMcp.stats() };
}

async function observe() {
  ui.observe.disabled = true;
  setNote('');
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || tab.id === undefined) throw new Error('No active tab to observe.');
    const target = { tabId: tab.id };

    await chrome.scripting.executeScript({
      target, files: ['lib/observer.js'], world: 'ISOLATED',
    });
    const [result] = await chrome.scripting.executeScript({
      target,
      world: 'ISOLATED',
      func: readStateInPage,
      args: [{ maxText: MAX_TEXT, includeText: ui.text.checked }],
    });

    const payload = result?.result;
    if (!payload || payload.error) {
      throw new Error(payload?.error || 'The page returned nothing.');
    }

    const raw = JSON.parse(payload.state);
    sequence += 1;
    tabLabel = raw.title || raw.url || '';

    const observation = observationFromRaw(raw, {
      // The observer flags secrets it recognises; this is the second net the server also casts,
      // so a field named "Card number" is hidden here for the same reason it is there.
      maskSecrets: (name, role) => isSecret(name, role, DEFAULT_SECRET_PATTERNS),
      sequence,
    });
    // A delta only makes sense against the same page, which is the rule `Observation.render` uses.
    const prior = previous && previous.url === observation.url ? previous : null;
    previous = observation;

    setTable(renderObservation(observation, {
      previous: prior,
      mode: 'auto',
      includeText: ui.text.checked,
      maxText: MAX_TEXT,
    }));

    const stats = payload.stats || {};
    ui.page.textContent = tabLabel;
    ui.page.title = raw.url;
    ui.stats.textContent = [
      `refs=${stats.refs ?? '?'}`,
      `reachable=${raw.reachable}/${raw.actions.length}`,
      `offscreen=${raw.offscreen}`,
      `omitted=${raw.omitted}`,
    ].join('  ');
    if (prior) setNote(`Showing a delta against the previous read of this page (obs#${sequence - 1}).`);
  } catch (error) {
    const message = String(error?.message || error);
    setTable('');
    ui.stats.textContent = '';
    setNote(
      /Cannot access|chrome:\/\/|extension:\/\/|The extensions gallery/i.test(message)
        ? 'This page does not allow extensions to read it. Try a normal http(s) page.'
        : message,
      { error: true },
    );
  } finally {
    ui.observe.disabled = false;
  }
}

/* ------------------------------------------------------------------ the replay panel */

async function refreshMacros({ keep = '' } = {}) {
  const reply = await chrome.runtime.sendMessage({ type: 'list' });
  macros = (reply && reply.macros) || [];
  const chosen = keep || ui.select.value;
  ui.select.replaceChildren();
  for (const macro of macros) {
    const option = document.createElement('option');
    option.value = macro.name;
    option.textContent = `${macro.name}  (${macro.steps} steps)`;
    option.title = macro.goal || macro.start_url || '';
    ui.select.append(option);
  }
  if (chosen && macros.some((macro) => macro.name === chosen)) ui.select.value = chosen;
  if (!macros.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'no macros yet — paste one below';
    ui.select.append(option);
  }
  syncMacroRow();
}

function selectedMacro() {
  return macros.find((macro) => macro.name === ui.select.value) || null;
}

function syncMacroRow() {
  const macro = selectedMacro();
  ui.run.disabled = !macro;
  ui.forget.disabled = !macro;
  if (!macro) return;
  const wanted = macro.placeholders || [];
  // The parameters the macro actually asks for, named, because a placeholder left unfilled is
  // replaced by `{{name}}` verbatim and typed into the page as those nine characters.
  ui.params.placeholder = wanted.length
    ? `needs ${wanted.map((name) => `"${name}"`).join(', ')} — JSON, e.g. {`
      + wanted.map((name) => `"${name}":"..."`).join(', ') + '}'
    : 'parameters as JSON, e.g. {"query":"shoes"}';
}

/** The step report, which is `server.py::browser_macro`'s output for a replay, verbatim. */
function renderReplay(result) {
  return renderMacroReplay(result.name, result.steps, result.resolved || [], result.payload || {});
}

function setReplayBusy(busy) {
  ui.observe.disabled = busy;
  ui.save.disabled = busy;
  syncMacroRow();
  if (busy) ui.run.disabled = true;
  ui.stop.disabled = !busy;
}

async function runMacro() {
  const name = ui.select.value;
  if (!name) return;

  let params = {};
  const raw = ui.params.value.trim();
  if (raw) {
    try {
      params = JSON.parse(raw);
    } catch (error) {
      setMacroNote(`those parameters are not JSON: ${error.message}`, { error: true });
      return;
    }
    if (!params || typeof params !== 'object' || Array.isArray(params)) {
      setMacroNote('parameters have to be a JSON object like {"query":"shoes"}', { error: true });
      return;
    }
  }

  setReplayBusy(true);
  setMacroNote('Attaching the debugger to this tab. Chrome will show a banner while it is on.');
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || tab.id === undefined) throw new Error('No active tab to replay on.');
    const result = await chrome.runtime.sendMessage({ type: 'replay', tabId: tab.id, name, params });
    if (!result) throw new Error('The service worker gave no answer. Reload the extension.');
    if (result.error) throw new Error(result.error);

    ui.report.textContent = renderReplay(result);
    const failed = (result.payload && result.payload.ops || []).filter((step) => !step.ok);
    if (failed.length) {
      setMacroNote(`The replay stopped at ${failed[0].op}: `
        + `${failed[0].detail || failed[0].error}`, { error: true });
    } else {
      // Read the page again rather than showing the replay's own view. Otherwise the table would be
      // one reader's output and the label and counters another's, and two readings of one page that
      // could disagree is exactly the thing this popup exists not to be.
      await observe();
      setMacroNote(`Replayed '${name}'. The table above is a delta against what was here before.`,
        { ok: true });
    }
  } catch (error) {
    setMacroNote(String(error?.message || error), { error: true });
  } finally {
    setReplayBusy(false);
  }
}

async function detachDebugger() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.id !== undefined) {
      await chrome.runtime.sendMessage({ type: 'detach', tabId: tab.id });
    }
    setMacroNote("Released the debugger, so Chrome's banner is gone.");
  } catch (error) {
    setMacroNote(String(error?.message || error), { error: true });
  }
}

async function savePasted() {
  const text = ui.macroText.value.trim();
  if (!text) {
    setMacroNote('Paste the macro JSON first.', { error: true });
    return;
  }
  try {
    const reply = await chrome.runtime.sendMessage(
      { type: 'import', text, name: ui.name.value.trim() });
    if (!reply || !reply.ok) throw new Error((reply && reply.error) || 'the import failed');
    ui.paste.open = false;
    ui.macroText.value = '';
    ui.name.value = '';
    await refreshMacros({ keep: reply.name });
    setMacroNote(`Saved '${reply.name}' with ${reply.steps} steps.`, { ok: true });
  } catch (error) {
    setMacroNote(String(error?.message || error), { error: true });
  }
}

async function forgetMacro() {
  const name = ui.select.value;
  if (!name) return;
  await chrome.runtime.sendMessage({ type: 'remove', name });
  await refreshMacros();
  setMacroNote(`Forgot '${name}'.`);
}

/* ------------------------------------------------------------------ wiring */

ui.observe.addEventListener('click', observe);
ui.run.addEventListener('click', runMacro);
ui.stop.addEventListener('click', detachDebugger);
ui.save.addEventListener('click', savePasted);
ui.forget.addEventListener('click', forgetMacro);
ui.select.addEventListener('change', () => {
  syncMacroRow();
  setMacroNote('');
});

ui.copy.addEventListener('click', async () => {
  if (!rendered) return;
  try {
    await navigator.clipboard.writeText(rendered);
    setNote('Table copied.');
  } catch (error) {
    setNote(`Could not copy: ${error.message}`, { error: true });
  }
});

ui.text.addEventListener('change', async () => {
  await chrome.storage.local.set({ [PREF_KEY]: ui.text.checked });
});

// The macro list lives in storage so the service worker and the popup agree on it without a
// protocol; a change from either side shows up here.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes[STORAGE_KEY]) refreshMacros();
});

// Restore the toggle, then take the first read so the popup is useful the moment it opens.
(async () => {
  const stored = await chrome.storage.local.get(PREF_KEY);
  // Defaults to on, because the server includes page text in what it hands the model. The popup's
  // first read should be the server's view of the page, and only differ when you ask it to.
  ui.text.checked = stored[PREF_KEY] === undefined ? true : Boolean(stored[PREF_KEY]);
  await refreshMacros();
  await observe();
})();
