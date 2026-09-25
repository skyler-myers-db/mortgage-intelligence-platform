// ---------------------------------------------------------------------------
// oxlint jsx-a11y ratchet (audit a11y-05 item 2): the pure half of the gate in
// tools/oxlint_ratchet.mjs. Nothing here spawns oxlint or touches the file
// system, so every rule is unit-testable (frontend/src/test/oxlintRatchet.test.ts).
//
// frontend/oxlint-baseline.json records, per file, how many hits of each
// jsx-a11y rule the tree carries, keyed by FILE + RULE + COUNT, never by line.
// The gate fails on an UNLISTED file+rule key, a GROWN count, a STALE entry
// (a lower count, or no hit left in the file), a baseline recorded with a
// different oxlint, and any suppression: a directive, or a config-level
// disable (an `overrides` block, an extra ignore pattern). `--ratchet` only lowers;
// a new hit is fixed in code, never added.
// ---------------------------------------------------------------------------
import path from 'node:path';

export const CATEGORIES = ['correctness', 'nursery', 'pedantic', 'perf', 'restriction', 'style', 'suspicious'];
// The only top-level config keys: `overrides`, `extends`, `settings` and the
// like would disable a rule for a file outside the source scan, so a
// config-level disable is banned like a directive.
export const CONFIG_KEYS = ['$schema', 'categories', 'ignorePatterns', 'plugins', 'rules'];
export const IGNORE_PATTERNS = ['**/*.test.ts', '**/*.test.tsx', 'src/test/**', 'src/mocks/**'];
export const BASELINE_FIELDS = ['oxlintVersion', 'config', 'scope', 'policy', 'files'];
export const CONFIG_REL = 'frontend/.oxlintrc.json';
export const SCOPE_REL = 'frontend/src';
const POLICY_TEXT = {
  gate: 'node tools/oxlint_ratchet.mjs --check frontend/oxlint-baseline.json (npm --prefix frontend run lint:a11y)',
  key: 'file + rule + count, never line',
  newHit: 'fix it in code; a new file+rule key or a higher count is never added here',
  ratchet: '--ratchet only lowers counts, drops fixed or deleted files and accepts a new oxlintVersion; --moved-from/--moved-to transfers what a pure move carried into a new file',
  suppressions: 'banned under frontend/src: any oxlint-disable directive, and an eslint-disable directive that is bare or names a jsx-a11y rule',
};
const RULE_CODE = /^(?:eslint-plugin-)?jsx[-_]a11y\(([a-z0-9-]+)\)$/;
// oxlint honours both its own and ESLint's disable comments.
const DIRECTIVE = /(?:\/\/|\/\*)\s*(oxlint|eslint)-disable(?:-next-line|-line)?(?=\s|\*\/|$)([^\n]*)/g;

export const posix = (p) => p.split(path.sep).join('/');
const sortObject = (o) => Object.fromEntries(Object.keys(o).sort().map((k) => [k, o[k]]));

/** `jsx-a11y(alt-text)` / `eslint-plugin-jsx-a11y(alt-text)` -> `jsx-a11y/alt-text`; null otherwise. */
export function normalizeRule(code) {
  const match = RULE_CODE.exec(String(code ?? ''));
  return match ? `jsx-a11y/${match[1]}` : null;
}

/** Rules the config sets to "error" (the only enabled severity it may use). */
export function enabledRules(config) {
  return Object.keys(config?.rules ?? {}).filter((rule) => config.rules[rule] === 'error').sort();
}

/** Config drift problems; every name in `installed` (the oxlint's jsx-a11y rules) must be named. */
export function validateConfig(config, installed = []) {
  const problems = [];
  for (const key of Object.keys(config ?? {})) {
    if (!CONFIG_KEYS.includes(key)) problems.push(`${key}: only ${CONFIG_KEYS.join(', ')} may be set (a config-level disable is banned)`);
  }
  if (JSON.stringify(config?.ignorePatterns) !== JSON.stringify(IGNORE_PATTERNS)) {
    problems.push(`ignorePatterns must be exactly ${JSON.stringify(IGNORE_PATTERNS)}`);
  }
  if (JSON.stringify(config?.plugins) !== '["jsx-a11y"]') problems.push('plugins must be exactly ["jsx-a11y"]');
  for (const category of CATEGORIES) {
    if (config?.categories?.[category] !== 'off') problems.push(`category ${category} must be "off"`);
  }
  for (const [rule, level] of Object.entries(config?.rules ?? {})) {
    if (!rule.startsWith('jsx-a11y/')) problems.push(`${rule}: only jsx-a11y rules may be configured`);
    if (level !== 'error' && level !== 'off') problems.push(`${rule}: level must be "error" or "off"`);
  }
  if (enabledRules(config).length === 0) problems.push('no jsx-a11y rule is enabled');
  for (const rule of installed) {
    if (!Object.hasOwn(config?.rules ?? {}, rule)) problems.push(`${rule} is listed by the installed oxlint but not named in the config`);
  }
  return problems;
}

/** Parse `oxlint --format json` output. Throws unless it is a real, non-vacuous run. */
export function parseOxlintOutput(stdout, expectedRules) {
  let report;
  try {
    report = JSON.parse(stdout);
  } catch {
    throw new Error(`oxlint output is not JSON (a config or runtime error?):\n${String(stdout).slice(0, 2000)}`);
  }
  if (!report || !Array.isArray(report.diagnostics)) throw new Error('oxlint JSON has no diagnostics array');
  const { number_of_files: fileCount, number_of_rules: ruleCount } = report;
  if (!Number.isInteger(fileCount) || !Number.isInteger(ruleCount)) {
    throw new Error('oxlint JSON has no number_of_files / number_of_rules summary');
  }
  if (fileCount < 1) throw new Error('vacuous oxlint run: no file was linted');
  if (ruleCount !== expectedRules) throw new Error(`vacuous oxlint run: ${ruleCount} rules ran, the config enables ${expectedRules}`);
  return { diagnostics: report.diagnostics, fileCount, ruleCount };
}

/** Diagnostics -> { measured: {file: {rule: count}}, lines: {"file|rule": [line]} }; POSIX paths relative to `root`. */
export function aggregate(diagnostics, { cwd, root }) {
  const measured = {};
  const lines = {};
  for (const diagnostic of diagnostics) {
    const rule = normalizeRule(diagnostic.code);
    if (!rule) {
      throw new Error(`config drift: a non-jsx-a11y diagnostic (${diagnostic.code ?? 'no code'}) in ${diagnostic.filename}: ${diagnostic.message}`);
    }
    const file = posix(path.relative(root, path.resolve(cwd, diagnostic.filename)));
    measured[file] ??= {};
    measured[file][rule] = (measured[file][rule] ?? 0) + 1;
    (lines[`${file}|${rule}`] ??= []).push(diagnostic.labels?.[0]?.span?.line ?? null);
  }
  return { measured, lines };
}

/** Banned suppression directives in `[{file, text}]`, as `{file, line, directive}`. */
export function findSuppressions(sources) {
  const found = [];
  for (const { file, text } of sources) {
    for (const match of text.matchAll(DIRECTIVE)) {
      const rules = match[2].split('*/')[0].split(/\s--\s/)[0].trim();
      if (match[1] === 'oxlint' || rules === '' || rules.includes('jsx-a11y/')) {
        found.push({ file, line: text.slice(0, match.index).split('\n').length, directive: match[0].trim() });
      }
    }
  }
  return found;
}

const allowedCount = (files, file, rule) => (Object.hasOwn(files, file) && Object.hasOwn(files[file], rule) ? files[file][rule] : undefined);

/** Compare measured hits with a baseline. Pure. */
export function evaluateBaseline(measured, baseline, { version, suppressions = [] } = {}) {
  const base = baseline.files ?? {};
  const unlisted = [];
  const grown = [];
  const stale = [];
  for (const file of Object.keys(measured).sort()) {
    for (const rule of Object.keys(measured[file]).sort()) {
      const count = measured[file][rule];
      const allowed = allowedCount(base, file, rule);
      if (allowed === undefined) unlisted.push({ file, rule, count });
      else if (count > allowed) grown.push({ file, rule, count, allowed });
    }
  }
  for (const file of Object.keys(base).sort()) {
    const gone = !Object.hasOwn(measured, file);
    for (const rule of Object.keys(base[file]).sort()) {
      const count = gone ? 0 : (measured[file][rule] ?? 0);
      if (count < base[file][rule]) stale.push({ file, rule, count, allowed: base[file][rule], gone });
    }
  }
  const versionMismatch = baseline.oxlintVersion === version ? null : { baseline: baseline.oxlintVersion, installed: version };
  return { unlisted, grown, stale, suppressions, versionMismatch };
}

/** Per-rule totals over a {file: {rule: count}} map. */
export function ruleTotals(files) {
  const totals = {};
  for (const rules of Object.values(files)) {
    for (const [rule, count] of Object.entries(rules)) totals[rule] = (totals[rule] ?? 0) + count;
  }
  return sortObject(totals);
}

/** Transfer what each pure move carried from `from` into the NEW file `to`. Throws on anything else. */
export function applyMoves(measured, baseline, moves, recorded) {
  const files = structuredClone(baseline.files ?? {});
  const records = [];
  for (const { from, to } of moves) {
    if (from === to) throw new Error(`--moved-from and --moved-to name the same file: ${from}`);
    if (Object.hasOwn(files, to)) throw new Error(`${to} already has a baseline entry: a move must land in a new file`);
    if (!Object.hasOwn(files, from)) throw new Error(`${from} has no baseline entry to move from`);
    const landed = Object.hasOwn(measured, to) ? measured[to] : {};
    if (Object.keys(landed).length === 0) throw new Error(`${to} has no hit: nothing moved`);
    const source = files[from];
    const transfer = {};
    for (const rule of Object.keys(landed).sort()) {
      const left = (source[rule] ?? 0) - (measured[from]?.[rule] ?? 0);
      if (landed[rule] > left) {
        throw new Error(`${to}: ${rule} has ${landed[rule]} hit(s) but only ${Math.max(left, 0)} left ${from}; a new hit is fixed in code, never moved`);
      }
      transfer[rule] = landed[rule];
      source[rule] -= landed[rule];
      if (source[rule] === 0) delete source[rule];
    }
    if (Object.keys(source).length === 0) delete files[from];
    files[to] = transfer;
    records.push({ from, to, rules: transfer, recorded });
  }
  return { files, records };
}

/** The baseline `--ratchet` writes. Throws while anything is suppressed, unlisted or grown. */
export function ratchetBaseline(measured, baseline, { version, suppressions = [], moves = [], recorded }) {
  if (suppressions.length > 0) throw new Error('refusing to ratchet while a suppression directive exists; remove it and fix the hit');
  const moved = applyMoves(measured, baseline, moves, recorded);
  const verdict = evaluateBaseline(measured, { ...baseline, files: moved.files }, { version });
  if (verdict.unlisted.length > 0 || verdict.grown.length > 0) {
    throw new Error('refusing to ratchet while a hit is unlisted or above its baseline; fix it in code');
  }
  const files = {};
  for (const [file, rules] of Object.entries(moved.files)) {
    const next = {};
    for (const [rule, allowed] of Object.entries(rules)) {
      const count = Math.min(allowed, measured[file]?.[rule] ?? 0);
      if (count > 0) next[rule] = count;
    }
    if (Object.keys(next).length > 0) files[file] = next;
  }
  const before = ruleTotals(baseline.files ?? {});
  for (const [rule, total] of Object.entries(ruleTotals(files))) {
    if (total > (before[rule] ?? 0)) throw new Error(`refusing to ratchet: the ${rule} total would grow ${before[rule] ?? 0} -> ${total}`);
  }
  const earlier = Array.isArray(baseline.policy?.moves) ? baseline.policy.moves : [];
  return { ...baseline, oxlintVersion: version, policy: { ...POLICY_TEXT, moves: [...earlier, ...moved.records] }, files };
}

/** A fresh baseline (bootstrap only; `--write-baseline`). */
export function bootstrapBaseline(measured, version) {
  return { oxlintVersion: version, config: CONFIG_REL, scope: SCOPE_REL, policy: { ...POLICY_TEXT, moves: [] }, files: measured };
}

/** Structural validation: a malformed baseline must fail, never read as "allow all" or "allow none". */
export function validateBaseline(baseline) {
  if (!baseline || typeof baseline !== 'object') return ['the baseline is not a JSON object'];
  const problems = [];
  if (JSON.stringify(Object.keys(baseline).sort()) !== JSON.stringify([...BASELINE_FIELDS].sort())) {
    problems.push(`fields must be exactly ${BASELINE_FIELDS.join(', ')}`);
  }
  if (typeof baseline.oxlintVersion !== 'string') problems.push('oxlintVersion must be a string');
  if (baseline.config !== CONFIG_REL || baseline.scope !== SCOPE_REL) problems.push(`config/scope must be ${CONFIG_REL} / ${SCOPE_REL}`);
  if (!Array.isArray(baseline.policy?.moves)) problems.push('policy.moves must be an array');
  if (!baseline.files || typeof baseline.files !== 'object') return [...problems, 'files must be an object'];
  for (const [file, rules] of Object.entries(baseline.files)) {
    if (!file.startsWith(`${SCOPE_REL}/`)) problems.push(`${file}: path must be repo-relative under ${SCOPE_REL}/`);
    if (!rules || typeof rules !== 'object' || Object.keys(rules).length === 0) problems.push(`${file}: an entry must list at least one rule`);
    for (const [rule, count] of Object.entries(rules ?? {})) {
      if (!rule.startsWith('jsx-a11y/')) problems.push(`${file}: ${rule} is not a jsx-a11y rule`);
      if (!Number.isInteger(count) || count < 1) problems.push(`${file}: ${rule} count must be a positive integer`);
    }
  }
  return problems;
}

/** Deterministic: fixed top-level order, sorted keys, one file per line, trailing newline. */
export function serializeBaseline(baseline) {
  const moves = baseline.policy.moves.map((move) => sortObject({ ...move, rules: sortObject(move.rules) }));
  const policy = JSON.stringify(sortObject({ ...baseline.policy, moves }), null, 2).replaceAll('\n', '\n  ');
  const files = Object.keys(baseline.files).sort()
    .map((file) => `    ${JSON.stringify(file)}: ${JSON.stringify(sortObject(baseline.files[file]))}`);
  return [
    '{',
    `  "oxlintVersion": ${JSON.stringify(baseline.oxlintVersion)},`,
    `  "config": ${JSON.stringify(baseline.config)},`,
    `  "scope": ${JSON.stringify(baseline.scope)},`,
    `  "policy": ${policy},`,
    '  "files": {',
    ...(files.length > 0 ? [files.join(',\n')] : []),
    '  }',
    '}',
    '',
  ].join('\n');
}

/** Human-readable lines for a verdict; empty when the gate passes. */
export function formatVerdict(verdict, lines = {}) {
  const at = (file, rule) => (lines[`${file}|${rule}`] ? ` (lines ${lines[`${file}|${rule}`].join(', ')})` : '');
  const mismatch = verdict.versionMismatch;
  return [
    ...(mismatch ? [`VERSION  the baseline records oxlint ${mismatch.baseline}; ${mismatch.installed} is installed (run --ratchet after the bump)`] : []),
    ...verdict.suppressions.map((s) => `SUPPRESSED ${s.file}:${s.line}: ${s.directive} (suppressions are banned; fix the hit)`),
    ...verdict.unlisted.map((u) => `UNLISTED ${u.file}: ${u.rule} x${u.count}${at(u.file, u.rule)} (fix it in code)`),
    ...verdict.grown.map((g) => `GROWN    ${g.file}: ${g.rule} ${g.allowed} -> ${g.count}${at(g.file, g.rule)} (fix it in code)`),
    ...verdict.stale.map((s) => `STALE    ${s.file}: ${s.rule} ${s.allowed} -> ${s.count}${s.gone ? ' (no hit left in the file)' : ''}; run --ratchet`),
  ];
}
