/* Macro storage, in `chrome.storage.local`.
 *
 * A macro file written by the server is the unit of exchange, not a new format: `browser_macro`
 * with `action="record_stop"` writes
 *
 *   {"name": ..., "goal": ..., "start_url": ..., "created": ..., "steps": [...]}
 *
 * and that object is what this stores, keyed by name. So a recorded path moves between the two
 * halves by being pasted, and the two never need to agree on an encoder — only on the file the
 * server already writes.
 */

import { TEMPLATE_SOURCE } from './macro.js';

/** The `chrome.storage.local` key the macros live under, shared with the popup. */
export const STORAGE_KEY = 'jevMacros';

const KEY = STORAGE_KEY;

/** Every `{{name}}` a macro would want filled in, in the order it first appears. */
export function placeholdersOf(macro) {
  const found = [];
  const scan = (value) => {
    if (typeof value !== 'string') return;
    const pattern = new RegExp(TEMPLATE_SOURCE, 'g');
    let match = pattern.exec(value);
    while (match) {
      if (!found.includes(match[1])) found.push(match[1]);
      match = pattern.exec(value);
    }
  };
  for (const step of (macro && macro.steps) || []) {
    scan(step.text);
    scan(step.value);
    scan(step.url);
    for (const path of step.paths || []) scan(path);
  }
  return found;
}

/** The shape a macro has to have to be replayable. Throws with the reason when it does not. */
export function validateMacro(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('a macro has to be an object');
  }
  if (!Array.isArray(value.steps)) {
    throw new TypeError("a macro needs a 'steps' array");
  }
  for (const [index, step] of value.steps.entries()) {
    if (!step || typeof step !== 'object' || Array.isArray(step)) {
      throw new TypeError(`step ${index + 1} is not an object`);
    }
    if (!step.op) throw new TypeError(`step ${index + 1} has no 'op'`);
  }
  return value;
}

/**
 * Read a macro out of pasted text.
 *
 * Two things can be pasted and both are accepted: the file the server writes, and the bare array of
 * steps, which is what `browser_macro action="inspect"` shows and what a person is likely to have on
 * the clipboard.
 */
export function parseMacro(text, fallbackName = '') {
  let parsed;
  try {
    parsed = JSON.parse(String(text || ''));
  } catch (error) {
    throw new TypeError(`that is not JSON: ${error.message}`);
  }
  if (Array.isArray(parsed)) parsed = { steps: parsed };
  const macro = validateMacro(parsed);
  return {
    name: macro.name || fallbackName || 'imported',
    goal: macro.goal || '',
    start_url: macro.start_url || '',
    created: macro.created || new Date().toISOString().slice(0, 19),
    steps: macro.steps,
  };
}

async function all() {
  const stored = await chrome.storage.local.get(KEY);
  return stored[KEY] && typeof stored[KEY] === 'object' ? stored[KEY] : {};
}

export async function listMacros() {
  const macros = await all();
  return Object.values(macros)
    .map((macro) => ({
      name: macro.name,
      goal: macro.goal || '',
      start_url: macro.start_url || '',
      created: macro.created || '',
      steps: (macro.steps || []).length,
      placeholders: placeholdersOf(macro),
    }))
    .sort((left, right) => String(left.name).localeCompare(String(right.name)));
}

export async function getMacro(name) {
  const macros = await all();
  const macro = macros[name];
  if (!macro) throw new TypeError(`No macro named '${name}'`);
  return macro;
}

export async function saveMacro(macro) {
  validateMacro(macro);
  if (!macro.name) throw new TypeError('a macro needs a name to be stored under');
  const macros = await all();
  macros[macro.name] = macro;
  await chrome.storage.local.set({ [KEY]: macros });
  return macro;
}

export async function deleteMacro(name) {
  const macros = await all();
  if (!(name in macros)) return false;
  delete macros[name];
  await chrome.storage.local.set({ [KEY]: macros });
  return true;
}

/** The names in storage, for `chrome.storage.onChanged` to compare against. */
export function macroNames(macros) {
  return Object.keys(macros || {}).sort();
}
