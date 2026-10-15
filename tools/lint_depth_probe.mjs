#!/usr/bin/env node
// ---------------------------------------------------------------------------
// Lint-depth probe (2026-09-21 audit critic-08, stack-07 items 3-5 and
// quality-10 step 2; W5e w5-compiler-lint item 0).
//
// A self-contained, read-only probe that any W5e worktree can run BEFORE the
// lint-depth gates reach it, to find the hits its own new code will meet:
//
//   git show ux/w5e-compiler-lint:tools/lint_depth_probe.mjs > "$TMPDIR/lint_depth_probe.mjs"
//   node "$TMPDIR/lint_depth_probe.mjs" --repo <worktree> [--files <paths...>]
//
// It imports nothing from the repo it probes: eslint, the typescript-eslint
// parser and plugin, and typescript all load from <repo>/frontend/node_modules,
// and the ban and the flag list are embedded here (src/test/lintDepthProbe.test.ts
// pins both to the real eslint.config.js TITLE_BAN and tools/ts_strict_ratchet_baseline.mjs
// REQUIRED_FLAGS). It applies NO baseline, allowlist or suppression:
//
//   1. ESLint with ONLY the embedded TITLE_BAN (no-restricted-syntax) and the
//      three typed rules (no-floating-promises, no-misused-promises,
//      no-base-to-string) through projectService;
//   2. tsc over frontend/tsconfig.json with all eight strict flags (and the
//      ES2022 target/lib) as CLI overrides.
//
// --files takes paths relative to <repo> (or absolute). Without it, every
// production file under frontend/src is linted (tests, src/test and src/mocks
// excluded) and every tsc hit in frontend/src is printed.
//
// Exit 0: no hit. Exit 1: hits were printed. Exit 2: the probe could not run.
// ---------------------------------------------------------------------------
import { spawnSync } from 'node:child_process';
import { existsSync, globSync, realpathSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

/** A verbatim copy of frontend/eslint.config.js TITLE_BAN (pinned by lintDepthProbe.test.ts). */
export const TITLE_BAN = [
  {
    selector: "JSXOpeningElement[name.name=/^[a-z]/]:not([name.name='iframe']) > JSXAttribute[name.name='title']",
    message:
      'A native title= is mouse-only, appears late in OS chrome and never on keyboard focus or touch (audit critic-08). Use <Tooltip> (ui/Tooltip), or the Chip/Button `tooltip` prop. An icon-only control also needs an aria-label.',
  },
  {
    selector: "JSXOpeningElement[name.name=/^(Chip|Button|EvidenceChip|Link|NavLink)$/] > JSXAttribute[name.name='title']",
    message:
      'This component renders `title` as a native DOM title. Use the `tooltip` prop (Chip, Button), rely on the evidence hover card (EvidenceChip), or wrap the Link in <Tooltip>.',
  },
];

/** The three typed rules of the typed-lint block, at their default options. */
export const TYPED_RULES = [
  '@typescript-eslint/no-floating-promises',
  '@typescript-eslint/no-misused-promises',
  '@typescript-eslint/no-base-to-string',
];

/** A verbatim copy of tools/ts_strict_ratchet_baseline.mjs REQUIRED_FLAGS (pinned). */
export const PROBE_FLAGS = [
  'verbatimModuleSyntax',
  'erasableSyntaxOnly',
  'noImplicitOverride',
  'noImplicitReturns',
  'noFallthroughCasesInSwitch',
  'noUnusedLocals',
  'noUnusedParameters',
  'noUncheckedIndexedAccess',
];

/** The tsc CLI the probe runs (after `-p <tsconfig>`). */
export function tscProbeArgs() {
  return ['--noEmit', '--pretty', 'false', '--target', 'ES2022', '--lib', 'DOM,DOM.Iterable,ES2022', ...PROBE_FLAGS.map((flag) => `--${flag}`)];
}

const TEST_FILE = /\.test\.tsx?$/;
const posix = (p) => p.split(path.sep).join('/');

/** Whether a frontend-relative POSIX path is production source (the typed-lint and strict scope). */
export function isProductionSource(rel) {
  return /^src\/.+\.tsx?$/.test(rel) && !TEST_FILE.test(rel) && !rel.endsWith('.d.ts')
    && !rel.startsWith('src/test/') && !rel.startsWith('src/mocks/');
}

/** `path(l,c): error TSnnnn: message` lines -> [{file, line, code, message}]; other lines are ignored. */
export function parseTscLines(stdout) {
  const hits = [];
  for (const line of String(stdout).split(/\r?\n/)) {
    const match = /^(.+?)\((\d+),(\d+)\): error (TS\d+): (.*)$/.exec(line);
    if (match) hits.push({ file: posix(match[1]), line: Number(match[2]), code: match[4], message: match[5] });
  }
  return hits;
}

function parseArgs(argv) {
  const args = { repo: null, files: [] };
  let inFiles = false;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--repo') {
      args.repo = argv[i + 1];
      i += 1;
      inFiles = false;
    } else if (arg === '--files') {
      inFiles = true;
    } else if (inFiles && !arg.startsWith('--')) {
      args.files.push(arg);
    } else {
      throw new Error(`unknown argument ${arg}\nusage: lint_depth_probe.mjs --repo <worktree> [--files <paths...>]`);
    }
  }
  if (!args.repo) throw new Error('usage: lint_depth_probe.mjs --repo <worktree> [--files <paths...>]');
  return args;
}

async function lint(frontendDir, targets) {
  const require = createRequire(path.join(frontendDir, 'package.json'));
  const { ESLint } = require('eslint');
  const tsParser = require('@typescript-eslint/parser');
  const tsPlugin = require('@typescript-eslint/eslint-plugin');
  const eslint = new ESLint({
    cwd: frontendDir,
    overrideConfigFile: true,
    overrideConfig: [
      {
        files: ['**/*.{ts,tsx}'],
        languageOptions: {
          parser: tsParser,
          parserOptions: {
            ecmaVersion: 'latest',
            sourceType: 'module',
            ecmaFeatures: { jsx: true },
            projectService: true,
            tsconfigRootDir: frontendDir,
          },
        },
        plugins: { '@typescript-eslint': tsPlugin },
        linterOptions: { reportUnusedDisableDirectives: 'off', noInlineConfig: true },
        rules: {
          'no-restricted-syntax': ['error', ...TITLE_BAN],
          ...Object.fromEntries(TYPED_RULES.map((rule) => [rule, 'error'])),
        },
      },
    ],
  });
  const results = await eslint.lintFiles(targets);
  const hits = [];
  for (const result of results) {
    for (const message of result.messages) {
      if (message.fatal) throw new Error(`${result.filePath} did not parse: ${message.message}`);
      const rule = message.ruleId === 'no-restricted-syntax' ? 'TITLE_BAN' : message.ruleId;
      hits.push({ file: posix(path.relative(frontendDir, result.filePath)), line: message.line, rule, message: message.message });
    }
  }
  return hits;
}

function strictTsc(frontendDir) {
  const tsc = path.join(frontendDir, 'node_modules', 'typescript', 'bin', 'tsc');
  const run = spawnSync(process.execPath, [tsc, '-p', path.join(frontendDir, 'tsconfig.json'), ...tscProbeArgs()], {
    cwd: frontendDir,
    encoding: 'utf8',
    maxBuffer: 128 * 1024 * 1024,
  });
  if (run.error) throw run.error;
  if (run.status !== 0 && run.status !== 2) throw new Error(`tsc exited ${run.status}:\n${run.stdout}${run.stderr}`);
  const global = String(run.stdout).split(/\r?\n/).find((line) => /^error TS\d+/.test(line));
  if (global) throw new Error(`tsc reported a global error (a config problem): ${global}`);
  return parseTscLines(run.stdout).map((hit) => ({ ...hit, file: posix(path.relative(frontendDir, path.resolve(frontendDir, hit.file))) }));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const repo = path.resolve(args.repo);
  const frontendDir = path.join(repo, 'frontend');
  if (!existsSync(path.join(frontendDir, 'node_modules', 'eslint'))) throw new Error(`${frontendDir}/node_modules has no eslint`);
  const relFiles = args.files.length > 0
    ? args.files.map((file) => posix(path.relative(frontendDir, path.resolve(repo, file))))
    : globSync('src/**/*.{ts,tsx}', { cwd: frontendDir }).map(posix).filter(isProductionSource).sort();
  const lintable = relFiles.filter(isProductionSource);
  const wanted = new Set(relFiles);
  const lintHits = lintable.length > 0 ? await lint(frontendDir, lintable) : [];
  const tscHits = strictTsc(frontendDir).filter((hit) => (args.files.length > 0 ? wanted.has(hit.file) : hit.file.startsWith('src/')));
  const byFile = new Map();
  for (const hit of [...lintHits, ...tscHits.map((h) => ({ ...h, rule: h.code }))]) {
    if (!byFile.has(hit.file)) byFile.set(hit.file, []);
    byFile.get(hit.file).push(hit);
  }
  for (const file of [...byFile.keys()].sort()) {
    const hits = byFile.get(file).sort((a, b) => a.line - b.line);
    console.log(`${file} (${hits.length})`);
    for (const hit of hits) console.log(`  ${hit.line}: ${hit.rule}: ${hit.message.split('\n')[0]}`);
  }
  const skipped = relFiles.filter((file) => !isProductionSource(file));
  if (skipped.length > 0) console.log(`not linted (tests, src/test, src/mocks or outside frontend/src): ${skipped.join(', ')}`);
  console.log(`lint-depth probe: ${lintable.length} file(s) linted, ${lintHits.length} lint hit(s), ${tscHits.length} strict-tsc hit(s).`);
  return lintHits.length + tscHits.length > 0 ? 1 : 0;
}

// Symlink-safe entry guard: Node runs the realpath of the main module.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  main().then(
    (code) => { process.exitCode = code; },
    (error) => {
      console.error(`lint_depth_probe: ${error.message}`);
      process.exitCode = 2;
    },
  );
}
