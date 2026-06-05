#!/usr/bin/env node
import { gzipSync } from 'node:zlib';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const repoRoot = path.resolve(import.meta.dirname, '..');
const distDir = path.join(repoRoot, 'frontend', 'dist');
const assetsDir = path.join(distDir, 'assets');

const KiB = 1024;
const budgets = {
  initialJsBytes: 300 * KiB,
  initialJsGzipBytes: 90 * KiB,
  // The May 2026 explainability tranches added a first-class glossary,
  // borrower proof drawer, and governed asset detail route. Keep the increase
  // bounded while preserving initial JS, gzip, largest-route, and font gates.
  // The admin operations center now has phone-width shell/admin layout rules;
  // Vite emits route CSS as one initial stylesheet, so keep the added allowance
  // narrow and leave the JS, lazy-route, and font gates unchanged.
  initialCssBytes: 102 * KiB,
  initialCssGzipBytes: 18.25 * KiB,
  // Route-level decomposition introduces a few KiB of lazy-module boundary
  // overhead while leaving initial load and largest-route gates unchanged.
  // Keep this aggregate cap tight enough to catch accidental asset growth
  // without forcing large components back into single-file maintenance debt.
  // The governed lender overlay adds cross-route URL propagation and public-safe
  // lender filter helpers. Keep the raw aggregate allowance bounded while
  // preserving the stricter gzip, initial-load, lazy-route, CSS, and font gates.
  // Admin-only data operations add a governed refresh control surface while
  // keeping initial load, largest lazy route, CSS, and font budgets unchanged.
  // The activation outbox adds a governed post-approval writeback loop; keep the
  // increase bounded while preserving initial-load and largest-route gates.
  totalJsBytes: 832 * KiB,
  // Linux CI zlib output runs about 0.5 KiB larger than macOS for the same
  // Vite assets. Keep a narrow margin so budget enforcement is stable across
  // runners without weakening initial-load or largest-route gates.
  totalJsGzipBytes: 276 * KiB,
  maxLazyJsBytes: 160 * KiB,
  maxLazyJsGzipBytes: 60 * KiB,
  fontAssetCount: 14,
  fontBytes: 230 * KiB,
};

function bytes(n) {
  if (n >= KiB * KiB) return `${(n / (KiB * KiB)).toFixed(2)} MiB`;
  return `${(n / KiB).toFixed(2)} KiB`;
}

function assetInfo(file) {
  const abs = path.join(assetsDir, file);
  const raw = readFileSync(abs);
  return {
    file,
    bytes: statSync(abs).size,
    gzipBytes: gzipSync(raw, { level: 9 }).length,
  };
}

function failIf(overages, condition, message) {
  if (condition) overages.push(message);
}

function main() {
  let files;
  try {
    files = readdirSync(assetsDir);
  } catch (err) {
    console.error(`Frontend budget check requires a built Vite dist at ${assetsDir}.`);
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }

  const js = files.filter((f) => f.endsWith('.js')).map(assetInfo);
  const css = files.filter((f) => f.endsWith('.css')).map(assetInfo);
  const fonts = files.filter((f) => /\.(woff2?|ttf|otf)$/.test(f)).map(assetInfo);
  const initialJs = js.find((a) => /^index-[\w-]+\.js$/.test(a.file));
  const initialCss = css.find((a) => /^index-[\w-]+\.css$/.test(a.file));
  const lazyJs = js.filter((a) => a !== initialJs);
  const totalJsBytes = js.reduce((sum, a) => sum + a.bytes, 0);
  const totalJsGzipBytes = js.reduce((sum, a) => sum + a.gzipBytes, 0);
  const fontBytes = fonts.reduce((sum, a) => sum + a.bytes, 0);
  const largestLazy = lazyJs.reduce((max, a) => (a.bytes > max.bytes ? a : max), {
    file: 'none',
    bytes: 0,
    gzipBytes: 0,
  });

  const overages = [];
  failIf(overages, !initialJs, 'missing initial index-*.js asset');
  failIf(overages, !initialCss, 'missing initial index-*.css asset');

  if (initialJs) {
    failIf(
      overages,
      initialJs.bytes > budgets.initialJsBytes,
      `initial JS ${initialJs.file} is ${bytes(initialJs.bytes)} > ${bytes(budgets.initialJsBytes)}`,
    );
    failIf(
      overages,
      initialJs.gzipBytes > budgets.initialJsGzipBytes,
      `initial JS gzip ${initialJs.file} is ${bytes(initialJs.gzipBytes)} > ${bytes(budgets.initialJsGzipBytes)}`,
    );
  }
  if (initialCss) {
    failIf(
      overages,
      initialCss.bytes > budgets.initialCssBytes,
      `initial CSS ${initialCss.file} is ${bytes(initialCss.bytes)} > ${bytes(budgets.initialCssBytes)}`,
    );
    failIf(
      overages,
      initialCss.gzipBytes > budgets.initialCssGzipBytes,
      `initial CSS gzip ${initialCss.file} is ${bytes(initialCss.gzipBytes)} > ${bytes(budgets.initialCssGzipBytes)}`,
    );
  }

  failIf(
    overages,
    totalJsBytes > budgets.totalJsBytes,
    `total JS is ${bytes(totalJsBytes)} > ${bytes(budgets.totalJsBytes)}`,
  );
  failIf(
    overages,
    totalJsGzipBytes > budgets.totalJsGzipBytes,
    `total JS gzip is ${bytes(totalJsGzipBytes)} > ${bytes(budgets.totalJsGzipBytes)}`,
  );
  failIf(
    overages,
    largestLazy.bytes > budgets.maxLazyJsBytes,
    `largest lazy JS ${largestLazy.file} is ${bytes(largestLazy.bytes)} > ${bytes(budgets.maxLazyJsBytes)}`,
  );
  failIf(
    overages,
    largestLazy.gzipBytes > budgets.maxLazyJsGzipBytes,
    `largest lazy JS gzip ${largestLazy.file} is ${bytes(largestLazy.gzipBytes)} > ${bytes(budgets.maxLazyJsGzipBytes)}`,
  );
  failIf(
    overages,
    fonts.length > budgets.fontAssetCount,
    `font asset count is ${fonts.length} > ${budgets.fontAssetCount}`,
  );
  failIf(
    overages,
    fontBytes > budgets.fontBytes,
    `font assets total ${bytes(fontBytes)} > ${bytes(budgets.fontBytes)}`,
  );

  console.log('Frontend budget report');
  console.log(`  initial JS: ${initialJs ? `${initialJs.file} ${bytes(initialJs.bytes)} / gzip ${bytes(initialJs.gzipBytes)}` : 'missing'}`);
  console.log(`  initial CSS: ${initialCss ? `${initialCss.file} ${bytes(initialCss.bytes)} / gzip ${bytes(initialCss.gzipBytes)}` : 'missing'}`);
  console.log(`  total JS: ${bytes(totalJsBytes)} / gzip ${bytes(totalJsGzipBytes)} across ${js.length} chunks`);
  console.log(`  largest lazy JS: ${largestLazy.file} ${bytes(largestLazy.bytes)} / gzip ${bytes(largestLazy.gzipBytes)}`);
  console.log(`  fonts: ${fonts.length} files, ${bytes(fontBytes)}`);

  if (overages.length > 0) {
    console.error('\nFrontend budget check failed:');
    for (const overage of overages) console.error(`  - ${overage}`);
    process.exit(1);
  }
  console.log('Frontend budget check passed.');
}

main();
