// ---------------------------------------------------------------------------
// Pure helpers over Vite's build manifest (audit bundle-08, 2026-09-24).
//
// `vite build` writes `build-manifest.json` (frontend/vite.config.ts
// `build.manifest`); tools/postbuild_artifacts.mjs moves it from dist into
// frontend/build-meta/ before anything is served. The manifest is the
// bundler's own chunk graph: every key is a chunk (the `index.html` entry, a
// `src/...` module that is a dynamic-import target, or an `_name-hash.js`
// shared chunk) with its emitted `file`, its static `imports`, its
// `dynamicImports` and the `css` it needs. Measuring through it replaces the
// regex walk over emitted JS the budget used in wave 1c, and it sees the CSS
// a chunk pulls in, which a JS scan cannot.
//
// Node built-ins only; no I/O here, so tests pass synthetic manifests.
// ---------------------------------------------------------------------------

/** Where a route module lives, as a manifest key. */
export const ROUTE_SOURCE_PREFIX = 'src/routes/';

const CHUNK_FILE = /\.(?:js|css)$/;

/** The app entry's manifest key. */
export const APP_ENTRY_KEY = 'index.html';

/**
 * The boot module (src/boot/primeBoot.ts, audit bundle-02): the second
 * manifest entry. The built HTML loads it beside the app entry, so it is part
 * of every first paint; it must stay a leaf (see bootEntryFile).
 */
export const BOOT_ENTRY_KEY = 'src/boot/primeBoot.ts';

/**
 * The `index.html` entry key. Throws unless the manifest holds exactly two
 * `isEntry` keys, `index.html` and the boot module.
 */
export function manifestEntryKey(manifest) {
  const entries = Object.keys(manifest).filter((key) => manifest[key].isEntry === true).sort();
  if (entries.length !== 2 || !entries.includes(APP_ENTRY_KEY) || !entries.includes(BOOT_ENTRY_KEY)) {
    throw new Error(
      `build manifest must have exactly two entries (${APP_ENTRY_KEY} and ${BOOT_ENTRY_KEY}), found ${entries.length}: ${entries.join(', ')}`,
    );
  }
  return APP_ENTRY_KEY;
}

/**
 * The boot module's emitted file. Throws when the boot entry imports anything
 * (statically or dynamically), carries CSS, or is imported by any chunk: it
 * must start its reads without waiting on another request, and no app chunk
 * may depend on it.
 */
export function bootEntryFile(manifest) {
  const boot = manifest[BOOT_ENTRY_KEY];
  if (!boot || boot.isEntry !== true) throw new Error(`build manifest has no ${BOOT_ENTRY_KEY} entry`);
  for (const field of ['imports', 'dynamicImports', 'css']) {
    if ((boot[field] ?? []).length > 0) {
      throw new Error(`the boot module ${BOOT_ENTRY_KEY} must have no ${field}, found: ${boot[field].join(', ')}`);
    }
  }
  const importers = Object.keys(manifest).filter((key) =>
    [...(manifest[key].imports ?? []), ...(manifest[key].dynamicImports ?? [])].includes(BOOT_ENTRY_KEY),
  );
  if (importers.length > 0) throw new Error(`no chunk may import the boot module ${BOOT_ENTRY_KEY}; imported by: ${importers.join(', ')}`);
  return boot.file;
}

/**
 * Every key reachable from `startKeys` through static `imports`, the starts
 * included. `dynamicImports` are never followed: a lazy chunk is not loaded
 * until something asks for it.
 */
export function staticImportClosure(manifest, startKeys) {
  const seen = new Set();
  const queue = [...startKeys];
  while (queue.length > 0) {
    const key = queue.shift();
    if (seen.has(key)) continue;
    const chunk = manifest[key];
    if (!chunk) throw new Error(`build manifest has no chunk "${key}" (imported by another chunk)`);
    seen.add(key);
    for (const imported of chunk.imports ?? []) queue.push(imported);
  }
  return seen;
}

function jsFiles(manifest, keys) {
  return [...keys].map((key) => manifest[key].file).filter((file) => file.endsWith('.js'));
}

function cssFiles(manifest, keys) {
  const files = new Set();
  for (const key of keys) for (const file of manifest[key].css ?? []) files.add(file);
  return [...files];
}

/**
 * What every first paint loads before the app runs: the entry chunk plus its
 * transitive static imports (never its dynamic imports), the boot module the
 * HTML loads beside it, and the union of the CSS those chunks need. Files are
 * dist-relative (`assets/...`).
 */
export function initialClosure(manifest) {
  const entry = manifestEntryKey(manifest);
  const boot = bootEntryFile(manifest);
  const keys = staticImportClosure(manifest, [entry]);
  return {
    entry,
    keys: [...keys, BOOT_ENTRY_KEY],
    js: [...jsFiles(manifest, keys), boot],
    css: cssFiles(manifest, keys),
  };
}

/**
 * Per route module (a dynamic entry whose source lives under src/routes/):
 * the route chunk plus its transitive static imports, minus what the initial
 * closure already loaded, plus the CSS they add. This is what a navigation to
 * that route fetches on a warm shell. Keyed by manifest source path.
 */
export function routeClosures(manifest, initial = initialClosure(manifest)) {
  const initialKeys = new Set(initial.keys);
  const initialCss = new Set(initial.css);
  const routes = {};
  for (const [key, chunk] of Object.entries(manifest)) {
    if (!chunk.isDynamicEntry || !key.startsWith(ROUTE_SOURCE_PREFIX)) continue;
    const keys = [...staticImportClosure(manifest, [key])].filter((k) => !initialKeys.has(k));
    routes[key] = {
      js: jsFiles(manifest, keys),
      css: cssFiles(manifest, keys).filter((file) => !initialCss.has(file)),
    };
  }
  return routes;
}

/** Every .js/.css file the manifest names, as a chunk `file` or in a `css` list. */
export function manifestChunkFiles(manifest) {
  const files = new Set();
  for (const chunk of Object.values(manifest)) {
    if (CHUNK_FILE.test(chunk.file)) files.add(chunk.file);
    for (const file of chunk.css ?? []) files.add(file);
  }
  return files;
}

/**
 * A manifest is stale when it does not describe the dist it sits beside: a
 * file it names is missing from dist, or a dist chunk is missing from it
 * (a build that ran without the manifest step, or a hand-copied asset).
 * Only .js and .css are compared, so the .br/.gz siblings that
 * precompress_assets.mjs writes after postbuild never count.
 * `distChunkFiles` are dist-relative paths (`assets/x.js`).
 */
export function staleManifestProblems(manifest, distChunkFiles) {
  const named = manifestChunkFiles(manifest);
  const present = new Set([...distChunkFiles].filter((file) => CHUNK_FILE.test(file)));
  const problems = [];
  for (const file of named) {
    if (!present.has(file)) problems.push(`stale build manifest: it names ${file}, which is not in dist`);
  }
  for (const file of present) {
    if (!named.has(file)) problems.push(`stale build manifest: dist has ${file}, which it does not name`);
  }
  return problems.sort();
}
