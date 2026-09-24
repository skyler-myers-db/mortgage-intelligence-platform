#!/usr/bin/env node
// ---------------------------------------------------------------------------
// Post-build artifact relocation (audit bundle-08 + stack-01, 2026-09-24).
//
// `vite build` writes build metadata into frontend/dist that must never be
// served or deployed. backend/main.py's `_spa_fallback` serves ANY real file
// under dist (its `candidate.is_file()` branch), the `/assets` route serves
// any file under dist/assets, and databricks.yml uploads all of
// frontend/dist/**. So:
//
//   - build-manifest.json (vite.config.ts `build.manifest`), the chunk graph
//     tools/check_frontend_budgets.mjs measures, and build-modules.json (the
//     vite.config.ts chunk-modules plugin: which modules each chunk holds),
//     move to frontend/build-meta/.
//   - every *.map (vite.config.ts `build.sourcemap: 'hidden'`) moves to
//     frontend/sourcemaps/, keeping its dist-relative path. That directory is
//     the CI artifact for decoding production stack traces; it is never
//     served.
//
// Each destination is emptied and recreated first, so it only ever holds
// this build's files. Then the build FAILS if dist still contains a .map,
// the manifest, a `.vite/` metadata dir, or a JS file carrying a
// `//# sourceMappingURL=` comment (a non-hidden sourcemap setting would make
// every browser request the map). Both destinations are git-ignored, so the
// bundle sync (git-tracked files plus frontend/dist/**) and
// scripts/package_source.sh (git archive) never ship them.
//
// Wired into `npm --prefix frontend run build` between `vite build` and
// tools/precompress_assets.mjs. Node built-ins only.
// ---------------------------------------------------------------------------
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

export { initialClosure } from './build_manifest.mjs';

export const MANIFEST_FILE = 'build-manifest.json';
export const CHUNK_MODULES_FILE = 'build-modules.json';
/** Build metadata `vite build` must emit into dist and this step moves out. */
export const META_FILES = [MANIFEST_FILE, CHUNK_MODULES_FILE];

/** A source-map comment at the start of a line (`//# ...` or legacy `//@ ...`). */
export const SOURCE_MAP_COMMENT = /^\/\/[#@]\s*sourceMappingURL=/m;

const repoRoot = path.resolve(fileURLToPath(new URL('../', import.meta.url)));

export const DEFAULT_DIRS = {
  distDir: path.join(repoRoot, 'frontend', 'dist'),
  metaDir: path.join(repoRoot, 'frontend', 'build-meta'),
  mapsDir: path.join(repoRoot, 'frontend', 'sourcemaps'),
};

function resetDir(dir) {
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
}

/** Every file under `dir`, as a `/`-separated path relative to it. */
function walkFiles(dir, prefix = '') {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) files.push(...walkFiles(path.join(dir, entry.name), relative));
    else files.push(relative);
  }
  return files;
}

/**
 * Build metadata still inside dist: each entry is one reason the dist must
 * not be deployed.
 */
export function distLeftovers(distDir) {
  const problems = [];
  for (const entry of readdirSync(distDir, { withFileTypes: true })) {
    if (META_FILES.includes(entry.name)) problems.push(`dist still contains ${entry.name}`);
    if (entry.name === '.vite') problems.push('dist still contains a .vite/ metadata directory');
  }
  for (const file of walkFiles(distDir)) {
    if (file.endsWith('.map')) problems.push(`dist still contains a source map: ${file}`);
    if (file.endsWith('.js') && SOURCE_MAP_COMMENT.test(readFileSync(path.join(distDir, file), 'utf8'))) {
      problems.push(`dist JS carries a sourceMappingURL comment: ${file} (vite.config.ts build.sourcemap must be 'hidden')`);
    }
  }
  return problems;
}

/**
 * Move the build metadata files into a freshly emptied `metaDir` and every
 * source map into a freshly emptied `mapsDir` (same relative path), then
 * verify dist is clean. Throws with every problem found; returns what moved.
 */
export function relocateBuildArtifacts({ distDir, metaDir, mapsDir }) {
  const missing = META_FILES.filter((name) => !existsSync(path.join(distDir, name)));
  if (missing.length > 0) {
    throw new Error(
      `postbuild: ${missing.join(', ')} missing from ${distDir}; vite.config.ts must set ` +
        `build.manifest to '${MANIFEST_FILE}' and keep the chunk-modules plugin that writes '${CHUNK_MODULES_FILE}'.`,
    );
  }
  resetDir(metaDir);
  for (const name of META_FILES) renameSync(path.join(distDir, name), path.join(metaDir, name));
  const manifest = path.join(metaDir, MANIFEST_FILE);

  resetDir(mapsDir);
  const maps = walkFiles(distDir).filter((file) => file.endsWith('.map'));
  for (const file of maps) {
    const target = path.join(mapsDir, file);
    mkdirSync(path.dirname(target), { recursive: true });
    renameSync(path.join(distDir, file), target);
  }

  const problems = distLeftovers(distDir);
  if (problems.length > 0) {
    throw new Error(`postbuild: dist is not deployable:\n  - ${problems.join('\n  - ')}`);
  }
  return { manifest, chunkModules: path.join(metaDir, CHUNK_MODULES_FILE), maps };
}

function main() {
  try {
    const result = relocateBuildArtifacts(DEFAULT_DIRS);
    console.log(
      `postbuild: ${META_FILES.join(' + ')} -> ${path.relative(repoRoot, DEFAULT_DIRS.metaDir)}/; ` +
        `${result.maps.length} source maps -> ${path.relative(repoRoot, DEFAULT_DIRS.mapsDir)}/`,
    );
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.join(repoRoot, 'tools', 'postbuild_artifacts.mjs')) {
  main();
}
