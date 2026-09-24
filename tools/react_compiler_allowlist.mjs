// ---------------------------------------------------------------------------
// React Compiler bailout allowlist (audit finding runtime-03): the pure half
// of the coverage gate in tools/react_compiler_coverage.mjs.
//
// A BAILOUT is anything that makes the compiler ship a function unmemoized:
// a CompileError, a CompileSkip, a PipelineError, or a 'use no memo' /
// 'use no forget' pragma. A file that emits no memo cache is NOT by itself a
// bailout: a component whose compiled body needs zero memo slots emits
// nothing and is clean (appRouter.tsx, commandSelection.ts, connectivity.ts
// and genieTurnStatus.ts on 2026-09-24).
//
// tools/react_compiler_allowlist.json records, per file, how many of each
// bailout the tree carried when the gate was introduced, who owns the fix
// and why. The gate fails on a bailout in an unlisted file, on a count above
// its entry, and on a STALE entry (the file is gone, or it improved: every
// count at or below the entry and at least one lower). The last rule makes
// the list a ratchet: a fix lowers the entry in the same change
// (`--ratchet`), so the list can only shrink. Never raise a count or add an
// entry to make the gate pass: a manual addition needs a finding id and a
// reviewer sign-off in the commit body.
//
// Nothing here touches Babel or the file system, so it is unit-testable in
// isolation (frontend/src/test/reactCompilerGate.test.ts).
// ---------------------------------------------------------------------------

/** Bailout counters, in the order every allowlist entry stores them. */
export const BAILOUT_COUNTERS = ['compileErrors', 'compileSkips', 'pipelineErrors', 'optOutPragmas'];

/** Annotation fields every entry carries after its counters. */
export const ENTRY_ANNOTATIONS = ['finding', 'owner', 'note', 'recorded'];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Per-counter totals for one analyzeFile() report. */
export function bailoutCounts(report) {
  return Object.fromEntries(BAILOUT_COUNTERS.map((counter) => [counter, report[counter]?.length ?? 0]));
}

function hasBailout(counts) {
  return BAILOUT_COUNTERS.some((counter) => counts[counter] > 0);
}

function allowedCounts(entry) {
  return Object.fromEntries(BAILOUT_COUNTERS.map((counter) => [counter, entry[counter]]));
}

/** Every bailout in one report as `{ kind, line, reason }`, for the gate's output. */
export function describeBailouts(report) {
  const rows = [];
  for (const error of report.compileErrors ?? []) {
    rows.push({ kind: 'compileError', line: error.line ?? error.fnLine ?? null, reason: error.reason });
  }
  for (const skip of report.compileSkips ?? []) {
    rows.push({ kind: 'compileSkip', line: skip.line ?? skip.fnLine ?? null, reason: String(skip.reason) });
  }
  for (const failure of report.pipelineErrors ?? []) {
    rows.push({ kind: 'pipelineError', line: failure.fnLine ?? null, reason: failure.data });
  }
  for (const pragma of report.optOutPragmas ?? []) {
    rows.push({ kind: 'optOutPragma', line: pragma.line ?? null, reason: `'${pragma.directive}' (${pragma.scope} scope)` });
  }
  return rows;
}

/**
 * Structural validation. Returns a list of problems (empty when valid): a
 * malformed allowlist must fail the gate, never read as "nothing allowed" or
 * "everything allowed".
 */
export function validateAllowlist(allowlist) {
  const problems = [];
  if (!allowlist || typeof allowlist !== 'object') return ['the allowlist is not a JSON object'];
  if (typeof allowlist.compilerVersion !== 'string') problems.push('compilerVersion must be a string');
  if (!allowlist.files || typeof allowlist.files !== 'object') return [...problems, 'files must be an object'];
  for (const [file, entry] of Object.entries(allowlist.files)) {
    if (!file.startsWith('frontend/src/')) problems.push(`${file}: path must be repo-relative under frontend/src/`);
    if (!entry || typeof entry !== 'object') {
      problems.push(`${file}: entry must be an object`);
      continue;
    }
    for (const counter of BAILOUT_COUNTERS) {
      if (!Number.isInteger(entry[counter]) || entry[counter] < 0) {
        problems.push(`${file}: ${counter} must be a non-negative integer`);
      }
    }
    if (BAILOUT_COUNTERS.every((counter) => entry[counter] === 0)) {
      problems.push(`${file}: an entry that allows nothing must be removed`);
    }
    for (const field of ENTRY_ANNOTATIONS) {
      if (typeof entry[field] !== 'string' || entry[field].trim() === '') problems.push(`${file}: ${field} is required`);
    }
    if (typeof entry.recorded === 'string' && !ISO_DATE.test(entry.recorded)) {
      problems.push(`${file}: recorded must be an ISO date (YYYY-MM-DD)`);
    }
  }
  return problems;
}

/**
 * Compare scan reports with an allowlist. Pure.
 *
 *   unlisted: a file with at least one bailout and no entry.
 *   grown:    a listed file with a count above its entry.
 *   stale:    an entry whose file was not scanned (gone, or outside the gate's
 *             scope), or whose counts are all at or below the entry with at
 *             least one lower ("improved — run --ratchet").
 *
 * A listed file that is both lower on one counter and higher on another is
 * `grown`, not stale: the new bailout must be fixed first.
 */
export function evaluateAllowlist(reports, allowlist) {
  const entries = allowlist?.files ?? {};
  const scanned = new Set();
  const unlisted = [];
  const grown = [];
  const stale = [];
  for (const report of reports) {
    scanned.add(report.file);
    const counts = bailoutCounts(report);
    const entry = Object.hasOwn(entries, report.file) ? entries[report.file] : null;
    if (!entry) {
      if (hasBailout(counts)) unlisted.push({ file: report.file, counts, bailouts: describeBailouts(report) });
      continue;
    }
    const allowed = allowedCounts(entry);
    const over = BAILOUT_COUNTERS.filter((counter) => counts[counter] > allowed[counter]);
    if (over.length > 0) {
      grown.push({ file: report.file, counts, allowed, counters: over, bailouts: describeBailouts(report) });
      continue;
    }
    const lower = BAILOUT_COUNTERS.filter((counter) => counts[counter] < allowed[counter]);
    if (lower.length > 0) stale.push({ file: report.file, reason: 'improved', counts, allowed, counters: lower });
  }
  for (const file of Object.keys(entries).sort()) {
    if (!scanned.has(file)) stale.push({ file, reason: 'missing', counts: null, allowed: allowedCounts(entries[file]), counters: [] });
  }
  return { unlisted, grown, stale };
}

/**
 * The allowlist `--ratchet` writes: every entry lowered to its measured
 * counts, and dropped when its file is gone or clean. Throws while any file
 * is unlisted or grown (the caller refuses and leaves the file untouched).
 * Counts only ever go DOWN here; annotations are kept.
 */
export function ratchetAllowlist(reports, allowlist, compilerVersion) {
  const verdict = evaluateAllowlist(reports, allowlist);
  if (verdict.unlisted.length > 0 || verdict.grown.length > 0) {
    throw new Error('refusing to ratchet while a bailout is unlisted or above its entry; fix those first');
  }
  const measured = new Map(reports.map((report) => [report.file, bailoutCounts(report)]));
  const files = {};
  for (const [file, entry] of Object.entries(allowlist.files)) {
    const counts = measured.get(file);
    if (!counts || !hasBailout(counts)) continue;
    const next = { ...entry };
    for (const counter of BAILOUT_COUNTERS) next[counter] = Math.min(entry[counter], counts[counter]);
    files[file] = next;
  }
  return { ...allowlist, compilerVersion, files };
}

/** A fresh allowlist from a scan (bootstrap only; `--write-allowlist`). */
export function bootstrapAllowlist(reports, compilerVersion, recorded) {
  const files = {};
  for (const report of reports) {
    const counts = bailoutCounts(report);
    if (!hasBailout(counts)) continue;
    files[report.file] = {
      ...counts,
      finding: 'runtime-03',
      owner: 'unassigned',
      note: describeBailouts(report).map((row) => row.reason).join('; '),
      recorded,
    };
  }
  return { compilerVersion, files };
}

function entryLine(entry) {
  const ordered = [...BAILOUT_COUNTERS, ...ENTRY_ANNOTATIONS];
  const extra = Object.keys(entry).filter((key) => !ordered.includes(key)).sort();
  return `{ ${[...ordered, ...extra]
    .filter((key) => entry[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}: ${JSON.stringify(entry[key])}`)
    .join(', ')} }`;
}

/**
 * Stable serialization: top-level keys first, then `files` sorted by path
 * with ONE entry per line, so a ratchet or a new entry is a one-line diff.
 */
export function serializeAllowlist(allowlist) {
  const header = Object.keys(allowlist)
    .filter((key) => key !== 'files')
    .map((key) => `  ${JSON.stringify(key)}: ${JSON.stringify(allowlist[key])},`);
  const files = Object.keys(allowlist.files)
    .sort()
    .map((file) => `    ${JSON.stringify(file)}: ${entryLine(allowlist.files[file])}`);
  const body = files.length > 0 ? [files.join(',\n')] : [];
  return ['{', ...header, '  "files": {', ...body, '  }', '}', ''].join('\n');
}

function formatBailouts(bailouts) {
  return bailouts.map((row) => `      line ${row.line ?? '?'} [${row.kind}] ${row.reason}`);
}

function formatCounts(counts) {
  return BAILOUT_COUNTERS.map((counter) => `${counter}=${counts[counter]}`).join(' ');
}

/** Human-readable lines for a verdict; empty when the gate passes. */
export function formatVerdict(verdict) {
  const lines = [];
  for (const item of verdict.unlisted) {
    lines.push(`UNLISTED ${item.file} (${formatCounts(item.counts)})`, ...formatBailouts(item.bailouts));
  }
  for (const item of verdict.grown) {
    lines.push(
      `GROWN    ${item.file}: ${item.counters.map((c) => `${c} ${item.allowed[c]} -> ${item.counts[c]}`).join(', ')}`,
      ...formatBailouts(item.bailouts),
    );
  }
  for (const item of verdict.stale) {
    lines.push(
      item.reason === 'missing'
        ? `STALE    ${item.file}: the file is gone or outside the gate's scope; remove the entry (run --ratchet)`
        : `STALE    ${item.file}: improved — run --ratchet (${item.counters.map((c) => `${c} ${item.allowed[c]} -> ${item.counts[c]}`).join(', ')})`,
    );
  }
  return lines;
}
