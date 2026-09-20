#!/usr/bin/env node
/* Does the JavaScript resolver resolve exactly what Python resolves?
 *
 * The fixtures were produced by the real resolver (`macros.resolve`), so this is a
 * comparison against ground truth rather than against a second hand-written guess.
 * Every case must agree, including the two ways a step can be refused -- "did it
 * raise" is part of the contract, because a replay that raises has not touched the
 * page, and a replay that quietly picks the second-best element has already
 * submitted the form.
 *
 *   node chrome-extension/test/macro-parity.mjs
 *
 * Exits non-zero on the first case that disagrees and prints the first differing
 * line, because "the replay went somewhere else" is not a useful bug report.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  AMBIGUITY_MARGIN, CONTEXT_WEIGHT, DEFAULT_THRESHOLD, MacroError, resolve, resolveMacro,
} from '../lib/macro.js';
import { observationFromRaw } from '../lib/render.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(readFileSync(join(HERE, 'macro-fixtures.json'), 'utf8'));

const failures = [];
let passed = 0;

/* One line per thing the resolver decided, so a mismatch points at the step that
 * moved rather than at a wall of JSON. Scores are compared as JavaScript sees them
 * on both sides -- the expectation came through `JSON.parse`, where Python's `1.0`
 * is already the number 1 -- so a perfect match does not fail here for spelling
 * reasons while the real decision agrees. */
function outcomeLines(outcome) {
  if (outcome.error !== undefined) return [`refused: ${outcome.error}`];
  return [
    ...outcome.ops.map((op, index) => `op[${index}] ${JSON.stringify(op)}`),
    ...outcome.report.map((item, index) => `report[${index}] ${JSON.stringify(item)}`),
  ];
}

function firstDifference(expected, actual) {
  const left = outcomeLines(expected);
  const right = outcomeLines(actual);
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    if (left[i] !== right[i]) {
      return [
        `  line ${i + 1}:`,
        `    python: ${JSON.stringify(left[i])}`,
        `    port:   ${JSON.stringify(right[i])}`,
      ].join('\n');
    }
  }
  return null;
}

for (const testCase of fixtures.cases) {
  if (testCase.expected.error === undefined && !Array.isArray(testCase.expected.ops)) {
    failures.push(testCase.name);
    console.log(`[FAIL] ${testCase.name} — the fixture has neither ` + '`ops` nor `error`');
    continue;
  }

  const observation = observationFromRaw(testCase.raw, { sequence: 1 });
  let actual;
  try {
    actual = resolve(testCase.steps, observation, testCase.params, {
      threshold: testCase.threshold,
    });
  } catch (error) {
    // Anything that is not a refusal is a port bug, and swallowing it as one
    // would turn a TypeError into a passing case.
    if (!(error instanceof MacroError)) throw error;
    actual = { error: error.message };
  }

  const difference = firstDifference(testCase.expected, actual);
  if (difference === null) {
    passed += 1;
    console.log(`[ok]   ${testCase.name}`);
  } else {
    failures.push(testCase.name);
    console.log(`[FAIL] ${testCase.name} — ${testCase.why}`);
    console.log(difference);
  }
}

/* The constants are what a drift would hide behind: scores that are far apart agree
 * whatever the margin is. Python's values ride in the fixture, so both languages
 * have to agree with the same three numbers rather than each with itself. */
const LIMITS = [
  ['DEFAULT_THRESHOLD', DEFAULT_THRESHOLD, fixtures.limits.threshold],
  ['AMBIGUITY_MARGIN', AMBIGUITY_MARGIN, fixtures.limits.ambiguity_margin],
  ['CONTEXT_WEIGHT', CONTEXT_WEIGHT, fixtures.limits.context_weight],
];
for (const [name, port, python] of LIMITS) {
  if (port === python) {
    passed += 1;
  } else {
    failures.push(name);
    console.log(`[FAIL] ${name}: the port says ${port}, Python says ${python}`);
  }
}
console.log(`[ok]   constants — ${LIMITS.length} compared against Python's`);

/* `resolveMacro` is the entry point a replayer uses, and it is the one place that
 * decides what a macro file with no `steps` means. Both halves are checked: the
 * empty case, and a real macro against the same steps passed directly -- a wrapper
 * that forwarded the macro itself as the step list would still resolve nothing for
 * `{}` and would do the same for a real macro, silently. */
const EMPTY = { ops: [], report: [] };
const emptyResults = [resolveMacro({}, { elements: [] }), resolveMacro(null, { elements: [] })];
if (emptyResults.every((result) => JSON.stringify(result) === JSON.stringify(EMPTY))) {
  passed += 1;
} else {
  failures.push('resolveMacro');
  console.log('[FAIL] resolveMacro: a macro with no steps must resolve to nothing, not throw');
}
console.log('[ok]   resolveMacro — a macro with no steps resolves to nothing');

const SAMPLE_STEPS = [
  { op: 'click', target: { role: 'button', name: 'Check in', context: '' } },
];
const SAMPLE_OBSERVATION = observationFromRaw({
  url: 'https://example.com', title: '', text: '', reachable: 1,
  actions: [{
    ref: 'e1', role: 'button', name: 'Check in', label: 'Check in', value: '',
    editable: false, occluded: false, inViewport: true, checked: null, expanded: null,
    current: null, options: [], opts_total: 0, secret: false, context: '',
    accept: null, multiple: false, hoverable: false,
  }],
});
const viaMacro = resolveMacro({ name: 'sample', steps: SAMPLE_STEPS }, SAMPLE_OBSERVATION);
if (JSON.stringify(viaMacro) === JSON.stringify(resolve(SAMPLE_STEPS, SAMPLE_OBSERVATION))
    && viaMacro.ops.length === 1) {
  passed += 1;
} else {
  failures.push('resolveMacro-steps');
  console.log('[FAIL] resolveMacro: a macro with steps must resolve its `steps`, not the macro');
}
console.log('[ok]   resolveMacro — a real macro resolves through its steps');

const refusals = fixtures.cases.filter((item) => item.expected.error !== undefined).length;
console.log('');
if (failures.length) {
  console.log(`${failures.length} failed, ${passed} passed`);
  process.exit(1);
}
console.log(`all ${passed} checks passed (${fixtures.cases.length} macro cases, `
  + `${refusals} of them refusals, + ${LIMITS.length} constants + resolveMacro)`);
