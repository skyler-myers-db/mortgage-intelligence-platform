#!/usr/bin/env node
// ---------------------------------------------------------------------------
// Post-build artifact relocation (audit bundle-08, 2026-09-24).
//
// `vite build` writes build metadata into frontend/dist that must never be
// served or deployed:
//
//   - build-manifest.json (vite.config.ts `build.manifest`), the chunk graph
//     tools/check_frontend_budgets.mjs measures. backend/main.py's
//     `_spa_fallback` serves ANY real file under dist (its `candidate.is_file()`
//     branch), so a manifest left in dist would be public at
//     /build-manifest.json, and databricks.yml uploads all of frontend/dist/**.
//
// This step empties and recreates frontend/build-meta/ and moves the manifest
// there, then fails the build if a leftover remains in dist (the manifest, or
// a `.vite/` metadata dir from a default-named manifest). build-meta/ is
// git-ignored, so the bundle sync (which uploads git-tracked files plus
// frontend/dist/**) and scripts/package_source.sh (git archive) never ship it.
//
// Wired into `npm --prefix frontend run build` between `vite build` and
// tools/precompress_assets.mjs. Node built-ins only.
// ---------------------------------------------------------------------------
import { existsSync, mkdirSync, readdirSync, renameSync, rmSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

export { initialClosure } from './build_manifest.mjs';

export const MANIFEST_FILE = 'build-manifest.json';

const repoRoot = path.resolve(fileURLToPath(new URL('../', import.meta.url)));

export const DEFAULT_DIRS = {
  distDir: path.join(repoRoot, 'frontend', 'dist'),
  metaDir: path.join(repoRoot, 'frontend', 'build-meta'),
};

function resetDir(dir) {
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
}

/**
 * Build metadata still inside dist after relocation: each entry is one reason
 * the dist must not be deployed. Pure over the directory listing.
 */
export function distLeftovers(distDir) {
  const problems = [];
  const top = readdirSync(distDir, { withFileTypes: true });
  for (const entry of top) {
    if (entry.name === MANIFEST_FILE) problems.push(`dist still contains ${MANIFEST_FILE}`);
    if (entry.name === '.vite') problems.push('dist still contains a .vite/ metadata directory');
  }
  return problems;
}

/**
 * Move the build manifest out of `distDir` into a freshly emptied `metaDir`,
 * then verify dist is clean. Throws with every problem found; returns the
 * relocated paths.
 */
export function relocateBuildArtifacts({ distDir, metaDir }) {
  const manifestSource = path.join(distDir, MANIFEST_FILE);
  if (!existsSync(manifestSource)) {
    throw new Error(
      `postbuild: ${manifestSource} is missing; vite.config.ts must set build.manifest to '${MANIFEST_FILE}'.`,
    );
  }
  resetDir(metaDir);
  const manifest = path.join(metaDir, MANIFEST_FILE);
  renameSync(manifestSource, manifest);

  const problems = distLeftovers(distDir);
  if (problems.length > 0) {
    throw new Error(`postbuild: dist is not deployable:\n  - ${problems.join('\n  - ')}`);
  }
  return { manifest };
}

function main() {
  try {
    const result = relocateBuildArtifacts(DEFAULT_DIRS);
    console.log(`postbuild: manifest -> ${path.relative(repoRoot, result.manifest)}`);
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.join(repoRoot, 'tools', 'postbuild_artifacts.mjs')) {
  main();
}
