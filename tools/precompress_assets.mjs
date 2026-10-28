#!/usr/bin/env node
// Build-time precompression for hashed Vite assets (2026-06-10 perf slice).
//
// Why: the app serves /assets/* with `Cache-Control: immutable` for a year.
// Compressing those bytes per-request in GZipMiddleware burns container CPU
// on every cold cache and caps us at gzip ratios. Emitting .br + .gz
// siblings at build time lets the backend serve the smallest negotiated
// variant from disk (see backend/services/static_assets.py) at brotli
// quality 11 / gzip level 9 — levels far too slow for request-path
// compression but free at build time.
//
// Scope: frontend/dist/assets only (hashed, immutable). index.html is
// intentionally excluded (no-store; tiny). Fonts (woff2) are already
// entropy-coded and skipped. Node built-ins only — no new dependencies.
//
// Wired into `npm --prefix frontend run build`, which is what CI and
// scripts/deploy.sh both invoke, so the bundle sync uploads the variants
// with zero extra deploy steps (databricks.yml syncs frontend/dist/**).
//
// Concurrent since 2026-10-27: the committed ZCTA geometry (51 per-state
// TopoJSON assets, 15.5 MiB) made this step a minute of single-threaded
// brotli 11 on every CI build and deploy. The async zlib calls run on
// libuv's thread pool (UV_THREADPOOL_SIZE, 4 by default), so the files
// compress in parallel; each file's bytes are exactly what the sync calls
// wrote, since brotli and gzip are deterministic for the same parameters.
import { promisify } from 'node:util';
import { brotliCompress, constants as zlibConstants, gzip } from 'node:zlib';
import { readdirSync, statSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { availableParallelism } from 'node:os';
import path from 'node:path';
import process from 'node:process';

const brotliAsync = promisify(brotliCompress);
const gzipAsync = promisify(gzip);

const repoRoot = path.resolve(import.meta.dirname, '..');
const assetsDir = path.join(repoRoot, 'frontend', 'dist', 'assets');

// Text-like assets that compress meaningfully. Keep this list conservative:
// a suffix here means "serve the .br/.gz sibling when the client accepts it".
// No `map`: source maps are never served (tools/postbuild_artifacts.mjs
// keeps build metadata out of dist before this step runs).
const COMPRESSIBLE = /\.(js|css|svg|json|txt)$/;
const MIN_BYTES = 1024; // mirrors GZipMiddleware minimum_size

async function compressOne(absPath) {
  const raw = await readFile(absPath);
  const [br, gz] = await Promise.all([
    brotliAsync(raw, {
      params: {
        [zlibConstants.BROTLI_PARAM_QUALITY]: 11,
        [zlibConstants.BROTLI_PARAM_SIZE_HINT]: raw.length,
      },
    }),
    gzipAsync(raw, { level: 9 }),
  ]);
  // Only keep variants that actually save bytes; serving a larger "compressed"
  // file would be strictly worse than identity.
  let wrote = { br: 0, gz: 0 };
  if (br.length < raw.length) {
    await writeFile(`${absPath}.br`, br);
    wrote.br = br.length;
  }
  if (gz.length < raw.length) {
    await writeFile(`${absPath}.gz`, gz);
    wrote.gz = gz.length;
  }
  return { raw: raw.length, ...wrote };
}

/** Runs `task` over `items` with at most `limit` in flight; results keep item order. */
async function mapBounded(items, limit, task) {
  const results = new Array(items.length);
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await task(items[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return results;
}

async function main() {
  let files;
  try {
    files = readdirSync(assetsDir);
  } catch (err) {
    console.error(`precompress: no built assets at ${assetsDir}; run vite build first.`);
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }

  const targets = files
    .filter((file) => COMPRESSIBLE.test(file))
    .map((file) => path.join(assetsDir, file))
    .filter((abs) => statSync(abs).size >= MIN_BYTES);
  // Enough in flight to keep the thread pool busy; the pool caps the cores used.
  const results = await mapBounded(targets, availableParallelism(), compressOne);

  let count = 0;
  let rawTotal = 0;
  let brTotal = 0;
  let gzTotal = 0;
  for (const { raw, br, gz } of results) {
    count += 1;
    rawTotal += raw;
    brTotal += br || raw;
    gzTotal += gz || raw;
  }

  const KiB = 1024;
  const fmt = (n) => `${(n / KiB).toFixed(2)} KiB`;
  console.log(
    `precompress: ${count} assets — raw ${fmt(rawTotal)}, br ${fmt(brTotal)} ` +
      `(-${rawTotal ? Math.round((1 - brTotal / rawTotal) * 100) : 0}%), ` +
      `gz ${fmt(gzTotal)} (-${rawTotal ? Math.round((1 - gzTotal / rawTotal) * 100) : 0}%)`,
  );
}

await main();
