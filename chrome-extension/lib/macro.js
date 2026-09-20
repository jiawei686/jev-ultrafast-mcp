/* The macro resolver, ported from `jev_ultrafast_mcp/macros.py`.
 *
 * A macro is a recorded path stored as *semantics* -- `{role, name, context}`,
 * never a ref and never a selector -- so it survives a reload, a redesign that
 * keeps the labels, and a different browser instance. Replaying one means
 * scoring every element on the page in front of you against those descriptors
 * and refusing to act unless one of them wins clearly.
 *
 * That refusal is the reason this is a port and not a shortcut. Two "Select"
 * buttons that both score 1.0 are exactly the silent misclick the design exists
 * to prevent, so the resolver raises and the run stops with the page untouched.
 * A replay that "usually works" is worse than no replay at all, because the
 * paragraph you did not read is the one that got submitted.
 *
 * Nothing here is re-derived: `norm`, `tokens`, `scoreTarget`, `substitute` and
 * `resolve` are line-for-line ports of `_norm`, `_tokens`, `_score`,
 * `_substitute` and `resolve`, and `test/macro-parity.mjs` holds them to
 * fixtures the real Python resolver wrote. Where JavaScript would quietly
 * diverge from Python -- the truthiness of an empty object, `round` breaking a
 * tie the other way, `str()` of a boolean, which characters count as space --
 * the difference is spelled out in a comment rather than left to the reader.
 *
 * This is the one piece of the replay path with real risk in it. Everything
 * else the extension needs -- `chrome.alarms`, attach/detach, storage -- is
 * written straight off the manual; this is the part where being wrong is
 * invisible until it clicks the wrong thing.
 */

export const DEFAULT_THRESHOLD = 0.7;
export const AMBIGUITY_MARGIN = 0.06;
// Must exceed AMBIGUITY_MARGIN: a distinguishing context is the only thing that
// is allowed to break a tie between two identically-named controls. If it were
// worth less than the margin, adding it could never rescue an ambiguous step,
// which is the one job it has.
export const CONTEXT_WEIGHT = 0.15;

const TEMPLATE = /\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}/g;

/* The pattern without its `g` flag, which is the problem with sharing a global regex: `lastIndex`
 * survives between calls, so the second user silently starts matching from the middle. `store.js`
 * needs it to list the placeholders a macro wants filled in, and a second copy of the pattern here
 * would be a second definition of what a placeholder is. */
export const TEMPLATE_SOURCE = TEMPLATE.source;

/* Roles that behave the same way to a click, so a macro recorded against one
 * still resolves against the other -- at a lower base score, which keeps an
 * exact role match winning when both are on the page. */
const NEAR_ROLES = new Set([
  'button|link', 'link|button', 'textbox|searchbox',
  'searchbox|textbox', 'menuitem|button', 'button|menuitem',
]);

/* Python's `str.split()` with no argument splits on everything `str.isspace()`
 * accepts and drops the empty pieces. JavaScript's `\s` is a *different set*:
 * it counts U+FEFF as space, which Python does not, and omits U+001C-U+001F and
 * U+0085, which Python does. Names containing those are pathological, but
 * spelling the class out costs one line and removes the question -- `norm`
 * decides whether two labels are the same label, and that decision is what
 * every score is built on. */
const PYTHON_SPACE = /[\t\n\v\f\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\u001c-\u001f]+/;

export class MacroError extends Error {
  constructor(message) {
    super(message);
    this.name = 'MacroError';
  }
}

/** Python's `_norm`: lowercase, then collapse every run of space to one space. */
export function norm(text) {
  return String(text ?? '').toLowerCase().split(PYTHON_SPACE).filter(Boolean).join(' ');
}

/** Python's `_tokens`.
 *
 * Note what the character class does to a name that is not ASCII: `[^a-z0-9]`
 * matches CJK outright, so `tokens('签到领奖')` is the *empty set* while
 * `tokens('Check in')` is `{check, in}`. That is the Python behaviour, and this
 * port reproduces it rather than improving on it -- the two have to agree
 * before either can be fixed. See the `cjk` fixtures for what it costs.
 */
export function tokens(text) {
  return new Set(norm(text).split(/[^a-z0-9]+/).filter(Boolean));
}

/** Python's `len(a & b) / len(a | b)`. */
function jaccard(left, right) {
  let shared = 0;
  for (const token of left) if (right.has(token)) shared += 1;
  return shared / (left.size + right.size - shared);
}

/** Python's `round(value, 3)`.
 *
 * `toFixed` sends a tie away from zero and Python sends it to the even digit,
 * so the two disagree on exactly one class of input: values a double holds
 * precisely *and* that sit halfway between two thousandths -- the multiples of
 * 1/16. No sum `scoreTarget` can return is one of those. Every addend comes
 * from {0.4, 0.2, 0.2*overlap, 0.15, 0.1} on top of 0.55 or 0.6, and the only
 * reachable three-place sums ending in a 5 are 0.65, 1.05 and 1.15, none of
 * which is a multiple of 1/16. The fixtures assert the digits rather than
 * trusting that argument.
 *
 * The rounding is not cosmetic: it is what makes a score of 0.6999999999 pass a
 * 0.7 threshold, and the threshold is a decision, not a measurement.
 */
export function round3(value) {
  return Number(value.toFixed(3));
}

/** Python's `str()`, in the two places it differs from `String()`.
 *
 * A float that happens to be integral is the case this cannot recover: JSON
 * `42.0` is a float in Python and arrives as the number `42` here, and no check
 * can tell it from an integer. It only matters if a macro parameter is written
 * as a literal `42.0`, which is worth knowing rather than worth guarding.
 */
export function pythonStr(value) {
  if (value === null || value === undefined) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  return String(value);
}

/** Python's `str()` for a number the other side holds as a float.
 *
 * `pythonStr` cannot do this one, and that is the whole reason this exists. A
 * score comes back from `round(score, 3)`, which is a float in Python however
 * integral it looks, so the server's `f"({score})"` writes `1.0`. JSON keeps no
 * trace of that: it hands this side the number `1`, and `String(1)` is `"1"`.
 * The caller knows the field is a float, so the `.0` is read off the contract
 * rather than guessed from the value.
 *
 * `pythonRepr` would coincide here -- `repr` and `str` agree on every float --
 * but it would not say *why* the `.0` is owed, and the next reader would have
 * to re-derive that the field is a float at all. `test/act-parity.mjs` holds
 * this to Python's own `str(round(v, 3))` over the scores `_score` can return,
 * the integral one included: it was a live replay of a perfect match that first
 * printed `(1)` where the server printed `(1.0)`.
 */
export function pythonFloat(value) {
  if (typeof value !== 'number') return pythonStr(value);
  return Number.isInteger(value) ? `${value}.0` : String(value);
}

/** Python's `repr`, for the one place output must match character for
 * character: the two error messages, which the parity fixtures compare in full.
 * A string gets single quotes, a boolean and None get capitals, a list or dict
 * is bracketed with `, ` between items, and an integral float keeps its `.0` --
 * which is exactly what `round(score, 3)` produces for a perfect match, so
 * `score=1.0` rather than `score=1`.
 */
export function pythonRepr(value) {
  if (value === null || value === undefined) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? `${value}.0` : String(value);
  }
  if (typeof value === 'string') {
    return `'${value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
  }
  if (Array.isArray(value)) return `[${value.map(pythonRepr).join(', ')}]`;
  if (typeof value === 'object') {
    return `{${Object.entries(value)
      .map(([key, item]) => `${pythonRepr(key)}: ${pythonRepr(item)}`)
      .join(', ')}}`;
  }
  return String(value);
}

/** Python's `_score`.
 *
 * Returns 0 for "not this element at all", which the resolver reads as "not a
 * candidate". Anything above 0 is a candidate, and how far above is what the
 * threshold and the ambiguity margin act on.
 */
export function scoreTarget(target, element) {
  let score;
  if (target.role && element.role !== target.role) {
    if (!NEAR_ROLES.has(`${target.role}|${element.role}`)) return 0;
    score = 0.55;
  } else {
    score = 0.6;
  }

  const wanted = norm(target.name);
  const actual = norm(element.name);
  if (!wanted) return score + 0.1;
  if (wanted === actual) {
    score += 0.4;
  } else if (wanted.includes(actual) || actual.includes(wanted)) {
    // An empty observed name lands here, because `'' in anything` is true --
    // the same thing Python's `"" in wanted` does, and the reason a nameless
    // button scores 0.75 against any target rather than being ignored.
    score += 0.2;
  } else {
    const wantedTokens = tokens(wanted);
    const actualTokens = tokens(actual);
    if (wantedTokens.size && actualTokens.size) {
      const overlap = jaccard(wantedTokens, actualTokens);
      if (overlap >= 0.5) score += 0.2 * overlap;
      else return 0;
    } else {
      return 0;
    }
  }

  // Context is the only thing allowed to outweigh the ambiguity margin, and it
  // only counts when it is genuinely shared. A macro is recorded *with* context
  // exactly when the observer saw a repeated label, so a step that has context
  // is a step that needed it.
  const context = norm(target.context);
  if (context && norm(element.context)) {
    const contextTokens = tokens(context);
    const elementTokens = tokens(element.context);
    if (contextTokens.size && elementTokens.size) {
      const overlap = jaccard(contextTokens, elementTokens);
      if (overlap >= 0.4) score += CONTEXT_WEIGHT;
    }
  }
  return score;
}

/** Python's `_substitute`: `{{name}}` for a parameter, left alone if missing. */
export function substitute(value, params) {
  if (typeof value !== 'string') return value;
  return value.replace(
    new RegExp(TEMPLATE_SOURCE, 'g'),
    (whole, name) => (Object.prototype.hasOwnProperty.call(params, name)
      ? pythonStr(params[name])
      : whole),
  );
}

/** Python's `resolve`, returning `{ops, report}` instead of a tuple.
 *
 * `ops` is what `act()` takes; `report` is what a human reads to find out which
 * element each step landed on and how sure the match was. Both are returned
 * because a replay that silently resolved to the wrong row looks identical to
 * one that resolved correctly until you read the report.
 *
 * @param {Array<object>} steps      descriptor steps from a macro file
 * @param {object} observation       an observation whose elements went through
 *                                   `elementFromRaw` (the field names matter)
 * @param {object} [params]          values for `{{placeholders}}`
 * @param {object} [options]
 * @param {number} [options.threshold=DEFAULT_THRESHOLD]
 */
export function resolve(steps, observation, params = {}, { threshold = DEFAULT_THRESHOLD } = {}) {
  const table = params || {};
  const elements = observation?.elements || [];
  const ops = [];
  const report = [];

  steps.forEach((step, index) => {
    // Python's `enumerate(steps, 1)`: the number a step is reported under is its
    // position in the file, so skipping a step that has no `op` does not
    // renumber the ones after it.
    const position = index + 1;
    const op = step.op;
    if (!op) return;

    const entry = { step: position, op };
    const target = step.target;
    // Python's `if target:` -- an empty dict is falsy there and truthy here, so
    // "present but empty" is spelled out rather than left to coercion. The
    // difference is invisible until a macro has a bare `"target": {}`, at which
    // point Python resolves it targetless and this would have tried to match.
    let opBody;

    if (target && Object.keys(target).length > 0) {
      const candidates = [];
      for (const element of elements) {
        const value = scoreTarget(target, element);
        if (value > 0) candidates.push({ element, score: round3(value) });
      }
      // Stable in ES2019+, and Python's `sort(reverse=True)` is stable too, so
      // equal scores keep the order the observer emitted them in -- which is
      // the order a failure report lists them in.
      candidates.sort((left, right) => right.score - left.score);

      if (!candidates.length || candidates[0].score < threshold) {
        const best = candidates.slice(0, 5).map((candidate) => ({
          ref: candidate.element.ref,
          name: candidate.element.name,
          role: candidate.element.role,
          score: candidate.score,
        }));
        // `pythonRepr`/`pythonStr` rather than template interpolation, because
        // Python prints `None` for a missing role and `0.7` for a float
        // threshold, and `${undefined}` and `${1}` are neither. The threshold
        // goes through `repr` rather than `str` because for a float the two are
        // the same function and only one of them is here: a threshold of `1.0`
        // has to print as `1.0`.
        throw new MacroError(
          `step ${position} (${op} on ${pythonStr(target.role)} ${pythonRepr(target.name)}) `
          + `did not match anything above ${pythonRepr(threshold)}: `
          // `best or 'none'` in Python: an empty list is falsy, so the fallback
          // is the *string* 'none' and the f-string prints it without quotes.
          + `best=${best.length ? pythonRepr(best) : 'none'}`,
        );
      }

      // Any near-tie is refused, however good the best match looks: two
      // "Select" buttons that both score 1.0 are exactly the silent misclick
      // this design exists to prevent. Context is what breaks the tie, so a
      // macro recorded on the right row scores higher.
      if (candidates.length > 1
          && candidates[0].score - candidates[1].score < AMBIGUITY_MARGIN) {
        throw new MacroError(
          `step ${position} is ambiguous between `
          + `${candidates[0].element.ref} ${pythonRepr(candidates[0].element.name)} and `
          + `${candidates[1].element.ref} ${pythonRepr(candidates[1].element.name)}. `
          + 'Add distinguishing context to the macro or run the step manually.',
        );
      }

      const chosen = candidates[0].element;
      entry.ref = chosen.ref;
      entry.name = chosen.name;
      entry.score = candidates[0].score;
      opBody = { op, ref: chosen.ref };
    } else {
      opBody = { op };
    }

    for (const field of ['text', 'value', 'url', 'key']) {
      if (Object.prototype.hasOwnProperty.call(step, field)) {
        opBody[field] = substitute(step[field], table);
      }
    }
    if (step.submit) opBody.submit = true;
    if (step.state !== undefined && step.state !== null) opBody.state = step.state;
    if (step.dir) {
      opBody.dir = step.dir;
      // Python's `step.get("amount", 600)` -- a present key wins even when it is
      // 0, so the default is applied on absence, not on falsiness. `|| 600`
      // here would turn an explicit `"amount": 0` into a 600-pixel scroll.
      opBody.amount = Object.prototype.hasOwnProperty.call(step, 'amount')
        ? step.amount
        : 600;
    }
    if (step.ms) opBody.ms = step.ms;
    // An empty `paths` is falsy in Python and truthy here, hence the length.
    if (step.paths && step.paths.length) {
      opBody.paths = step.paths.map((path) => substitute(path, table));
    }

    ops.push(opBody);
    report.push(entry);
  });

  return { ops, report };
}

/**
 * Resolve a macro file's steps.
 *
 * The wrapper exists because `load` is where a missing `steps` key becomes an
 * empty list -- the server does the same with `data.get("steps", [])` -- and
 * because a replayer should not have to remember that `resolve` takes steps
 * rather than the macro.
 */
export function resolveMacro(macro, observation, params = {}, options = {}) {
  return resolve(macro?.steps || [], observation, params, options);
}
