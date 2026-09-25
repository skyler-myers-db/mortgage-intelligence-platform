#!/usr/bin/env node
// ---------------------------------------------------------------------------
// oxlint jsx-a11y ratchet (audit a11y-05 item 2).
//
// Runs the pinned oxlint (frontend/node_modules/oxlint) with
// frontend/.oxlintrc.json, which enables the jsx-a11y plugin only and names
// every rule the pinned version lists explicitly, over frontend/src, and
// compares the hits with frontend/oxlint-baseline.json keyed by
// FILE + RULE + COUNT (never by line). The rules and the baseline format live
// in tools/oxlint_ratchet_baseline.mjs.
//
//   --check <baseline>     exit 1 on an UNLISTED file+rule key, a GROWN count,
//                          a STALE entry (a lower count, or no hit left in the
//                          file), a baseline oxlintVersion other than the
//                          installed one, or a suppression directive.
//   --ratchet <baseline>   lower counts, drop fixed or deleted files, accept a
//                          new oxlintVersion; refuses while anything is
//                          unlisted, grown or suppressed. Repeatable
//                          `--moved-from <old> --moved-to <new>` records a
//                          pure move: the (rule, count) pairs that went stale
//                          in <old> transfer to the unlisted keys of the NEW
//                          file <new>, and no per-rule total may grow.
//   --write-baseline <p>   bootstrap a new baseline; refuses to overwrite.
//
// It fails closed on unparseable output or a config error, on any diagnostic
// that is not jsx-a11y (config drift), on a vacuous run (no file linted, or a
// rule count other than the config's), and when the installed oxlint lists a
// jsx-a11y rule the config does not name (a bump cannot widen the gate
// silently). A new hit is fixed in code, never added to the baseline.
//
// Usage: npm --prefix frontend run lint:a11y  (= --check oxlint-baseline.json)
//        node tools/oxlint_ratchet.mjs --ratchet frontend/oxlint-baseline.json
// ---------------------------------------------------------------------------
import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import {
  aggregate,
  bootstrapBaseline,
  CONFIG_REL,
  enabledRules,
  evaluateBaseline,
  findSuppressions,
  formatVerdict,
  parseOxlintOutput,
  posix,
  ratchetBaseline,
  ruleTotals,
  SCOPE_REL,
  serializeBaseline,
  validateBaseline,
  validateConfig,
} from './oxlint_ratchet_baseline.mjs';

export * from './oxlint_ratchet_baseline.mjs';

export const REPO_ROOT = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
export const FRONTEND_DIR = path.join(REPO_ROOT, 'frontend');
export const CONFIG_PATH = path.join(FRONTEND_DIR, '.oxlintrc.json');
const OXLINT_DIR = path.join(FRONTEND_DIR, 'node_modules', 'oxlint');
const SOURCE_FILE = /\.[cm]?[jt]sx?$/;

/** The installed oxlint's version (frontend/node_modules/oxlint). */
export function installedVersion() {
  return JSON.parse(readFileSync(path.join(OXLINT_DIR, 'package.json'), 'utf8')).version;
}

function oxlint(args, cwd) {
  const run = spawnSync(process.execPath, [path.join(OXLINT_DIR, 'bin', 'oxlint'), ...args], {
    cwd,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  if (run.error) throw run.error;
  // oxlint exits 1 when it reports an error-level hit; the JSON decides the rest.
  if (run.status !== 0 && run.status !== 1) throw new Error(`oxlint exited ${run.status}:\n${run.stdout}${run.stderr}`);
  return run.stdout;
}

/** The jsx-a11y rule names the installed oxlint lists (`--rules --format json`). */
export function installedRules() {
  return JSON.parse(oxlint(['--rules', '--format', 'json'], FRONTEND_DIR))
    .filter((rule) => rule.scope === 'jsx_a11y')
    .map((rule) => `jsx-a11y/${rule.value}`)
    .sort();
}

/**
 * Lint `targets` (relative to `cwd`) with `config` and return the parsed,
 * fail-closed run: `{ diagnostics, fileCount, ruleCount, measured, lines }`,
 * with `measured` keyed by POSIX paths relative to `root`.
 */
export function runOxlint({ targets = ['src'], cwd = FRONTEND_DIR, config = CONFIG_PATH, root = REPO_ROOT } = {}) {
  const parsedConfig = JSON.parse(readFileSync(config, 'utf8'));
  const problems = validateConfig(parsedConfig);
  if (problems.length > 0) throw new Error(`${config} drifted:\n  ${problems.join('\n  ')}`);
  const parsed = parseOxlintOutput(oxlint(['-c', config, '--format', 'json', ...targets], cwd), enabledRules(parsedConfig).length);
  return { ...parsed, ...aggregate(parsed.diagnostics, { cwd, root }) };
}

function scanSuppressions() {
  const dir = path.join(FRONTEND_DIR, 'src');
  const files = readdirSync(dir, { recursive: true }).map(String).filter((file) => SOURCE_FILE.test(file)).sort();
  return findSuppressions(files.map((file) => ({ file: posix(path.join(SCOPE_REL, file)), text: readFileSync(path.join(dir, file), 'utf8') })));
}

const USAGE = 'usage: oxlint_ratchet.mjs --check <baseline> | --ratchet <baseline> [--moved-from <old> --moved-to <new>]...'
  + ' | --write-baseline <new path>';

function parseArgs(argv) {
  const args = { mode: null, baseline: null, moves: [] };
  const from = [];
  for (let i = 0; i < argv.length; i += 2) {
    const [flag, value] = [argv[i], argv[i + 1]];
    if (!value || value.startsWith('--')) throw new Error(`${flag} needs a path\n${USAGE}`);
    const repoPath = posix(path.relative(REPO_ROOT, path.resolve(value)));
    if (flag === '--check' || flag === '--ratchet' || flag === '--write-baseline') {
      if (args.mode) throw new Error('--check, --ratchet and --write-baseline are exclusive');
      Object.assign(args, { mode: flag.slice(2), baseline: path.resolve(value) });
    } else if (flag === '--moved-from') {
      from.push(repoPath);
    } else if (flag === '--moved-to') {
      args.moves.push({ from: from.shift(), to: repoPath });
    } else {
      throw new Error(`unknown argument ${flag}\n${USAGE}`);
    }
  }
  if (!args.mode) throw new Error(USAGE);
  if (from.length > 0 || args.moves.some((move) => !move.from)) throw new Error('--moved-from and --moved-to come in pairs, from first');
  if (args.moves.length > 0 && args.mode !== 'ratchet') throw new Error('--moved-from/--moved-to only go with --ratchet');
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const version = installedVersion();
  const drift = validateConfig(JSON.parse(readFileSync(CONFIG_PATH, 'utf8')), installedRules());
  if (drift.length > 0) throw new Error(`${CONFIG_REL} drifted from oxlint ${version}:\n  ${drift.join('\n  ')}`);
  const run = runOxlint();
  const suppressions = scanSuppressions();
  const totals = Object.entries(ruleTotals(run.measured)).map(([rule, count]) => `  ${String(count).padStart(4)}  ${rule}`);
  if (args.mode === 'write-baseline') {
    if (existsSync(args.baseline)) throw new Error(`${args.baseline} exists: --write-baseline only bootstraps (lower one with --ratchet)`);
    if (suppressions.length > 0) throw new Error(formatVerdict({ suppressions, unlisted: [], grown: [], stale: [] }).join('\n'));
    writeFileSync(args.baseline, serializeBaseline(bootstrapBaseline(run.measured, version)));
    console.log(`Wrote ${Object.keys(run.measured).length} files to ${args.baseline}; per-rule totals:\n${totals.join('\n')}`);
    return 0;
  }
  const baseline = JSON.parse(readFileSync(args.baseline, 'utf8'));
  const problems = validateBaseline(baseline);
  if (problems.length > 0) throw new Error(`${args.baseline} is malformed:\n  ${problems.join('\n  ')}`);
  if (args.mode === 'ratchet') {
    const recorded = new Date().toISOString().slice(0, 10);
    const next = ratchetBaseline(run.measured, baseline, { version, suppressions, moves: args.moves, recorded });
    writeFileSync(args.baseline, serializeBaseline(next));
    console.log(`Ratcheted ${args.baseline}: ${Object.keys(baseline.files).length} -> ${Object.keys(next.files).length} files, ${args.moves.length} move(s)`);
    return 0;
  }
  const verdict = evaluateBaseline(run.measured, baseline, { version, suppressions });
  const failures = formatVerdict(verdict, run.lines);
  console.log(`oxlint ${version} jsx-a11y over ${SCOPE_REL}: ${run.fileCount} files, ${run.ruleCount} rules; per-rule totals:\n${totals.join('\n')}`);
  if (failures.length > 0) {
    console.error(`${failures.join('\n')}\n\noxlint jsx-a11y ratchet FAILED: fix a new hit in code; lower a fixed one with --ratchet (tools/oxlint_ratchet.mjs).`);
    return 1;
  }
  console.log(`oxlint jsx-a11y ratchet OK: no new, grown, stale or suppressed hit (${Object.keys(baseline.files).length} baselined files).`);
  return 0;
}

// Symlink-safe entry guard: Node runs the realpath of the main module.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(`oxlint_ratchet: ${error.message}`);
    process.exitCode = 1;
  }
}
