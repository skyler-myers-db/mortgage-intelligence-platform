// ---------------------------------------------------------------------------
// TypeScript strict ratchet (2026-09-21 audit stack-07 items 3-5): the pure
// half of the gate in tools/ts_strict_ratchet.mjs. Nothing here spawns tsc or
// touches the file system, so every rule is unit-testable
// (frontend/src/test/tsStrictRatchet.test.ts).
//
// The build config (frontend/tsconfig.json, `tsc -b`) carries ES2022 and every
// strict flag that is at zero over ALL of src. frontend/tsconfig.strict.json
// extends it with the remaining flags over PRODUCTION files only (tests,
// src/test and src/mocks stay on the build config), and
// tools/ts_strict_ratchet_baseline.json records its hits keyed by
// FILE + TS CODE + COUNT, never by line. The gate fails on an UNLISTED
// file+code, a GROWN count, a STALE entry (a lower count, or no hit left in
// the file), a baseline recorded with another TypeScript, config drift, and a
// suppression comment. `--ratchet` only lowers. A flag moves into
// tsconfig.json once it is zero over production AND tests (the promotion rule).
// ---------------------------------------------------------------------------
import path from 'node:path';

/** The eight strict flags the audit names; each must be on in the build or the strict config. */
export const REQUIRED_FLAGS = [
  'verbatimModuleSyntax',
  'erasableSyntaxOnly',
  'noImplicitOverride',
  'noImplicitReturns',
  'noFallthroughCasesInSwitch',
  'noUnusedLocals',
  'noUnusedParameters',
  'noUncheckedIndexedAccess',
];
export const BUILD_CONFIG_REL = 'frontend/tsconfig.json';
export const STRICT_CONFIG_REL = 'frontend/tsconfig.strict.json';
export const SCOPE_REL = 'frontend/src';
export const STRICT_INCLUDE = ['src'];
export const STRICT_EXCLUDE = ['src/**/*.test.ts', 'src/**/*.test.tsx', 'src/test/**', 'src/mocks/**'];
export const TARGET = 'ES2022';
export const LIB = ['DOM', 'DOM.Iterable', 'ES2022'];
export const BASELINE_FIELDS = ['typescriptVersion', 'config', 'flags', 'policy', 'files'];
const POLICY_TEXT = {
  gate: 'node tools/ts_strict_ratchet.mjs --check tools/ts_strict_ratchet_baseline.json (CI frontend job)',
  key: 'file + TS code + count, never line',
  newHit: 'fix it in code (narrowing, `?? fallback`, an early return, an explicit tuple type); a new file+code key or a higher count is never added here',
  promotion: 'a flag moves from tsconfig.strict.json into tsconfig.json once it is zero over production AND tests',
  ratchet: '--ratchet only lowers counts, drops fixed or deleted files and accepts a new typescriptVersion',
  suppressions: 'banned in production code: @ts-ignore and @ts-nocheck anywhere, @ts-expect-error outside src/types/wireContract.*.check.ts',
};

const LOCATED = /^(.+?)\((\d+),(\d+)\): error (TS\d+): (.*)$/;
const GLOBAL_ERROR = /\berror TS\d+:/;
const TS_DIRECTIVE = /(?:\/\/|\/\*)\s*@ts-(ignore|nocheck|expect-error)\b/g;
const EXPECT_ERROR_HOME = /^frontend\/src\/types\/wireContract\.[^/]*\.check\.ts$/;

export const posix = (p) => p.split(path.sep).join('/');
const sortObject = (o) => Object.fromEntries(Object.keys(o).sort().map((k) => [k, o[k]]));

/** Whether a frontend-relative POSIX path is production source (what the strict config type-checks). */
export function isProductionSource(rel) {
  return /^src\/.+\.tsx?$/.test(rel) && !/\.test\.tsx?$/.test(rel) && !rel.startsWith('src/test/') && !rel.startsWith('src/mocks/');
}

/**
 * Parse `tsc --pretty false --listFiles` output into located hits and listed
 * files. Indented continuation lines are ignored; an `error TS…` without a
 * location (a config or global error) throws, so it can never read as zero.
 */
export function parseTscOutput(stdout) {
  const hits = [];
  const listed = [];
  for (const line of String(stdout).split(/\r?\n/)) {
    if (line.trim() === '' || /^\s/.test(line)) continue;
    const match = LOCATED.exec(line);
    if (match) {
      hits.push({ file: posix(match[1]), line: Number(match[2]), column: Number(match[3]), code: match[4], message: match[5] });
    } else if (GLOBAL_ERROR.test(line)) {
      throw new Error(`tsc reported an error without a location (config drift or a global error): ${line}`);
    } else {
      listed.push(posix(line));
    }
  }
  return { hits, listed };
}

/** Hits -> { measured: {file: {code: count}}, lines: {"file|code": [line]} }; POSIX paths relative to `root`. */
export function aggregate(hits, { cwd, root }) {
  const measured = {};
  const lines = {};
  for (const hit of hits) {
    const file = posix(path.relative(root, path.resolve(cwd, hit.file)));
    measured[file] ??= {};
    measured[file][hit.code] = (measured[file][hit.code] ?? 0) + 1;
    (lines[`${file}|${hit.code}`] ??= []).push(hit.line);
  }
  return { measured, lines };
}

/**
 * The strict program must cover exactly the production files the tool
 * enumerates: fewer is a vacuous gate, more means the exclude drifted.
 * `expected` and `listed` are repo-relative POSIX paths under SCOPE_REL.
 */
export function listedFileProblems(expected, listed) {
  const want = new Set(expected);
  const got = new Set(listed.filter((file) => file.startsWith(`${SCOPE_REL}/`)));
  const missing = [...want].filter((file) => !got.has(file)).sort();
  const extra = [...got].filter((file) => !want.has(file)).sort();
  if (missing.length === 0 && extra.length === 0 && want.size > 0) return [];
  return [
    `the strict program listed ${got.size} file(s) under ${SCOPE_REL}; the tool enumerates ${want.size} production file(s)`,
    ...missing.map((file) => `  not type-checked: ${file}`),
    ...extra.map((file) => `  not production source: ${file}`),
  ];
}

const flagValue = (config, flag) => config?.compilerOptions?.[flag];

/** Config drift problems for the build config and the strict config (both parsed JSON). */
export function validateStrictConfig({ buildConfig, strictConfig }) {
  const problems = [];
  if (strictConfig?.extends !== './tsconfig.json') problems.push(`${STRICT_CONFIG_REL} must extend ./tsconfig.json`);
  if (JSON.stringify(strictConfig?.include) !== JSON.stringify(STRICT_INCLUDE)) {
    problems.push(`${STRICT_CONFIG_REL} include must be exactly ${JSON.stringify(STRICT_INCLUDE)}`);
  }
  if (JSON.stringify(strictConfig?.exclude) !== JSON.stringify(STRICT_EXCLUDE)) {
    problems.push(`${STRICT_CONFIG_REL} exclude must be exactly ${JSON.stringify(STRICT_EXCLUDE)} (production only)`);
  }
  const extraKeys = Object.keys(strictConfig ?? {}).filter((key) => !['extends', 'compilerOptions', 'include', 'exclude'].includes(key));
  if (extraKeys.length > 0) problems.push(`${STRICT_CONFIG_REL} may set only extends, compilerOptions, include and exclude (found ${extraKeys.join(', ')})`);
  for (const flag of REQUIRED_FLAGS) {
    const build = flagValue(buildConfig, flag);
    const strict = flagValue(strictConfig, flag);
    if (build === false || strict === false) problems.push(`${flag} is set to false (a strict flag may only be on)`);
    else if (build !== true && strict !== true) problems.push(`${flag} is on in neither ${BUILD_CONFIG_REL} nor ${STRICT_CONFIG_REL}`);
  }
  for (const [name, config] of [[BUILD_CONFIG_REL, buildConfig], [STRICT_CONFIG_REL, strictConfig]]) {
    const options = config?.compilerOptions ?? {};
    for (const key of ['strict']) if (options[key] === false) problems.push(`${name}: ${key} may not be false`);
    if (name === BUILD_CONFIG_REL || Object.hasOwn(options, 'target')) {
      if (String(options.target).toUpperCase() !== TARGET) problems.push(`${name}: target must be ${TARGET}`);
    }
    if (name === BUILD_CONFIG_REL || Object.hasOwn(options, 'lib')) {
      if (JSON.stringify(options.lib) !== JSON.stringify(LIB)) problems.push(`${name}: lib must be exactly ${JSON.stringify(LIB)}`);
    }
  }
  const stricter = Object.keys(strictConfig?.compilerOptions ?? {}).filter((key) => !REQUIRED_FLAGS.includes(key));
  if (stricter.length > 0) problems.push(`${STRICT_CONFIG_REL} compilerOptions may set only the REQUIRED_FLAGS (found ${stricter.join(', ')})`);
  if (buildConfig?.compilerOptions?.strict !== true) problems.push(`${BUILD_CONFIG_REL}: strict must be true`);
  return problems;
}

/**
 * Banned TS suppression comments in `[{file, text}]` (repo-relative POSIX
 * paths of production files): @ts-ignore and @ts-nocheck anywhere, and
 * @ts-expect-error outside src/types/wireContract.*.check.ts (whose
 * expect-errors are the wire-contract proofs themselves).
 */
export function findTsSuppressions(sources) {
  const found = [];
  for (const { file, text } of sources) {
    for (const match of text.matchAll(TS_DIRECTIVE)) {
      if (match[1] === 'expect-error' && EXPECT_ERROR_HOME.test(file)) continue;
      found.push({ file, line: text.slice(0, match.index).split('\n').length, directive: `@ts-${match[1]}` });
    }
  }
  return found;
}

const allowedCount = (files, file, code) => (Object.hasOwn(files, file) && Object.hasOwn(files[file], code) ? files[file][code] : undefined);

/** Compare measured hits with a baseline. Pure. */
export function evaluateBaseline(measured, baseline, { version, suppressions = [], configProblems = [] } = {}) {
  const base = baseline.files ?? {};
  const unlisted = [];
  const grown = [];
  const stale = [];
  for (const file of Object.keys(measured).sort()) {
    for (const code of Object.keys(measured[file]).sort()) {
      const count = measured[file][code];
      const allowed = allowedCount(base, file, code);
      if (allowed === undefined) unlisted.push({ file, code, count });
      else if (count > allowed) grown.push({ file, code, count, allowed });
    }
  }
  for (const file of Object.keys(base).sort()) {
    const gone = !Object.hasOwn(measured, file);
    for (const code of Object.keys(base[file]).sort()) {
      const count = gone ? 0 : (measured[file][code] ?? 0);
      if (count < base[file][code]) stale.push({ file, code, count, allowed: base[file][code], gone });
    }
  }
  const versionMismatch = baseline.typescriptVersion === version ? null : { baseline: baseline.typescriptVersion, installed: version };
  return { unlisted, grown, stale, suppressions, configProblems, versionMismatch };
}

/** The baseline `--ratchet` writes: only lowers. Throws while anything is suppressed, drifted, unlisted or grown. */
export function ratchetBaseline(measured, baseline, { version, suppressions = [], configProblems = [] }) {
  if (suppressions.length > 0) throw new Error('refusing to ratchet while a TS suppression comment exists; remove it and fix the hit');
  if (configProblems.length > 0) throw new Error('refusing to ratchet while the tsconfig drifted');
  const verdict = evaluateBaseline(measured, baseline, { version });
  if (verdict.unlisted.length > 0 || verdict.grown.length > 0) {
    throw new Error('refusing to ratchet while a hit is unlisted or above its baseline; fix it in code');
  }
  const files = {};
  for (const [file, codes] of Object.entries(baseline.files ?? {})) {
    const next = {};
    for (const [code, allowed] of Object.entries(codes)) {
      const count = Math.min(allowed, measured[file]?.[code] ?? 0);
      if (count > 0) next[code] = count;
    }
    if (Object.keys(next).length > 0) files[file] = next;
  }
  return { ...baseline, typescriptVersion: version, policy: { ...POLICY_TEXT }, files };
}

/** A fresh baseline (bootstrap only; `--write-baseline`). */
export function bootstrapBaseline(measured, version) {
  return { typescriptVersion: version, config: STRICT_CONFIG_REL, flags: [...REQUIRED_FLAGS], policy: { ...POLICY_TEXT }, files: measured };
}

/** Structural validation: a malformed baseline must fail, never read as "allow all" or "allow none". */
export function validateBaseline(baseline) {
  if (!baseline || typeof baseline !== 'object') return ['the baseline is not a JSON object'];
  const problems = [];
  if (JSON.stringify(Object.keys(baseline).sort()) !== JSON.stringify([...BASELINE_FIELDS].sort())) {
    problems.push(`fields must be exactly ${BASELINE_FIELDS.join(', ')}`);
  }
  if (typeof baseline.typescriptVersion !== 'string') problems.push('typescriptVersion must be a string');
  if (baseline.config !== STRICT_CONFIG_REL) problems.push(`config must be ${STRICT_CONFIG_REL}`);
  if (JSON.stringify(baseline.flags) !== JSON.stringify(REQUIRED_FLAGS)) problems.push(`flags must be exactly ${JSON.stringify(REQUIRED_FLAGS)}`);
  if (!baseline.files || typeof baseline.files !== 'object') return [...problems, 'files must be an object'];
  for (const [file, codes] of Object.entries(baseline.files)) {
    if (!file.startsWith(`${SCOPE_REL}/`)) problems.push(`${file}: path must be repo-relative under ${SCOPE_REL}/`);
    if (!codes || typeof codes !== 'object' || Object.keys(codes).length === 0) problems.push(`${file}: an entry must list at least one TS code`);
    for (const [code, count] of Object.entries(codes ?? {})) {
      if (!/^TS\d+$/.test(code)) problems.push(`${file}: ${code} is not a TS error code`);
      if (!Number.isInteger(count) || count < 1) problems.push(`${file}: ${code} count must be a positive integer`);
    }
  }
  return problems;
}

/** Deterministic: fixed top-level order, sorted keys, one file per line, trailing newline. */
export function serializeBaseline(baseline) {
  const policy = JSON.stringify(sortObject(baseline.policy), null, 2).replaceAll('\n', '\n  ');
  const files = Object.keys(baseline.files).sort()
    .map((file) => `    ${JSON.stringify(file)}: ${JSON.stringify(sortObject(baseline.files[file]))}`);
  return [
    '{',
    `  "typescriptVersion": ${JSON.stringify(baseline.typescriptVersion)},`,
    `  "config": ${JSON.stringify(baseline.config)},`,
    `  "flags": ${JSON.stringify(baseline.flags)},`,
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
  const at = (file, code) => (lines[`${file}|${code}`] ? ` (lines ${lines[`${file}|${code}`].join(', ')})` : '');
  const mismatch = verdict.versionMismatch;
  return [
    ...(verdict.configProblems ?? []).map((problem) => `CONFIG   ${problem}`),
    ...(mismatch ? [`VERSION  the baseline records TypeScript ${mismatch.baseline}; ${mismatch.installed} is installed (run --ratchet after the bump)`] : []),
    ...verdict.suppressions.map((s) => `SUPPRESSED ${s.file}:${s.line}: ${s.directive} (suppressions are banned in production; fix the hit)`),
    ...verdict.unlisted.map((u) => `UNLISTED ${u.file}: ${u.code} x${u.count}${at(u.file, u.code)} (fix it in code)`),
    ...verdict.grown.map((g) => `GROWN    ${g.file}: ${g.code} ${g.allowed} -> ${g.count}${at(g.file, g.code)} (fix it in code)`),
    ...verdict.stale.map((s) => `STALE    ${s.file}: ${s.code} ${s.allowed} -> ${s.count}${s.gone ? ' (no hit left in the file)' : ''}; run --ratchet`),
  ];
}
