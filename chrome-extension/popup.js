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
 */

import {
  DEFAULT_SECRET_PATTERNS, isSecret, observationFromRaw, renderObservation,
} from './lib/render.js';

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
};

/* One popup session is one conversation with the page. `previous` is what lets the second
 * Observe render a delta, which is how the server behaves; a fresh popup starts from the full
 * table because the popup's module state does not survive closing it. */
let sequence = 0;
let previous = null;
let rendered = '';
let tabLabel = '';

function setNote(message, { error = false } = {}) {
  ui.note.textContent = message;
  ui.note.classList.toggle('error', error);
  ui.note.hidden = !message;
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

ui.observe.addEventListener('click', observe);

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

// Restore the toggle, then take the first read so the popup is useful the moment it opens.
(async () => {
  const stored = await chrome.storage.local.get(PREF_KEY);
  // Defaults to on, because the server includes page text in what it hands the model. The popup's
  // first read should be the server's view of the page, and only differ when you ask it to.
  ui.text.checked = stored[PREF_KEY] === undefined ? true : Boolean(stored[PREF_KEY]);
  await observe();
})();
