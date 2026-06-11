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
import { brotliCompressSync, constants as zlibConstants, gzipSync } from 'node:zlib';
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const repoRoot = path.resolve(import.meta.dirname, '..');
const assetsDir = path.join(repoRoot, 'frontend', 'dist', 'assets');

// Text-like assets that compress meaningfully. Keep this list conservative:
// a suffix here means "serve the .br/.gz sibling when the client accepts it".
const COMPRESSIBLE = /\.(js|css|svg|json|map|txt)$/;
const MIN_BYTES = 1024; // mirrors GZipMiddleware minimum_size

function compressOne(absPath) {
  const raw = readFileSync(absPath);
  const br = brotliCompressSync(raw, {
    params: {
      [zlibConstants.BROTLI_PARAM_QUALITY]: 11,
      [zlibConstants.BROTLI_PARAM_SIZE_HINT]: raw.length,
    },
  });
  const gz = gzipSync(raw, { level: 9 });
  // Only keep variants that actually save bytes; serving a larger "compressed"
  // file would be strictly worse than identity.
  let wrote = { br: 0, gz: 0 };
  if (br.length < raw.length) {
    writeFileSync(`${absPath}.br`, br);
    wrote.br = br.length;
  }
  if (gz.length < raw.length) {
    writeFileSync(`${absPath}.gz`, gz);
    wrote.gz = gz.length;
  }
  return { raw: raw.length, ...wrote };
}

function main() {
  let files;
  try {
    files = readdirSync(assetsDir);
  } catch (err) {
    console.error(`precompress: no built assets at ${assetsDir}; run vite build first.`);
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }

  let count = 0;
  let rawTotal = 0;
  let brTotal = 0;
  let gzTotal = 0;
  for (const file of files) {
    if (!COMPRESSIBLE.test(file)) continue;
    const abs = path.join(assetsDir, file);
    if (statSync(abs).size < MIN_BYTES) continue;
    const { raw, br, gz } = compressOne(abs);
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

main();
