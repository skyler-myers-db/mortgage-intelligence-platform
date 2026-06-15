#!/usr/bin/env node
import { gzipSync } from 'node:zlib';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const repoRoot = path.resolve(import.meta.dirname, '..');
const distDir = path.join(repoRoot, 'frontend', 'dist');
const assetsDir = path.join(distDir, 'assets');

const KiB = 1024;
// ---------------------------------------------------------------------------
// Budget headroom policy (2026-06-10 re-baseline)
//
// Every gate is the CURRENT MEASURED ACTUAL plus ~5% headroom, rounded up to
// a whole KiB. ~5% absorbs hash-length jitter, lazy-boundary overhead, and
// the ~0.5 KiB Linux-vs-macOS zlib variance on gzip dimensions, while still
// failing CI on any real regression.
//
// Rules for changing a number here:
//   1. A bump must name the feature that needs it and cite the new measured
//      actuals from `npm run budget` — never bump just to turn CI green.
//      (The prior comment-per-bump style ratcheted totalJsBytes to 0.03 KiB
//      of headroom, which then tripped on a 0.4 KiB accessibility fix.)
//   2. When a slice SHRINKS the bundle, ratchet the gate DOWN to the new
//      actual + ~5% in the same commit so the win is locked in.
//   3. fontAssetCount stays exact — a 15th font file is always a mistake
//      (the Geist/Geist Mono subset list is fixed).
//
// Actuals at re-baseline (2026-06-10, post analytics decomposition + a11y
// polish + precompression slice): initial JS 256.60 / gzip 79.03; initial
// CSS 101.22 / gzip 18.13; total JS 832.42 / gzip 274.22 across 36 chunks;
// largest lazy chunk (shared mortgage components + drawerSources) 98.40 /
// gzip 32.06; fonts 14 files / 215.42 KiB. Build-time precompressed .br/.gz
// siblings are excluded by the .endsWith() filters below — they are strictly
// smaller duplicates served via content negotiation (see
// tools/precompress_assets.mjs + backend/services/static_assets.py).
// ---------------------------------------------------------------------------
const budgets = {
  // Bumped 2026-06-15 for the frontend security patch:
  // @babel/core 7.29.7, vite 8.0.16, and happy-dom 20.10.3 clear the npm audit
  // gate. Vite 8.0.16 folds the prior shared initial runtime/icon chunk into
  // index instead of emitting it separately, so index raw/gzip increases while
  // total JS remains below the existing total budget. Re-baseline index with
  // ~5% headroom on the measured post-patch actual.
  initialJsBytes: 393 * KiB, // actual 373.76
  initialJsGzipBytes: 122 * KiB, // actual 115.43
  // Bumped 2026-06-11 for the re-audit #4 Buyer-Wow tranche: ⌘K command
  // palette (.cmdk*), portal evidence hover-card (.evidence-hovercard*),
  // sleek one-time KPI entrance (.kpi__value--enter / .spark__line--draw),
  // and the campaign ROI projector (.roi-projector*) — ~7 KiB of feature CSS.
  // Bumped 2026-06-12 for the final Buyer-Wow epic (funnel Sankey,
  // morning briefing .briefing*, + the in-flight map/narrative/follow-up
  // tranches). Sankey + briefing measured at CSS ~110.5 / gzip ~19.75;
  // headroom raised so the remaining same-epic CSS features don't trip the
  // gate mid-stream. Ratchet back down after the epic settles.
  // Bumped 2026-06-13 for the auto-offer epic: portfolio summary + outreach
  // routing + search ⌘K chip + the borrower-offer prototype mock grew initial
  // CSS to 118.04 / gzip 20.56; slices 2-3 (indicative offer, personalized
  // copy) add more. Headroom raised so same-epic CSS doesn't trip mid-stream;
  // ratchet back down after the epic settles.
  initialCssBytes: 124 * KiB, // actual 118.04
  initialCssGzipBytes: 22 * KiB, // actual 20.56
  // Re-baselined 2026-06-12: the Buyer-Wow epic + "Your book today" summary
  // (Feature A) + LO-assignment/follow-up routing (Feature C) grew total JS
  // from the stale 832.42 baseline to 875.99 / gzip 288.42, tipping the old
  // 875/288 gate. Reset to the measured actual + ~5% per this file's policy.
  totalJsBytes: 920 * KiB, // actual 875.99
  totalJsGzipBytes: 303 * KiB, // actual 288.42
  maxLazyJsBytes: 104 * KiB, // actual 98.40 (was 160 -- tightened)
  maxLazyJsGzipBytes: 34 * KiB, // actual 32.06 (was 60 -- tightened)
  fontAssetCount: 14, // exact by policy
  fontBytes: 227 * KiB, // actual 215.42 (was 230 -- tightened)
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
