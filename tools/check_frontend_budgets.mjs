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
  // Re-baselined 2026-07-13 for the intelligence + trust UX slice. The shell
  // now carries the reviewed 48-asset evidence-destination registry so every
  // evidence chip and lineage node can resolve immediately and consistently,
  // plus native Genie feedback and the shared offer/campaign evidence controls.
  // Measured: initial JS 396.40 / gzip 119.85; restore ~5% headroom per policy.
  initialJsBytes: 417 * KiB, // actual 396.40
  initialJsGzipBytes: 126 * KiB, // actual 119.85
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
  // Bumped 2026-06-26 for the Mortgage Growth Agent route surface: governed
  // workflow cards, broad-vs-actionable reconciliation, tool timeline,
  // policy checks, and saved-monitor affordances. Measured post-feature
  // actual: CSS 125.45 / gzip 21.63; reset raw with ~5% headroom.
  // Re-baselined 2026-07-10 (UX declutter batches 1-2: sales-ops -> analytics
  // tab, capabilities -> admin console, glossary product-principles section,
  // plus the UC-identifier -> plain-label pass with a shared assetLabels helper
  // and the Today's-top-leads quick-pick). The initial-CSS gates had drifted
  // thin on main; restore ~5% headroom per this file's policy. Measured: CSS
  // 134.24 / gzip 22.97.
  initialCssBytes: 141 * KiB, // actual 134.24
  initialCssGzipBytes: 25 * KiB, // actual 22.97
  // Re-baselined 2026-07-10 for the UX declutter slice (batches 1-2). total JS
  // was red on main (990.51 > 990.00); restored ~5% headroom over the measured
  // actual per this file's policy. Batch 2 (asset-label helper, top-leads
  // quick-pick) nudged totals within that headroom. Measured: total JS 992.92 /
  // gzip 323.76 (38 chunks).
  // Re-baselined 2026-07-13 for the intelligence + trust UX slice: governed
  // asset links and lineage parity, the admin audit explorer, data-backed
  // campaign economics and Supervisor recommendations, offer-copy evidence,
  // and Genie reasoning/feedback affordances. Measured after the file-size
  // refactor: total JS 1050.03 / gzip 340.53 across 41 chunks. Restore the
  // documented ~5% headroom for
  // both aggregate dimensions; the initial, lazy-chunk, CSS, and font gates
  // remain unchanged.
  // Re-baselined 2026-07-15 for the signed Growth Agent cohort handoff and
  // campaign-variant approval provenance. The UI now carries and verifies the
  // opaque handoff proof, exposes stale-proof recovery, and preserves the
  // selected governed campaign variant through draft/approval/rejection.
  // Measured after the cohort-proof and public Agent Responses hardening:
  // total JS 1113.49 KiB / gzip 358.35 KiB across 41 chunks. Keep the same
  // documented ~5% headroom on both raw and compressed totals; this also
  // absorbs the zlib platform variance called out above without weakening the
  // initial-bundle or largest-lazy-chunk gates.
  totalJsBytes: 1170 * KiB, // actual 1113.49
  totalJsGzipBytes: 377 * KiB, // actual 358.35
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
