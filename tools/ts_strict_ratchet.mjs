#!/usr/bin/env node
// ---------------------------------------------------------------------------
// TypeScript strict ratchet (2026-09-21 audit stack-07 items 3-5).
//
// Runs the pinned tsc (frontend/node_modules/typescript) over
// frontend/tsconfig.strict.json (the build config plus the strict flags that
// still have hits, over production files only) and compares the hits with
// tools/ts_strict_ratchet_baseline.json keyed by FILE + TS CODE + COUNT (never
// by line). The rules and the baseline format live in
// tools/ts_strict_ratchet_baseline.mjs.
//
//   --check <baseline>     exit 1 on an UNLISTED file+code, a GROWN count, a
//                          STALE entry (lower it with --ratchet), a baseline
//                          from another TypeScript, config drift, or a TS
//                          suppression comment in production code.
//   --ratchet <baseline>   lower counts, drop fixed or deleted files, accept a
//                          new typescriptVersion; refuses while anything is
//                          unlisted, grown, drifted or suppressed.
//   --write-baseline <p>   bootstrap a new baseline; refuses to overwrite.
//
// It fails closed on a tsc exit other than 0 or 2, on an error without a
// location (a config error), and when the files tsc lists under frontend/src
// differ from the tool's own enumeration of production files (a vacuous or
// drifted program). A new hit is fixed in code, never added to the baseline.
//
// Usage: node tools/ts_strict_ratchet.mjs --check tools/ts_strict_ratchet_baseline.json
// ---------------------------------------------------------------------------
import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import {
  aggregate,
  bootstrapBaseline,
  evaluateBaseline,
  findTsSuppressions,
  formatVerdict,
  isProductionSource,
  listedFileProblems,
  parseTscOutput,
  posix,
  ratchetBaseline,
  SCOPE_REL,
  serializeBaseline,
  validateBaseline,
  validateStrictConfig,
} from './ts_strict_ratchet_baseline.mjs';

export * from './ts_strict_ratchet_baseline.mjs';

export const REPO_ROOT = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
export const FRONTEND_DIR = path.join(REPO_ROOT, 'frontend');
const TYPESCRIPT_DIR = path.join(FRONTEND_DIR, 'node_modules', 'typescript');

/** The installed TypeScript's version (frontend/node_modules/typescript). */
export function installedVersion() {
  return JSON.parse(readFileSync(path.join(TYPESCRIPT_DIR, 'package.json'), 'utf8')).version;
}

/** Comment-tolerant tsconfig read (tsconfig allows // comments and trailing commas). */
export function readTsconfig(file) {
  const text = readFileSync(file, 'utf8')
    .replace(/("(?:[^"\\]|\\.)*")|\/\/[^\n]*|\/\*[\s\S]*?\*\//g, (match, string) => string ?? '')
    .replace(/,(\s*[}\]])/g, '$1');
  return JSON.parse(text);
}

/** Production files under frontend/src, repo-relative POSIX (what the strict program must list). */
export function productionFiles() {
  const dir = path.join(FRONTEND_DIR, 'src');
  return readdirSync(dir, { recursive: true })
    .map((file) => posix(path.join('src', String(file))))
    .filter(isProductionSource)
    .map((rel) => `frontend/${rel}`)
    .sort();
}

/** Run tsc over `config` (default tsconfig.strict.json); fail-closed parse. */
export function runStrictTsc({ config = path.join(FRONTEND_DIR, 'tsconfig.strict.json') } = {}) {
  const tsc = path.join(TYPESCRIPT_DIR, 'bin', 'tsc');
  const run = spawnSync(process.execPath, [tsc, '-p', config, '--noEmit', '--pretty', 'false', '--listFiles'], {
    cwd: FRONTEND_DIR,
    encoding: 'utf8',
    maxBuffer: 256 * 1024 * 1024,
  });
  if (run.error) throw run.error;
  if (run.status !== 0 && run.status !== 2) throw new Error(`tsc exited ${run.status}:\n${run.stdout}${run.stderr}`);
  const { hits, listed } = parseTscOutput(run.stdout);
  if (run.status === 2 && hits.length === 0) throw new Error(`tsc exited 2 with no located error:\n${run.stdout.slice(0, 2000)}${run.stderr}`);
  const repoListed = listed.map((file) => posix(path.relative(REPO_ROOT, path.resolve(FRONTEND_DIR, file))));
  return { ...aggregate(hits, { cwd: FRONTEND_DIR, root: REPO_ROOT }), listed: repoListed, hitCount: hits.length };
}

function scanSuppressions(files) {
  return findTsSuppressions(files.map((file) => ({ file, text: readFileSync(path.join(REPO_ROOT, file), 'utf8') })));
}

function configProblems() {
  return validateStrictConfig({
    buildConfig: readTsconfig(path.join(FRONTEND_DIR, 'tsconfig.json')),
    strictConfig: readTsconfig(path.join(FRONTEND_DIR, 'tsconfig.strict.json')),
  });
}

const USAGE = 'usage: ts_strict_ratchet.mjs --check <baseline> | --ratchet <baseline> | --write-baseline <new path>';

function parseArgs(argv) {
  if (argv.length !== 2 || !['--check', '--ratchet', '--write-baseline'].includes(argv[0]) || argv[1].startsWith('--')) {
    throw new Error(USAGE);
  }
  return { mode: argv[0].slice(2), baseline: path.resolve(argv[1]) };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const version = installedVersion();
  const drift = configProblems();
  if (drift.length > 0 && args.mode !== 'check') throw new Error(`the tsconfig drifted:\n  ${drift.join('\n  ')}`);
  const expected = productionFiles();
  const run = runStrictTsc();
  const vacuity = listedFileProblems(expected, run.listed);
  if (vacuity.length > 0) throw new Error(vacuity.join('\n'));
  const suppressions = scanSuppressions(expected);
  const summary = `TypeScript ${version} strict program over ${SCOPE_REL}: ${expected.length} production files, ${run.hitCount} hit(s) in ${Object.keys(run.measured).length} file(s)`;
  if (args.mode === 'write-baseline') {
    if (existsSync(args.baseline)) throw new Error(`${args.baseline} exists: --write-baseline only bootstraps (lower one with --ratchet)`);
    if (suppressions.length > 0) throw new Error(formatVerdict({ suppressions, unlisted: [], grown: [], stale: [] }).join('\n'));
    writeFileSync(args.baseline, serializeBaseline(bootstrapBaseline(run.measured, version)));
    console.log(`${summary}\nWrote ${Object.keys(run.measured).length} files to ${args.baseline}`);
    return 0;
  }
  const baseline = JSON.parse(readFileSync(args.baseline, 'utf8'));
  const problems = validateBaseline(baseline);
  if (problems.length > 0) throw new Error(`${args.baseline} is malformed:\n  ${problems.join('\n  ')}`);
  if (args.mode === 'ratchet') {
    const next = ratchetBaseline(run.measured, baseline, { version, suppressions, configProblems: drift });
    writeFileSync(args.baseline, serializeBaseline(next));
    console.log(`${summary}\nRatcheted ${args.baseline}: ${Object.keys(baseline.files).length} -> ${Object.keys(next.files).length} files`);
    return 0;
  }
  const verdict = evaluateBaseline(run.measured, baseline, { version, suppressions, configProblems: drift });
  const failures = formatVerdict(verdict, run.lines);
  console.log(summary);
  if (failures.length > 0) {
    console.error(`${failures.join('\n')}\n\nTypeScript strict ratchet FAILED: fix a new hit in code; lower a fixed one with --ratchet (tools/ts_strict_ratchet.mjs).`);
    return 1;
  }
  console.log(`TypeScript strict ratchet OK: no new, grown, stale or suppressed hit (${Object.keys(baseline.files).length} baselined files).`);
  return 0;
}

// Symlink-safe entry guard: Node runs the realpath of the main module.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(`ts_strict_ratchet: ${error.message}`);
    process.exitCode = 1;
  }
}
