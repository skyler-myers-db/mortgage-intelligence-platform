#!/usr/bin/env node
// ---------------------------------------------------------------------------
// React Compiler coverage report (audit finding runtime-03).
//
// React Compiler 1.0 bails out of a whole function when it meets a construct
// it cannot model (a value block inside try/finally, a `throw` inside
// try/catch, ...). A bailout is silent: the build still succeeds, the
// react-hooks lint does not report these `Todo` bailouts, and the component
// simply ships unmemoized. This tool makes the gap visible.
//
// It runs `babel-plugin-react-compiler` over frontend/src with the SAME
// options the production build uses (frontend/vite.config.ts ->
// `reactCompilerPreset()` with no arguments -> plugin options `{}`), the same
// parser plugins `@rolldown/plugin-babel` applies per extension, and the
// plugin's `logger` hook. Both packages are resolved from
// frontend/node_modules, so the report always describes the installed
// compiler.
//
// READ-ONLY: nothing is written unless `--json <path>` is passed. It is a
// REPORT in this wave, not a gate: the exit code is 0 unless the tool itself
// cannot run (missing dependency, unparseable source file).
//
// Usage (from the repo root):
//   node tools/react_compiler_coverage.mjs                 # table + JSON summary
//   node tools/react_compiler_coverage.mjs --only-issues   # hide clean files
//   node tools/react_compiler_coverage.mjs --file frontend/src/components/mortgage/USChoroplethMap.tsx
//   node tools/react_compiler_coverage.mjs --include-ts    # also custom hooks in .ts
//   node tools/react_compiler_coverage.mjs --json /tmp/coverage.json
// ---------------------------------------------------------------------------
import { globSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
const frontendDir = path.join(repoRoot, 'frontend');
const frontendRequire = createRequire(path.join(frontendDir, 'package.json'));

// Mirrors frontend/vite.config.ts: `reactCompilerPreset()` is called with no
// arguments, which @vitejs/plugin-react turns into
// `['babel-plugin-react-compiler', {}]`. Keep this object in step with that
// call; the only addition here is the logger.
const COMPILER_OPTIONS = {};

// Mirrors @rolldown/plugin-babel's per-extension parser overrides.
const PARSER_PLUGINS = {
  '.tsx': ['typescript', 'jsx'],
  '.ts': ['typescript'],
  '.jsx': ['jsx'],
  '.js': [],
};

// Resolved once, lazily, from frontend/node_modules. The plugin is passed to
// Babel as a module object (not a name), so the report does not depend on the
// process cwd and `analyzeFile` can be called from a test.
let toolchain = null;
function loadToolchain() {
  if (toolchain) return toolchain;
  const babel = frontendRequire('@babel/core');
  // Fail loudly (not as an empty report) when the compiler is not installed.
  const compilerModule = frontendRequire('babel-plugin-react-compiler');
  const compilerPlugin = compilerModule.default ?? compilerModule;
  // The directives the compiler itself treats as an opt-out, read from the
  // installed plugin so the report cannot drift from it.
  const optOutDirectives = new Set(compilerModule.OPT_OUT_DIRECTIVES ?? ['use no memo', 'use no forget']);
  const compilerVersion = frontendRequire('babel-plugin-react-compiler/package.json').version;
  toolchain = { babel, compilerPlugin, optOutDirectives, compilerVersion };
  return toolchain;
}

function parseArgs(argv) {
  const args = { files: [], json: null, onlyIssues: false, includeTs: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--file') {
      args.files.push(argv[i + 1]);
      i += 1;
    } else if (arg === '--json') {
      args.json = argv[i + 1];
      i += 1;
    } else if (arg === '--only-issues') {
      args.onlyIssues = true;
    } else if (arg === '--include-ts') {
      args.includeTs = true;
    } else if (arg === '--help' || arg === '-h') {
      args.help = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return args;
}

function discoverFiles(includeTs) {
  const patterns = includeTs
    ? ['frontend/src/**/*.tsx', 'frontend/src/**/*.ts']
    : ['frontend/src/**/*.tsx'];
  const found = patterns.flatMap((pattern) => globSync(pattern, { cwd: repoRoot }));
  return found
    .filter((file) => !/\.test\.tsx?$/.test(file))
    .filter((file) => !file.endsWith('.d.ts'))
    .filter((file) => !file.startsWith('frontend/src/test/'))
    .sort();
}

// A tiny Babel plugin that records real opt-out DIRECTIVES (not strings that
// merely look like one) and whether each is module-scoped. A module-scoped
// opt-out is the important case: the compiler still analyses every function
// and logs CompileSuccess for it, but emits nothing.
function optOutCollector(pragmas, optOutDirectives) {
  return () => ({
    name: 'mip-react-compiler-opt-out-collector',
    visitor: {
      Program: {
        enter(programPath) {
          const record = (directive, scope) => {
            if (!optOutDirectives.has(directive.value.value)) return;
            pragmas.push({
              line: directive.loc?.start?.line ?? null,
              directive: directive.value.value,
              scope,
            });
          };
          for (const directive of programPath.node.directives) record(directive, 'module');
          programPath.traverse({
            Function(fnPath) {
              const body = fnPath.node.body;
              if (body.type !== 'BlockStatement') return;
              for (const directive of body.directives) record(directive, 'function');
            },
          });
        },
      },
    },
  });
}

function describeDetail(detail) {
  // `detail` is a CompilerErrorDetail or a CompilerDiagnostic; both expose
  // reason / description / category / severity getters and primaryLocation().
  const loc = typeof detail.primaryLocation === 'function' ? detail.primaryLocation() : null;
  const located = loc && typeof loc === 'object' && loc.start ? loc : null;
  return {
    reason: String(detail.reason ?? 'unknown'),
    description: detail.description ? String(detail.description) : null,
    category: detail.category ? String(detail.category) : null,
    severity: detail.severity ? String(detail.severity) : null,
    line: located ? located.start.line : null,
    column: located ? located.start.column : null,
  };
}

/**
 * Run the production compiler configuration over one file and describe what
 * it emitted. `relFile` is repo-relative (e.g.
 * `frontend/src/components/mortgage/USChoroplethMap.tsx`); pass `source` to
 * analyze text that is not on disk (a test's before/after probe).
 */
export function analyzeFile(relFile, source = null) {
  const { babel, compilerPlugin, optOutDirectives } = loadToolchain();
  const absFile = path.join(repoRoot, relFile);
  const text = source ?? readFileSync(absFile, 'utf8');
  const compiled = [];
  const pragmas = [];
  const errors = [];
  const skips = [];
  const pipelineErrors = [];
  const logger = {
    logEvent(_filename, event) {
      const fnLine = event.fnLoc?.start?.line ?? null;
      if (event.kind === 'CompileSuccess') {
        compiled.push({
          name: event.fnName ?? '(anonymous)',
          line: fnLine,
          memoSlots: event.memoSlots,
          memoBlocks: event.memoBlocks,
        });
      } else if (event.kind === 'CompileError') {
        errors.push({ fnLine, ...describeDetail(event.detail) });
      } else if (event.kind === 'CompileSkip') {
        skips.push({ fnLine, reason: event.reason, line: event.loc?.start?.line ?? null });
      } else if (event.kind === 'PipelineError') {
        pipelineErrors.push({ fnLine, data: String(event.data) });
      }
    },
  };
  const result = babel.transformSync(text, {
    filename: absFile,
    cwd: frontendDir,
    babelrc: false,
    configFile: false,
    code: true,
    ast: false,
    sourceMaps: false,
    parserOpts: {
      sourceType: 'module',
      allowAwaitOutsideFunction: true,
      plugins: PARSER_PLUGINS[path.extname(relFile)] ?? [],
    },
    plugins: [
      optOutCollector(pragmas, optOutDirectives),
      [compilerPlugin, { ...COMPILER_OPTIONS, logger }],
    ],
  });
  const moduleOptOut = pragmas.some((pragma) => pragma.scope === 'module');
  // The compiled output imports `c` (the memo cache) from the compiler
  // runtime. No import means the file ships with zero compiler memoization,
  // whatever the events said.
  const emitsMemoCache = /from\s+["']react\/compiler-runtime["']/.test(result?.code ?? '');
  return {
    file: relFile,
    // With a module-scoped opt-out the compiler logs CompileSuccess for a
    // function it then throws away, so `emitted` is what actually ships.
    compiledFunctions: compiled.map((fn) => ({ ...fn, emitted: !moduleOptOut })),
    compileErrors: errors,
    compileSkips: skips,
    pipelineErrors,
    optOutPragmas: pragmas,
    emitsMemoCache,
  };
}

function summarize(reports) {
  const reasonCounts = {};
  for (const report of reports) {
    for (const error of report.compileErrors) {
      reasonCounts[error.reason] = (reasonCounts[error.reason] ?? 0) + 1;
    }
  }
  const candidates = reports.filter(
    (r) =>
      r.compiledFunctions.length > 0
      || r.compileErrors.length > 0
      || r.compileSkips.length > 0
      || r.optOutPragmas.length > 0,
  );
  return {
    filesScanned: reports.length,
    filesWithCompilableCode: candidates.length,
    filesEmittingMemoCache: reports.filter((r) => r.emitsMemoCache).length,
    filesWithCompileErrors: reports.filter((r) => r.compileErrors.length > 0).length,
    filesWithOptOutPragma: reports.filter((r) => r.optOutPragmas.length > 0).length,
    // The headline number: a file the compiler looked at and emitted NOTHING
    // for (every function bailed out, was skipped, or the file opted out).
    filesEmittingNothing: candidates.filter((r) => !r.emitsMemoCache).map((r) => r.file),
    compiledFunctions: reports.reduce(
      (n, r) => n + r.compiledFunctions.filter((fn) => fn.emitted).length,
      0,
    ),
    functionsDiscardedByModuleOptOut: reports.reduce(
      (n, r) => n + r.compiledFunctions.filter((fn) => !fn.emitted).length,
      0,
    ),
    compileErrors: reports.reduce((n, r) => n + r.compileErrors.length, 0),
    compileSkips: reports.reduce((n, r) => n + r.compileSkips.length, 0),
    pipelineErrors: reports.reduce((n, r) => n + r.pipelineErrors.length, 0),
    optOutPragmas: reports.reduce((n, r) => n + r.optOutPragmas.length, 0),
    compileErrorReasons: Object.fromEntries(
      Object.entries(reasonCounts).sort((a, b) => b[1] - a[1]),
    ),
  };
}

function printReport(report) {
  const status = report.emitsMemoCache ? 'COMPILED' : 'NOTHING ';
  console.log(
    `${status} ${report.file}`
      + `  compiled=${report.compiledFunctions.filter((fn) => fn.emitted).length}`
      + ` errors=${report.compileErrors.length}`
      + ` skips=${report.compileSkips.length}`
      + ` pragmas=${report.optOutPragmas.length}`,
  );
  for (const fn of report.compiledFunctions) {
    const label = fn.emitted ? 'ok    ' : 'unused';
    const note = fn.emitted ? '' : '  (compiles, but the module opt-out discards it)';
    console.log(`    ${label} ${fn.name} (line ${fn.line ?? '?'}) memoSlots=${fn.memoSlots}${note}`);
  }
  for (const error of report.compileErrors) {
    const where = error.line === null ? `fn@${error.fnLine ?? '?'}` : `${error.line}:${error.column}`;
    console.log(`    bail  ${where} [${error.category ?? 'Unknown'}] ${error.reason}`);
    if (error.description) console.log(`          ${error.description}`);
  }
  for (const skip of report.compileSkips) {
    console.log(`    skip  fn@${skip.fnLine ?? '?'} ${skip.reason}`);
  }
  for (const failure of report.pipelineErrors) {
    console.log(`    crash fn@${failure.fnLine ?? '?'} ${failure.data}`);
  }
  for (const pragma of report.optOutPragmas) {
    console.log(`    optout line ${pragma.line ?? '?'} '${pragma.directive}' (${pragma.scope} scope)`);
  }
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(
      'Usage: node tools/react_compiler_coverage.mjs'
        + ' [--file <repo-relative path>]... [--only-issues] [--include-ts] [--json <path>]',
    );
    return;
  }
  const { compilerVersion } = loadToolchain();

  // `--file` paths are resolved against the invoking cwd, like any CLI path.
  const files = args.files.length > 0
    ? args.files.map((file) => path.relative(repoRoot, path.resolve(file)))
    : discoverFiles(args.includeTs);
  const reports = files.map((file) => analyzeFile(file));

  for (const report of reports) {
    const hasIssue =
      !report.emitsMemoCache
        && (report.compileErrors.length > 0
          || report.compileSkips.length > 0
          || report.optOutPragmas.length > 0);
    const partial = report.compileErrors.length > 0 || report.optOutPragmas.length > 0;
    if (args.onlyIssues && !hasIssue && !partial) continue;
    printReport(report);
  }

  const summary = { compilerVersion, compilerOptions: COMPILER_OPTIONS, ...summarize(reports) };
  console.log('\nSUMMARY');
  console.log(JSON.stringify(summary, null, 2));
  if (args.json) {
    writeFileSync(path.resolve(args.json), `${JSON.stringify({ summary, files: reports }, null, 2)}\n`);
    console.log(`\nWrote ${args.json}`);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.join(repoRoot, 'tools', 'react_compiler_coverage.mjs')) {
  main();
}
