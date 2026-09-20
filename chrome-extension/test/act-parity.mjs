/* Hold the execution-layer port to fixtures the real Python code wrote.
 *
 * `macro-parity.mjs` checks the scoring rules; this checks the other half of a replay — the tables
 * that become keystrokes, and the three rules that decide whether a step is allowed to run at all.
 * Both are places where being wrong does not throw: a bad key code types the wrong character, and a
 * bad confirmation rule clicks the button it was supposed to stop at. `assertions.py` and the
 * reports cannot see either, so the fixtures are the only thing that would.
 *
 *   node chrome-extension/test/act-parity.mjs
 *
 * Regenerate the fixtures with
 *   .venv/bin/python chrome-extension/test/make_act_fixtures.py
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  CLICKABLE_KINDS, DEFAULT_CONFIRM_PATTERNS, HELPER_VERSION, KEY_SPECS, MODIFIERS, NAV_KINDS,
  REQUIRES_REF, SCROLLABLE_REASONS, checkUrl, confirmReason, createSession, pythonInt, resolveRef,
} from '../lib/session.js';
import { DEFAULT_SECRET_PATTERNS, isSecret } from '../lib/render.js';
import { renderAct, renderMacroReplay } from '../lib/report.js';
import { DEFAULT_THRESHOLD, pythonFloat, round3 } from '../lib/macro.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = JSON.parse(readFileSync(join(HERE, 'act-fixtures.json'), 'utf8'));

let passed = 0;
const failures = [];

function check(name, ok, detail = '') {
  if (ok) {
    passed += 1;
  } else {
    failures.push(`${name}  — ${detail}`);
  }
}

/** Run an expression that is expected to raise, and hand back the message instead. */
function messageOf(run) {
  try {
    run();
  } catch (error) {
    return String(error.message ?? error);
  }
  return null;
}

/** Key order is not part of a report, so it is not part of the comparison. */
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function same(actual, expected) {
  return JSON.stringify(canonical(actual)) === JSON.stringify(canonical(expected));
}

/* --- the tables ---------------------------------------------------------------------------- */

const tables = FIXTURES.tables;

check('HELPER_VERSION', HELPER_VERSION === tables.helper_version,
  `${HELPER_VERSION} vs Python's ${tables.helper_version}`);
check('DEFAULT_THRESHOLD', DEFAULT_THRESHOLD === tables.threshold,
  `${DEFAULT_THRESHOLD} vs Python's ${tables.threshold}`);
check('MODIFIERS', same(MODIFIERS, tables.modifiers));
check('KEY_SPECS', same(
  Object.fromEntries(Object.entries(KEY_SPECS).map(([name, spec]) => [name, [...spec]])),
  tables.keys));
check('CLICKABLE_KINDS', same([...CLICKABLE_KINDS].sort(), tables.clickable_kinds));
check('NAV_KINDS', same([...NAV_KINDS].sort(), tables.nav_kinds));
check('REQUIRES_REF', same([...REQUIRES_REF].sort(), tables.requires_ref));
check('SCROLLABLE_REASONS', same([...SCROLLABLE_REASONS].sort(), tables.scrollable_reasons));
check('DEFAULT_CONFIRM_PATTERNS', same(DEFAULT_CONFIRM_PATTERNS, tables.confirm_patterns));
check('DEFAULT_SECRET_PATTERNS', same(DEFAULT_SECRET_PATTERNS, tables.secret_patterns));

/* --- the three rules ----------------------------------------------------------------------- */

for (const item of FIXTURES.helpers.confirm) {
  const label = `${JSON.stringify(item.name)} as ${item.role || '(no role)'}`;
  check(`confirm  ${label}`,
    confirmReason(item.name, item.role) === item.confirm,
    `${JSON.stringify(confirmReason(item.name, item.role))} vs ${JSON.stringify(item.confirm)}`);
  check(`secret   ${label}`,
    isSecret(item.name, item.role) === item.secret,
    `${isSecret(item.name, item.role)} vs ${item.secret}`);
}

for (const item of FIXTURES.helpers.url) {
  const label = `${item.url} allow=${JSON.stringify(item.allow)} deny=${JSON.stringify(item.deny)}`;
  const raised = messageOf(() => checkUrl(item.url, {
    allowDomains: item.allow, denyDomains: item.deny,
  }));
  check(`url      ${label}`,
    raised === (item.error ?? null),
    raised === null ? 'refused nothing' : `${JSON.stringify(raised)} vs ${JSON.stringify(item.error)}`);
}

for (const item of FIXTURES.helpers.int) {
  let actual = null;
  try {
    actual = pythonInt(item.value);
  } catch (error) {
    actual = { error: String(error.message) };
  }
  const expected = item.error === undefined
    ? item.int
    : { error: item.error };
  check(`int      ${JSON.stringify(item.value)}`,
    same(actual, expected),
    `${JSON.stringify(actual)} vs ${JSON.stringify(expected)}`);
}

for (const item of FIXTURES.helpers.ref) {
  const [element, option] = resolveRef(item.ref);
  check(`ref      ${item.ref}`,
    element === item.element && option === item.option,
    `[${element}, ${JSON.stringify(option)}] vs `
    + `[${item.element}, ${JSON.stringify(item.option)}]`);
}

/* The score as the replay header writes it. `round3` is what the resolver stores, `pythonFloat` is
 * what prints it, and Python's own `str(round(v, 3))` is what the fixture holds them both to. The
 * integral score is the case that matters: the file says `1.0`, JSON hands this side `1`, and the
 * server writes `1.0`. A raw template literal would print `1` and the report would stop being
 * diffable against `browser_macro`'s reply.
 */
for (const item of FIXTURES.helpers.float) {
  const actual = pythonFloat(round3(item.value));
  check(`float    ${item.expected}`,
    actual === item.expected,
    `${JSON.stringify(actual)} vs ${JSON.stringify(item.expected)}`);
}

/* And that the header actually reaches for it, rather than the primitive merely existing. The
 * wording of the header is the live check's business; this is the arithmetic. */
const headerWithIntegralScore = renderMacroReplay('m', 1,
  [{ step: 1, op: 'type', ref: 'e4', name: 'Where from?', score: 1.0 }],
  { ok: true, steps: 1, ops: [{ op: 'type', ref: 'e4', ok: true, ms: 1 }] });
check('the replay header prints a perfect match as 1.0, not 1',
  headerWithIntegralScore.split('\n')[1] === "  step 1 type → e4 'Where from?' (1.0)",
  JSON.stringify(headerWithIntegralScore.split('\n')[1]));

/* --- the op dispatcher ---------------------------------------------------------------------
 *
 * The same script as `ScriptedCdp` in make_act_fixtures.py, answering the same expressions with the
 * same rules, so that one op can be run through both dispatchers and the reports compared. What is
 * being compared is not "does it click" — no browser is involved — but the decisions: which op reads
 * a ref, which one demands confirmation, which one parses its argument as an int, and which
 * refusals are refusals rather than errors. Those are the things a reply cannot show, because a
 * dispatcher that gets one wrong still reports `ok`.
 */

const FIXTURE_READ = JSON.stringify({
  url: 'https://example.com/', title: 'T', text: '',
  scroll: { y: 0, height: 800 }, reachable: 0, actions: [], omitted: 0, offscreen: 0,
  overlays: [], cross_frames: 0, cross_frame_srcs: [], digest: 'd', page_key: 'k',
});

/** `reply_for` in make_act_fixtures.py, rule for rule and in the same order. */
function replyFor(script, expression) {
  if (expression.includes('verify(') || expression.includes('reinspect(')) return script.guard;
  if (expression.includes('resolve(')) return { ok: true, x: 10, y: 20 };
  if (expression.includes('label(')) return script.label;
  if (expression.includes('selectOption(')) return script.select;
  if (expression.includes('__jevRefs.nodes.get(')) return false;
  if (expression.includes('readyState')) return 'complete';
  if (expression.includes('innerText')) return 'some body text';
  if (expression.includes('innerWidth')) return 800;
  if (expression.includes('innerHeight')) return 600;
  if (expression.includes('settle(')) return null;
  // The one reply Python's fixture does not need, because it drives `_run_op` directly while this
  // goes through `act`, which reads the page first.
  if (expression.includes('readState(')) return FIXTURE_READ;
  return null;
}

function scriptedDriver(scenario) {
  const given = scenario.script || {};
  const script = {
    guard: given.guard || { ok: true },
    label: given.label || 'Search',
    select: given.select || { ok: true, value: '3', label: '3 adults' },
    calls: [],
  };
  return {
    script,
    async attach() {},
    async detach() {},
    async call(method, params) {
      script.calls.push({ method, params: params || {} });
      return method === 'Target.getTargets' ? { targetInfos: [] } : {};
    },
    async evaluate(expression) {
      return replyFor(script, expression);
    },
    sleep: async () => {},
    now: () => 0,
  };
}

/** The params the extension sources differently, left out of the comparison. */
const NOT_COMPARED = new Set(['x', 'y']);

function comparable(calls) {
  return calls.map(({ method, params }) => ({
    method,
    params: Object.fromEntries(
      Object.entries(params).filter(([key]) => !NOT_COMPARED.has(key))),
  }));
}

for (const scenario of FIXTURES.scenarios) {
  const label = `${JSON.stringify(scenario.op.op)}  (${scenario.why})`;
  const driver = scriptedDriver(scenario);
  const session = createSession(driver, { platform: 'mac', helperSource: '/* fixture */' });
  let actual;
  try {
    await session.attach();
    await session.observe();
    // Only the op's own commands are compared: attaching and reading the page are setup, and on
    // Python's side the fixture calls `_run_op` with no setup at all.
    driver.script.calls.length = 0;
    const payload = await session.act([scenario.op], {
      dryRun: Boolean(scenario.dry_run), observeAfter: false,
    });
    actual = { step: payload.ops[0], calls: comparable(driver.script.calls) };
  } catch (error) {
    actual = { threw: String(error.message || error) };
  }

  const wanted = scenario.expected;
  if (actual.threw) {
    check(`dispatch  ${label}`, false, `threw ${actual.threw}`);
    continue;
  }
  // The report's shape includes a stopwatch, which is the one field a fixture cannot carry: it is a
  // measurement, so leaving it in would make the fixture differ from its own generator every run.
  // Asserted here instead, where it costs nothing and still fails if the field disappears.
  check(`report declares ms  ${label}`,
    Number.isInteger(actual.step.ms), `ms is ${JSON.stringify(actual.step.ms)}`);
  const stepSansMs = { ...actual.step };
  delete stepSansMs.ms;
  const wantedSansMs = { ...wanted.step };
  delete wantedSansMs.ms;
  check(`dispatch  ${label}`,
    same(stepSansMs, wantedSansMs),
    `${JSON.stringify(stepSansMs)} vs ${JSON.stringify(wantedSansMs)}`);
  check(`commands  ${label}`,
    same(actual.calls, wanted.calls),
    `${JSON.stringify(actual.calls)} vs ${JSON.stringify(wanted.calls)}`);
}

/* The divergences, asserted to be the ones written down rather than discovered by a reader. Each
 * one is a place the extension does something the server does not, so "they agree" would be the
 * wrong answer — what has to hold is that the difference is the documented one. */
for (const item of FIXTURES.divergences) {
  const label = `divergence  ${JSON.stringify(item.op.op)} — ${item.field}`;
  const driver = scriptedDriver(item);
  const session = createSession(driver, { platform: 'mac', helperSource: '/* fixture */' });
  await session.attach();
  await session.observe();
  driver.script.calls.length = 0;
  const payload = await session.act([item.op], { observeAfter: false });
  const step = payload.ops[0];
  const summary = step.ok ? `ok  ${JSON.stringify(step.target || '')}` : `invalid_request  ${JSON.stringify(step.detail || step.error)}`;

  if (item.op.op === 'scroll') {
    const wheel = driver.script.calls.find((call) => call.params.type === 'mouseWheel');
    check(label, Boolean(wheel) && step.ok,
      `expected a wheel event at the tab's own viewport centre, got ${JSON.stringify(wheel)}`);
    check('divergence  the wheel goes to the viewport centre, not a configured window',
      wheel && wheel.params.x === 400 && wheel.params.y === 300,
      `${JSON.stringify(wheel && wheel.params)} is not 400,300`);
  } else if (item.op.op === 'upload') {
    check(label, !step.ok && step.error === 'invalid_request' && /cannot read/.test(step.detail || ''),
      summary);
  } else if (item.op.op === 'tab') {
    check(label, !step.ok && step.error === 'invalid_request' && /not available/.test(step.detail || ''),
      summary);
  } else if (item.op.op === 'eval') {
    check(label, !step.ok && step.error === 'invalid_request' && /no setting/.test(step.detail || ''),
      summary);
  } else {
    check(label, false, `no expectation was written for ${item.op.op}`);
  }
}

/* --- the report writers -------------------------------------------------------------------------
 *
 * The sentence a person reads to find out what a run did, held character for character to the one
 * `server.py::_render_act` writes. A report that is merely similar is a report that cannot be
 * diffed against `browser_act`'s output, and diffing them is how "did the extension do what the
 * server did" gets answered — including by scripts/extension_check.py, which compares the extension's
 * whole report against the real MCP tool's reply.
 */

for (const item of FIXTURES.renders) {
  const actual = renderAct(item.payload);
  check(`report     ${item.why}`, actual === item.expected,
    `${JSON.stringify(actual)} vs ${JSON.stringify(item.expected)}`);
}

/* --- the summary --------------------------------------------------------------------------- */

const total = passed + failures.length;
for (const failure of failures) console.log(`[FAIL] ${failure}`);
if (!failures.length) {
  const helpers = FIXTURES.helpers;
  console.log(`all ${total} checks passed (${helpers.confirm.length} names x2, `
    + `${helpers.url.length} urls, ${helpers.int.length} ints, ${helpers.ref.length} refs, `
    + `${helpers.float.length} score floats + the header, `
    + `${FIXTURES.scenarios.length} dispatched ops x2 (report + commands), `
    + `${FIXTURES.renders.length} rendered reports, `
    + `${FIXTURES.divergences.length} divergences, 10 frozen tables)`);
} else {
  console.log(`${failures.length} of ${total} checks failed`);
  process.exitCode = 1;
}
