#!/usr/bin/env node
/* Does the JavaScript port render exactly what Python renders?
 *
 * The fixtures were produced by the real renderer (`observe.Observation.render`), so this is a
 * comparison against ground truth rather than against a second hand-written guess. Every case
 * must match character for character, including the truncation ellipsis and the padding.
 *
 *   node chrome-extension/test/render-parity.mjs
 *
 * Exits non-zero on the first mismatch and prints the first differing line, because "the table
 * looks different" is not a useful bug report.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_SECRET_PATTERNS, isSecret, observationFromRaw, renderObservation,
} from '../lib/render.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(readFileSync(join(HERE, 'fixtures.json'), 'utf8'));

const failures = [];
let passed = 0;

function firstDifference(expected, actual) {
  const a = expected.split('\n');
  const b = actual.split('\n');
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    if (a[i] !== b[i]) {
      return [
        `  line ${i + 1}:`,
        `    expected: ${JSON.stringify(a[i])}`,
        `    actual:   ${JSON.stringify(b[i])}`,
      ].join('\n');
    }
  }
  return '  (no differing line — check the trailing newline)';
}

for (const testCase of fixtures.cases) {
  const { options } = testCase;
  const maskSecrets = options.maskSecrets
    ? (name, role) => isSecret(name, role, DEFAULT_SECRET_PATTERNS)
    : null;

  const current = observationFromRaw(testCase.raw, {
    maskSecrets, sequence: options.sequence,
  });
  // Not part of the observer's JSON — `browser.py` injects it after construction, so the harness
  // has to inject it the same way rather than smuggle it through `raw`.
  current.new_tabs = testCase.new_tabs || [];
  const previous = testCase.previous
    ? observationFromRaw(testCase.previous, {
      maskSecrets, sequence: options.sequence - 1,
    })
    : null;

  const actual = renderObservation(current, {
    previous,
    mode: options.mode,
    includeText: options.includeText,
    focus: options.focus,
    maxText: options.maxText,
  });

  if (actual === testCase.expected) {
    passed += 1;
    console.log(`[ok]   ${testCase.name}`);
  } else {
    failures.push(testCase.name);
    console.log(`[FAIL] ${testCase.name} — ${testCase.why}`);
    console.log(firstDifference(testCase.expected, actual));
  }
}

/* The secret mask is a separate implementation from the renderer, and it decides whether a value
 * is printed at all, so it gets its own check against the same patterns the server uses. */
const SECRET_CASES = [
  ['Password', 'textbox', true],
  ['Card number', 'textbox', true],
  ['API key', 'textbox', true],
  ['one-time code', 'textbox', true],
  ['CVV', 'textbox', true],
  ['Search', 'textbox', false],
  ['Passengers', 'combobox', false],
  ['Promo code', 'textbox', false],
  ['', 'password', true],
  ['Search', 'password', true],
];
for (const [name, role, expected] of SECRET_CASES) {
  const actual = isSecret(name, role);
  if (actual === expected) {
    passed += 1;
  } else {
    failures.push(`isSecret(${JSON.stringify(name)}, ${JSON.stringify(role)})`);
    console.log(`[FAIL] isSecret(${JSON.stringify(name)}, ${JSON.stringify(role)}) `
      + `expected ${expected}, got ${actual}`);
  }
}
console.log(`[ok]   isSecret — ${SECRET_CASES.length} name/role pairs`);

console.log('');
if (failures.length) {
  console.log(`${failures.length} failed, ${passed} passed`);
  process.exit(1);
}
console.log(`all ${passed} checks passed (${fixtures.cases.length} render cases + `
  + `${SECRET_CASES.length} secret-mask cases)`);
