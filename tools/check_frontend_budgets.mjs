#!/usr/bin/env node
import { brotliCompressSync, constants as zlibConstants, gzipSync } from 'node:zlib';
import { appendFileSync, readdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { initialClosure, routeClosures, staleManifestProblems } from './build_manifest.mjs';
// quality-08: targets, the per-chunk JSON and the step summary (main() only).
import { chunkReport, evaluateTargets, loadTargets, parseBudgetFlags, summaryTable } from './frontend_budget_report.mjs';
// The gates, their headroom policy and each re-baseline's attribution.
import { KiB, budgets } from './frontend_budget_gates.mjs';

// The manifest maths, re-exported so a test (or another tool) can import the
// budget gate's exact definitions from one place.
export { initialClosure, routeClosures, staleManifestProblems };

const repoRoot = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
const distDir = path.join(repoRoot, 'frontend', 'dist');
const buildMetaDir = path.join(repoRoot, 'frontend', 'build-meta');

function bytes(n) {
  // Totals above 1 MiB also print the exact KiB so a re-baseline can cite the
  // measured actual in the same unit the gates are declared in.
  if (n >= KiB * KiB) return `${(n / (KiB * KiB)).toFixed(2)} MiB (${(n / KiB).toFixed(2)} KiB)`;
  return `${(n / KiB).toFixed(2)} KiB`;
}

/** Raw, gzip level 9 and brotli quality 11 sizes of one buffer. */
export function measureBuffer(raw) {
  return {
    bytes: raw.length,
    gzipBytes: gzipSync(raw, { level: 9 }).length,
    brBytes: brotliCompressSync(raw, {
      params: {
        [zlibConstants.BROTLI_PARAM_QUALITY]: 11,
        [zlibConstants.BROTLI_PARAM_SIZE_HINT]: raw.length,
      },
    }).length,
  };
}

/** Sum of the sizes of `files` under a `sizeOf(file)` lookup. */
export function sumSizes(files, sizeOf) {
  const total = { files: [...files], bytes: 0, gzipBytes: 0, brBytes: 0 };
  for (const file of files) {
    const size = sizeOf(file);
    total.bytes += size.bytes;
    total.gzipBytes += size.gzipBytes;
    total.brBytes += size.brBytes;
  }
  return total;
}

/** Exact, both ways: null when the count matches, the problem otherwise. */
export function fontCountProblem(actual, expected) {
  return actual === expected ? null : `font asset count is ${actual}, expected exactly ${expected}`;
}

/** Every route module needs a budget entry and every entry needs a route module. */
export function routeBudgetProblems(routeKeys, budgetRoutes) {
  const problems = [];
  for (const key of routeKeys) {
    if (!(key in budgetRoutes)) problems.push(`route module ${key} has no entry in budgets.routes`);
  }
  for (const key of Object.keys(budgetRoutes)) {
    if (!routeKeys.includes(key)) problems.push(`budgets.routes names ${key}, which is not a route module in the build manifest`);
  }
  return problems;
}

/** The largest lazy chunk, per dimension (a chunk can be largest raw but not largest br). */
export function largestPerDimension(chunks) {
  const none = { file: 'none', value: 0 };
  const largest = { bytes: none, gzipBytes: none, brBytes: none };
  for (const chunk of chunks) {
    for (const dimension of Object.keys(largest)) {
      if (chunk[dimension] > largest[dimension].value) largest[dimension] = { file: chunk.file, value: chunk[dimension] };
    }
  }
  return largest;
}

/**
 * The vendor chunks vite.config.ts declares (audit bundle-03). Exactly these
 * must be emitted: a missing one means the codeSplitting groups were dropped
 * and every app edit re-hashes the framework code again.
 */
export const EXPECTED_VENDOR_CHUNKS = ['vendor-react', 'vendor-data'];

/** The bundler's runtime-helper chunk (CommonJS interop): the one non-vendor chunk a vendor chunk may import. */
const RUNTIME_HELPER_CHUNK = 'rolldown-runtime';

/**
 * node_modules modules that a build WITHOUT the vendor groups renders only
 * outside the initial closure (verified 2026-09-24 on the wave-2 dependency
 * batch by diffing a groups-free build's chunk modules): what the first paint
 * does not need. A vendor chunk is part of every first paint, so it must
 * never hold one; `tags: ['$initial']` on each vite.config.ts group is what
 * keeps them out, and this list is what proves it. An entry ending in `/`
 * names a whole package directory; any other entry is one module id, both
 * written from `node_modules/` on (moduleKey below), whether the install is
 * a real directory or a symlink to another tree.
 *
 * The entry's static-import reach (build-modules.json `entryStaticModules`)
 * cannot stand in for this list: it follows the @tanstack barrel re-exports
 * (index.js re-exports every hook), so it names useInfiniteQuery.js even
 * though tree-shaking renders it only in the lazy Console chunk.
 *
 * Kept exact against the build (lazyOnlyVendorProblems): an entry that
 * matches no rendered module fails (a renamed or dropped module would blind
 * the check), and so does a module of a vendor package that only a lazy
 * chunk renders but the list omits (a hook a lazy route starts using).
 * Update it deliberately, at a dependency batch or when such a failure names
 * a module; if the first paint starts to need a listed module, remove it.
 */
export const LAZY_ONLY_VENDOR_MODULES = [
  // The lazy Console's infinite audit feed.
  'node_modules/@tanstack/query-core/build/modern/infiniteQueryObserver.js',
  'node_modules/@tanstack/react-query/build/modern/useInfiniteQuery.js',
  // The Lead Queue's governed writes on TanStack mutations (wave 2
  // w2-queue-query-layer: lib/mutations, useMutation); lazy with LeadTable.
  'node_modules/@tanstack/query-core/build/modern/mutationObserver.js',
  'node_modules/@tanstack/react-query/build/modern/useMutation.js',
  'node_modules/@tanstack/react-query/build/modern/useMutationState.js',
  // LeadTable's row virtualizer.
  'node_modules/@tanstack/react-virtual/',
  'node_modules/@tanstack/virtual-core/',
  // The geography map's topology.
  'node_modules/topojson-client/',
  'node_modules/us-atlas/',
  // The persisted aggregate cache (audit delivery-05, lib/queryPersist): the
  // restore's hydrate and the persist-client core load only after the actor
  // gate first opens (@tanstack/react-query-persist-client itself is a
  // re-export barrel and renders no module).
  'node_modules/@tanstack/query-core/build/modern/hydration.js',
  'node_modules/@tanstack/query-persist-client-core/',
];

const NODE_MODULES = 'node_modules/';

/** The npm package a node_modules module id belongs to (`@scope/name` or `name`), or null. */
export function packageOfModule(id) {
  const at = id.lastIndexOf(NODE_MODULES);
  if (at === -1) return null;
  const [first, second] = id.slice(at + NODE_MODULES.length).split('/');
  return first.startsWith('@') ? `${first}/${second}` : first;
}

/**
 * A module id keyed by where the module sits inside node_modules, not by
 * where node_modules physically lives: from the id's last `node_modules/`
 * segment on (the rule packageOfModule uses), any other id unchanged. The
 * build resolves symlinks before vite.config.ts makes ids frontend-root
 * relative, so a worktree whose frontend/node_modules is a symlink to another
 * tree records `../../<other-tree>/frontend/node_modules/...`; every module-id
 * comparison below goes through this key so the guard reads the same in
 * either install.
 */
export function moduleKey(id) {
  const at = id.lastIndexOf(NODE_MODULES);
  return at === -1 ? id : id.slice(at);
}

/** build-modules.json with every module id replaced by its moduleKey. */
function keyedChunkModules(chunkModules) {
  return {
    chunks: Object.fromEntries(Object.entries(chunkModules.chunks).map(([file, ids]) => [file, ids.map(moduleKey)])),
    entryStaticModules: chunkModules.entryStaticModules.map(moduleKey),
  };
}

function lazyOnlyEntryMatches(entry, id) {
  return entry.endsWith('/') ? id.startsWith(entry) : id === entry;
}

/**
 * The lazy-only guard over the rendered modules of each chunk
 * (`chunkModules.chunks`, keyed by dist file):
 *  - a vendor chunk holding a listed module fails;
 *  - a listed entry that matches no rendered module anywhere fails (stale);
 *  - a module of a vendor package (one with any module in a vendor chunk)
 *    rendered in a chunk outside the initial closure must be listed, so the
 *    list stays complete as lazy routes start using more of a vendor package.
 * Module ids are compared, and named in problems, by their moduleKey.
 */
export function lazyOnlyVendorProblems(manifest, initial, chunkModules, lazyOnly = LAZY_ONLY_VENDOR_MODULES) {
  const problems = [];
  const vendorFiles = new Set(
    Object.values(manifest)
      .filter((chunk) => (chunk.name ?? '').startsWith('vendor-'))
      .map((chunk) => chunk.file),
  );
  const listed = (id) => lazyOnly.some((entry) => lazyOnlyEntryMatches(entry, id));
  const rendered = Object.entries(keyedChunkModules(chunkModules).chunks);
  const vendorPackages = new Set();
  for (const [file, ids] of rendered) {
    if (!vendorFiles.has(file)) continue;
    for (const id of ids) {
      const pkg = packageOfModule(id);
      if (pkg) vendorPackages.add(pkg);
      if (listed(id)) problems.push(`vendor chunk ${file} holds lazy-only ${id} (LAZY_ONLY_VENDOR_MODULES)`);
    }
  }
  for (const entry of lazyOnly) {
    if (!rendered.some(([, ids]) => ids.some((id) => lazyOnlyEntryMatches(entry, id)))) {
      problems.push(`LAZY_ONLY_VENDOR_MODULES entry ${entry} matches no module in the build: update the list`);
    }
  }
  const initialFiles = new Set(initial.js);
  for (const [file, ids] of rendered) {
    if (initialFiles.has(file) || vendorFiles.has(file)) continue;
    for (const id of ids) {
      const pkg = packageOfModule(id);
      if (pkg && vendorPackages.has(pkg) && !listed(id)) {
        problems.push(`${id} (vendor package ${pkg}) is rendered only in lazy chunk ${file}: add it to LAZY_ONLY_VENDOR_MODULES`);
      }
    }
  }
  return problems;
}

/**
 * Vendor chunks exist to be cached across app deploys, so each must:
 *  - be one of the expected groups, and every expected group must exist;
 *  - sit inside the initial closure (a vendor group that escaped it would be
 *    a lazy chunk named "vendor");
 *  - import only other vendor chunks or the runtime-helper chunk, never an
 *    app chunk (which would re-hash with app edits and can form a cycle);
 *  - when `chunkModules` (build-modules.json) is given, hold no lazy-only
 *    module (lazyOnlyVendorProblems above), and no module the entry cannot
 *    reach through static imports at all. That second check is only a
 *    backstop: the static reach follows barrel re-exports, so it catches a
 *    group that swallows a package the entry never imports (react-virtual)
 *    but not a lazy-only hook of a package it does (useInfiniteQuery).
 * Module ids are compared, and named in problems, by their moduleKey.
 */
export function vendorChunkProblems(manifest, initial, chunkModules = null, lazyOnly = LAZY_ONLY_VENDOR_MODULES) {
  const problems = [];
  const vendorKeys = Object.keys(manifest).filter((key) => (manifest[key].name ?? '').startsWith('vendor-'));
  const names = vendorKeys.map((key) => manifest[key].name);
  for (const expected of EXPECTED_VENDOR_CHUNKS) {
    if (!names.includes(expected)) problems.push(`vendor chunk ${expected} is missing (vite.config.ts codeSplitting groups)`);
  }
  const initialKeys = new Set(initial.keys);
  const keyed = chunkModules ? keyedChunkModules(chunkModules) : null;
  const reached = keyed ? new Set(keyed.entryStaticModules) : null;
  for (const key of vendorKeys) {
    const chunk = manifest[key];
    if (!EXPECTED_VENDOR_CHUNKS.includes(chunk.name)) problems.push(`unexpected vendor chunk ${chunk.file}`);
    if (!initialKeys.has(key)) problems.push(`vendor group escaped the closure: ${chunk.file} is not in the initial closure`);
    for (const imported of chunk.imports ?? []) {
      const target = manifest[imported];
      if (!vendorKeys.includes(imported) && target?.name !== RUNTIME_HELPER_CHUNK) {
        problems.push(`vendor chunk ${chunk.file} imports app chunk ${target?.file ?? imported}`);
      }
    }
    if (reached) {
      for (const id of keyed.chunks[chunk.file] ?? []) {
        if (!reached.has(id)) problems.push(`vendor chunk ${chunk.file} holds ${id}, which the entry does not import statically`);
      }
    }
  }
  if (chunkModules) problems.push(...lazyOnlyVendorProblems(manifest, initial, chunkModules, lazyOnly));
  return problems;
}

function readBuildMeta(file, dir = buildMetaDir) {
  const abs = path.join(dir, file);
  try {
    return JSON.parse(readFileSync(abs, 'utf8'));
  } catch (err) {
    console.error(`Frontend budget check requires ${abs}.`);
    console.error('Run `npm --prefix frontend run build` (tools/postbuild_artifacts.mjs moves it there).');
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
}

function main(argv = process.argv.slice(2)) {
  // Flags (quality-08): --json <path> writes the per-chunk report; --base
  // <json> adds base and delta columns to the step summary; --report-only
  // never fails on a gate (the bundle-delta job's base build); --dist and
  // --build-meta point at another build (default: this tree's).
  const options = parseBudgetFlags(argv, { distDir, buildMetaDir });
  const dist = path.resolve(options.distDir);
  const assets = path.join(dist, 'assets');
  let files;
  try {
    files = readdirSync(assets);
  } catch (err) {
    console.error(`Frontend budget check requires a built Vite dist at ${assets}.`);
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
  const targets = loadTargets(path.join(repoRoot, 'tools', 'frontend_budget_targets.json'));
  const manifest = readBuildMeta('build-manifest.json', path.resolve(options.buildMetaDir));
  const chunkModules = readBuildMeta('build-modules.json', path.resolve(options.buildMetaDir));

  const overages = [];
  const failIf = (condition, message) => {
    if (condition) overages.push(message);
  };
  const distChunks = files.filter((f) => /\.(?:js|css)$/.test(f)).map((f) => `assets/${f}`);
  overages.push(...staleManifestProblems(manifest, distChunks));

  const sizes = new Map();
  const sizeOf = (file) => {
    if (!sizes.has(file)) sizes.set(file, { file, ...measureBuffer(readFileSync(path.join(dist, file))) });
    return sizes.get(file);
  };
  const present = new Set(distChunks);
  const measurable = (list) => list.filter((file) => present.has(file));

  const initial = initialClosure(manifest);
  const initialJs = sumSizes(measurable(initial.js), sizeOf);
  const initialCss = sumSizes(measurable(initial.css), sizeOf);
  const js = distChunks.filter((f) => f.endsWith('.js'));
  const totalJs = sumSizes(js, sizeOf);
  const lazy = largestPerDimension(js.filter((f) => !initial.js.includes(f)).map(sizeOf));
  const closures = routeClosures(manifest, initial);
  const routes = Object.entries(closures).map(([key, closure]) => ({
    key,
    ...sumSizes(measurable([...closure.js, ...closure.css]), sizeOf),
  }));
  const fonts = files.filter((f) => /\.(woff2?|ttf|otf)$/.test(f));
  const fontBytes = fonts.reduce((sum, f) => sum + readFileSync(path.join(assets, f)).length, 0);

  const gate = (label, actual, budget) => failIf(actual > budget, `${label} is ${bytes(actual)} > ${bytes(budget)}`);
  gate('initial JS br', initialJs.brBytes, budgets.initialJsBrBytes);
  gate('initial JS', initialJs.bytes, budgets.initialJsBytes);
  gate('initial JS gzip', initialJs.gzipBytes, budgets.initialJsGzipBytes);
  gate('initial CSS br', initialCss.brBytes, budgets.initialCssBrBytes);
  gate('initial CSS', initialCss.bytes, budgets.initialCssBytes);
  gate('initial CSS gzip', initialCss.gzipBytes, budgets.initialCssGzipBytes);
  gate('total JS br', totalJs.brBytes, budgets.totalJsBrBytes);
  gate('total JS', totalJs.bytes, budgets.totalJsBytes);
  gate('total JS gzip', totalJs.gzipBytes, budgets.totalJsGzipBytes);
  gate(`largest lazy JS br (${lazy.brBytes.file})`, lazy.brBytes.value, budgets.maxLazyJsBrBytes);
  gate(`largest lazy JS (${lazy.bytes.file})`, lazy.bytes.value, budgets.maxLazyJsBytes);
  gate(`largest lazy JS gzip (${lazy.gzipBytes.file})`, lazy.gzipBytes.value, budgets.maxLazyJsGzipBytes);
  overages.push(...routeBudgetProblems(routes.map((r) => r.key), budgets.routes));
  for (const route of routes) {
    if (route.key in budgets.routes) gate(`route ${route.key} closure br`, route.brBytes, budgets.routes[route.key]);
  }
  overages.push(...vendorChunkProblems(manifest, initial, chunkModules));
  failIf(initialJs.files.length === 0, 'the build manifest names no initial JS');
  failIf(initialCss.files.length === 0, 'the build manifest names no initial CSS');
  const fontProblem = fontCountProblem(fonts.length, budgets.fontAssetCount);
  failIf(fontProblem !== null, fontProblem);
  gate('font assets total', fontBytes, budgets.fontBytes);

  const triple = (s) => `br ${bytes(s.brBytes)} / raw ${bytes(s.bytes)} / gzip ${bytes(s.gzipBytes)}`;
  console.log('Frontend budget report (manifest closure; brotli q11 primary)');
  console.log(`  initial JS: ${triple(initialJs)} (${initialJs.files.length} chunks)`);
  for (const file of initialJs.files) console.log(`    ${file}: ${triple(sizeOf(file))}`);
  console.log(`  initial CSS: ${triple(initialCss)} [${initialCss.files.join(' + ')}]`);
  console.log(`  total JS: ${triple(totalJs)} across ${js.length} chunks`);
  const lazyFiles = new Set([lazy.brBytes.file, lazy.bytes.file, lazy.gzipBytes.file]);
  const lazyAt = (dimension) => (lazyFiles.size === 1 ? '' : ` (${lazy[dimension].file})`);
  console.log(
    `  largest lazy JS${lazyFiles.size === 1 ? ` ${lazy.brBytes.file}` : ''}: ` +
      `br ${bytes(lazy.brBytes.value)}${lazyAt('brBytes')} / raw ${bytes(lazy.bytes.value)}${lazyAt('bytes')} / ` +
      `gzip ${bytes(lazy.gzipBytes.value)}${lazyAt('gzipBytes')}`,
  );
  console.log('  route closures (beyond the initial closure):');
  for (const route of routes) {
    console.log(`    ${route.key}: ${triple(route)} (${route.files.length} files)`);
  }
  const vendorModules = Object.values(manifest)
    .filter((chunk) => (chunk.name ?? '').startsWith('vendor-'))
    .map((chunk) => `${chunk.name} ${(chunkModules.chunks[chunk.file] ?? []).length}`);
  console.log(
    `  vendor chunk modules: ${vendorModules.join(', ')} (lazy-only list: ${LAZY_ONLY_VENDOR_MODULES.length} entries)`,
  );
  console.log(`  fonts: ${fonts.length} files, ${bytes(fontBytes)}`);

  const report = {
    actuals: {
      initialJsBr: initialJs.brBytes,
      initialCssBr: initialCss.brBytes,
      totalJsBr: totalJs.brBytes,
      routes: Object.fromEntries(routes.map((route) => [route.key, route.brBytes])),
    },
    chunks: chunkReport(
      distChunks.map(sizeOf),
      [...initial.js, ...initial.css],
      Object.fromEntries(Object.entries(closures).map(([key, closure]) => [key, [...closure.js, ...closure.css]])),
    ),
  };
  overages.push(...evaluateTargets(report.actuals, targets, new Date().toISOString().slice(0, 10)));
  if (options.json) writeFileSync(options.json, `${JSON.stringify(report, null, 2)}\n`);
  if (process.env.GITHUB_STEP_SUMMARY) {
    const gates = {
      initialJsBr: budgets.initialJsBrBytes,
      initialCssBr: budgets.initialCssBrBytes,
      totalJsBr: budgets.totalJsBrBytes,
      ...Object.fromEntries(Object.entries(budgets.routes).map(([key, value]) => [`routes.${key}`, value])),
    };
    const base = options.base ? JSON.parse(readFileSync(options.base, 'utf8')) : null;
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, summaryTable(report, gates, targets, base));
  }

  if (overages.length > 0) {
    console.error(`\nFrontend budget check ${options.reportOnly ? 'found (report-only)' : 'failed'}:`);
    for (const overage of overages) console.error(`  - ${overage}`);
    if (!options.reportOnly) process.exit(1);
    return;
  }
  console.log('Frontend budget check passed.');
}

// Main-module guard, realpath-safe (audit bundle-08 item 2): Node runs a
// symlinked script under its real path, so comparing path.resolve(argv[1])
// silently skipped the gate (exit 0) when invoked through a symlink.
if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  main();
}
