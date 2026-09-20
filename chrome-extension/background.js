/* The service worker: the only place that holds the debugger.
 *
 * It lives here rather than in the popup because a popup is destroyed the moment it loses focus,
 * and a replay is a sequence of clicks that outlives a glance at the toolbar. The worker is what
 * survives the popup, owns `chrome.debugger`, and can therefore finish a run that was started by a
 * popup nobody is looking at any more.
 *
 * The driver below is the whole browser surface `lib/session.js` is allowed to touch. Keeping it in
 * one small object is what lets the session be driven without a browser, and what makes the
 * deliberate differences from `browser.py` visible in one place: there is no
 * `Emulation.setDeviceMetricsOverride` here, because the tab being driven is the one the user is
 * looking at, and resizing somebody's window to take a reading of it is not a trade a tool gets to
 * make silently.
 */

import { DEFAULT_SECRET_PATTERNS } from './lib/render.js';
import { createSession, HELPER_VERSION } from './lib/session.js';
import { deleteMacro, getMacro, listMacros, parseMacro, saveMacro } from './lib/store.js';

const PROTOCOL = '1.3';
const HELPER_PATH = 'lib/observer.js';

/* Attachments live as long as this map says so, and are dropped on any evidence to the contrary — a
 * detached event, a closed tab, or a probe that fails. Believing a stale map is how a run ends up
 * "attached" to a tab it cannot reach. */
const sessions = new Map();

let helperSourcePromise = null;

/** The observer, fetched once. `lib/observer.js` is byte-identical to the server's copy. */
function helperSource() {
  if (!helperSourcePromise) {
    helperSourcePromise = fetch(chrome.runtime.getURL(HELPER_PATH)).then((response) => {
      if (!response.ok) throw new Error(`could not read ${HELPER_PATH} (${response.status})`);
      return response.text();
    }).catch((error) => {
      helperSourcePromise = null;
      throw error;
    });
  }
  return helperSourcePromise;
}

/** A CDP failure, tagged so `session.js` reports it as `browser_error` instead of throwing it. */
function browserError(error) {
  const wrapped = new Error((error && error.message) || String(error));
  wrapped.browserError = true;
  return wrapped;
}

function driverFor(tabId) {
  return {
    async attach() {
      try {
        await chrome.debugger.attach({ tabId }, PROTOCOL);
      } catch (error) {
        const message = (error && error.message) || String(error);
        // The common case is worth naming, because the fix is something the user has to do.
        throw new Error(/already attached/i.test(message)
          ? 'Another debugger is already attached to this tab. Close DevTools on it and try again.'
          : message);
      }
    },
    async detach() {
      try {
        await chrome.debugger.detach({ tabId });
      } catch (_) {
        // Already gone is the outcome that was wanted.
      }
    },
    async call(method, params) {
      try {
        return await chrome.debugger.sendCommand({ tabId }, method, params || {});
      } catch (error) {
        throw browserError(error);
      }
    },
    async evaluate(expression, { awaitPromise = false } = {}) {
      const result = await this.call('Runtime.evaluate', {
        expression, returnByValue: true, awaitPromise, userGesture: true,
      });
      if (result && result.exceptionDetails) {
        const details = result.exceptionDetails;
        const description = (details.exception && details.exception.description)
          || details.text || 'javascript error';
        throw new Error(String(description).split('\n')[0].slice(0, 300));
      }
      return result && result.result ? result.result.value : undefined;
    },
    sleep: (ms) => new Promise((resolveDone) => setTimeout(resolveDone, ms)),
    now: () => performance.now() / 1000,
  };
}

async function makeSession(tabId) {
  const [platform, source] = await Promise.all([
    chrome.runtime.getPlatformInfo(),
    helperSource(),
  ]);
  const session = createSession(driverFor(tabId), {
    platform: platform.os === 'mac' ? 'mac' : platform.os,
    helperSource: source,
    secretPatterns: DEFAULT_SECRET_PATTERNS,
    // Empty lists mean "no list", which is what `safety.check_url` reads them as. There is no
    // settings page yet, so the envelope is the default one: refuse only what cannot be parsed.
    allowDomains: [],
    denyDomains: [],
  });
  await session.attach();
  return session;
}

/** The session for a tab, rebuilt when the one held has stopped answering. */
async function liveSession(tabId) {
  const held = sessions.get(tabId);
  if (held && await held.probe()) return held;
  if (held) sessions.delete(tabId);
  const session = await makeSession(tabId);
  sessions.set(tabId, session);
  return session;
}

async function closeSession(tabId) {
  const held = sessions.get(tabId);
  sessions.delete(tabId);
  if (held) await held.detach();
}

chrome.debugger.onDetach.addListener((source) => {
  if (source && source.tabId !== undefined) sessions.delete(source.tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  sessions.delete(tabId);
});

/** Replay a stored macro on a tab, then let go of the debugger. */
async function replay({ tabId, name, params = {} }) {
  const macro = await getMacro(name);
  try {
    const session = await liveSession(tabId);
    const result = await session.runMacro(macro, params);
    return {
      ok: result.payload.ok,
      name: macro.name,
      goal: macro.goal || '',
      start_url: macro.start_url || '',
      steps: (macro.steps || []).length,
      resolved: result.report,
      payload: result.payload,
    };
  } finally {
    // Detach as soon as the run is over. The banner Chrome shows while a debugger is attached is a
    // real cost paid by the person using the browser, and it should last exactly as long as the run.
    await closeSession(tabId);
  }
}

const HANDLERS = {
  ping: async () => ({
    ok: true,
    helper_version: HELPER_VERSION,
    attached: [...sessions.keys()],
  }),
  replay,
  detach: async ({ tabId }) => {
    await closeSession(tabId);
    return { ok: true };
  },
  list: async () => ({ ok: true, macros: await listMacros() }),
  import: async ({ text, name }) => {
    const macro = parseMacro(text, name);
    await saveMacro(macro);
    return { ok: true, name: macro.name, steps: macro.steps.length };
  },
  remove: async ({ name }) => ({ ok: await deleteMacro(name) }),
};

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  // Only this extension may ask. A page cannot reach `chrome.runtime.onMessage` anyway, but the
  // check is what makes that a decision rather than an assumption.
  if (sender.id !== chrome.runtime.id) {
    respond({ ok: false, error: 'not this extension' });
    return false;
  }
  const handler = HANDLERS[message && message.type];
  if (!handler) {
    respond({ ok: false, error: `unknown request '${message && message.type}'` });
    return false;
  }
  Promise.resolve(handler(message)).then(
    (result) => respond(result),
    (error) => respond({ ok: false, error: (error && error.message) || String(error) }),
  );
  return true; // the response is asynchronous
});
