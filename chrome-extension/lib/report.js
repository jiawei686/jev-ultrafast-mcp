/* The two report writers, ported from `server.py`.
 *
 * `_render_act` and the header `browser_macro` prints for a replay are the sentences a person reads
 * to find out what a run did. They exist in one place in the server, so they exist in one place here
 * — a popup that formatted its own summary would be a third opinion about a run, and the first two
 * are already compared character for character by `test/act-parity.mjs`.
 *
 * That comparison is why this is a port and not a nicer version. `lib/render.js` is held to
 * `observe.py`; this is held to `server.py`, and `test/report-parity` fixtures freeze both writers'
 * output for the port to match.
 */

import { pythonFloat, pythonRepr, pythonStr } from './macro.js';

/* The arrow the server writes between an op and its target. Not decoration: a report that spells it
 * `->` is a report a reader cannot diff against `browser_act`'s output, and diffing them is the only
 * way to answer "did the extension do what the server did". */
const ARROW = '\u2192';

/** Python's `dict.get(key, default)` on a possibly-None value, which is not what `??` does. */
function fieldOf(source, key, fallback) {
  if (!Object.prototype.hasOwnProperty.call(source, key)) return fallback;
  return pythonStr(source[key]);
}

/** `server.py::_render_act`. */
export function renderAct(payload = {}, { verbose = false } = {}) {
  const lines = [];
  const ops = payload.ops || [];
  const okCount = ops.filter((op) => op.ok).length;
  lines.push(`${okCount}/${ops.length} ops ok` + (payload.ok ? '' : '  (stopped early)'));

  for (const op of ops) {
    const ref = op.ref || '';
    const target = op.target || '';
    let label = `${fieldOf(op, 'op', '')} ${ref}`.trim();
    if (target) label += ` ${ARROW} ${target}`;
    if (op.ok) {
      const detail = op.detail ? `  [${op.detail}]` : '';
      lines.push(`  + ${label}  ${op.ms === undefined ? 0 : op.ms}ms${detail}`);
    } else {
      lines.push(`  x ${label}  ${fieldOf(op, 'error', '')}: ${fieldOf(op, 'detail', '')}`);
    }
  }
  if (payload.stuck) lines.push(`! ${payload.stuck}`);
  if (payload.view) {
    lines.push('');
    lines.push(payload.view);
  }
  if (verbose) {
    lines.push('');
    lines.push(`(steps this session: ${payload.steps === undefined ? 0 : payload.steps})`);
  }
  return lines.join('\n');
}

/** The header `server.py::browser_macro` prints before `_render_act` for `action="run"`.
 *
 * The score goes through `pythonFloat` rather than straight into the template
 * because it is a float on the server's side and a bare number on this one: a
 * perfect match is `1.0` there and `1` here, and this was the line where a real
 * replay first disagreed with the tool's own reply.
 */
export function renderMacroReplay(name, totalSteps, report, payload) {
  const lines = [`replayed ${pythonRepr(name)}: resolved ${report.length} steps from `
    + `${totalSteps}`];
  for (const item of report) {
    lines.push(`  step ${item.step} ${item.op}`
      + (item.ref
        ? ` ${ARROW} ${item.ref} ${pythonRepr(item.name)} (${pythonFloat(item.score)})`
        : ''));
  }
  return `${lines.join('\n')}\n\n${renderAct(payload)}`;
}
