#!/usr/bin/env node
import { brotliCompressSync, constants as zlibConstants, gzipSync } from 'node:zlib';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { initialClosure, routeClosures, staleManifestProblems } from './build_manifest.mjs';

// The manifest maths, re-exported so a test (or another tool) can import the
// budget gate's exact definitions from one place.
export { initialClosure, routeClosures, staleManifestProblems };

const repoRoot = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
const distDir = path.join(repoRoot, 'frontend', 'dist');
const assetsDir = path.join(distDir, 'assets');
const buildMetaDir = path.join(repoRoot, 'frontend', 'build-meta');

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
//   3. fontAssetCount stays exact, both ways: an extra font file is always a
//      mistake, and a build that emits fewer fonts than expected lost a face.
//
// Method (2026-09-24, audit bundle-08). Everything is measured through the
// bundler's own chunk graph, frontend/build-meta/build-manifest.json (written
// by `vite build`, moved out of dist by tools/postbuild_artifacts.mjs; see
// tools/build_manifest.mjs):
//   - initial JS: the index.html entry plus its transitive static `imports`
//     (never `dynamicImports`); initial CSS: the union of the `css` those
//     chunks need.
//   - routes: per route module (a dynamic entry under src/routes/), the route
//     chunk plus its static imports minus the initial closure, JS + CSS. A
//     route module with no entry in `budgets.routes`, or an entry with no
//     route module, fails: a new route goes through the budget owner.
//   - lazy: every JS chunk outside the initial closure; the largest one per
//     dimension is gated.
//   - brotli at quality 11 (what tools/precompress_assets.mjs ships and the
//     backend serves) is the PRIMARY dimension; raw and gzip-9 gates stay.
//   - the manifest must describe the dist beside it: a stale one fails.
//   - vendor chunks (audit bundle-03, vite.config.ts codeSplitting groups):
//     exactly vendor-react and vendor-data, each inside the initial closure,
//     importing only vendor chunks or the runtime helper, and holding no
//     module on the LAZY_ONLY_VENDOR_MODULES list below (the node_modules
//     modules only lazy chunks render), read from each chunk's rendered
//     modules in build-meta/build-modules.json (written by the vite.config.ts
//     chunk-modules plugin). The list is kept exact against the build: every
//     entry must still match a rendered module, and every module of a vendor
//     package rendered only in a lazy chunk must be on it.
// Build-time precompressed .br/.gz siblings are never measured as assets;
// only .js/.css/font files are.
//
// Actuals at the bundle-08 re-baseline (2026-09-24, wave-2 lane
// w2-build-currency, measured on the dependency batch of the same lane):
// see each gate's comment below.
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
  // Re-baselined 2026-09-22 at the wave-0 integration of the UI/UX audit
  // (docs/audits/ui-ux-technology-audit-2026-09-21.md). Shell code that has
  // to live in the initial chunk grew by 16.9 KiB raw / 6.2 KiB gzip across
  // five lanes: the route + root ErrorBoundary, chunk-load detection and the
  // one-shot vite:preloadError reload (it must run before any lazy chunk can
  // fail); routeMeta, useMainScroll, the route announcer and the version
  // notice (shell continuity); the session can_approve / actor identity
  // threading; the GenieDock that keeps an in-flight turn alive while the
  // panel is closed plus the shared Escape stack; session-keyed Reveal and
  // the hashed wordmark. The raw gate had 0.4% headroom left and gzip had
  // none. Measured: initial JS 415.31 / gzip 128.25; restore ~5% headroom.
  // Re-measured 2026-09-23 for wave 1c (lane queue-keyboard-review). Initial
  // JS is now the entry chunk PLUS the chunks it imports statically (then a
  // regex walk over the emitted JS, since replaced by the manifest closure
  // in tools/build_manifest.mjs). Lazy-loading the `?` shortcut sheet made the
  // bundler move React, jsx-runtime and its CommonJS interop helpers (and
  // Icon with them) out of index-*.js into a sibling chunk the entry imports,
  // so index-*.js alone read 417.11 / 129.06, a false 7.8 KiB "shrink" while
  // every first paint still loads that chunk. Measured the same way the base
  // (main 726106a1, no split) is 424.88 / 131.57; this lane adds 7.15 raw /
  // 2.60 gzip of shell code that has to be live before any route renders:
  // the keymap registry (one dispatcher, scopes, the WCAG 2.1.4 single-key
  // switch and its per-actor storage), the `?` sheet host and the Cmd-K
  // selection verbs; the sheet itself and both approve reviews stay lazy.
  // That left 0.9% raw and 0.6% gzip headroom (under the ~0.5 KiB Linux
  // zlib variance), so restore ~5% per policy. Measured: 432.03 / 134.17.
  // Re-baselined 2026-09-24 at the wave-1c integration (all six lanes
  // merged; measured as index + its static chunk closure, see above). Base
  // main 726106a1: 424.88 / 131.57. Measured: 508.65 / 158.40 (+83.77 /
  // +26.83). Per lane, from each lane's own base-vs-head measurement (the
  // sum reproduces the total within 0.3 KiB):
  //   - feedback-guard: React Router's data-router engine behind the
  //     catch-all createBrowserRouter that useBlocker needs for the
  //     unsaved-changes guard (states-05): +53.16 / +16.13, measured against
  //     the same tree under <BrowserRouter>; react-router is sideEffects:false
  //     and react-router/dom adds only flushSync, so nothing else rides along.
  //     It cannot be lazy (the router mounts first). The same engine is what
  //     route loaders need to end the chunk-then-data waterfall (delivery-03).
  //     Plus the guard dialog host and the toast region: +5.50 / +1.85.
  //   - shell-wayfinding: typed route registry, breadcrumbs, identity menu
  //     trigger: +7.86 / +3.17.
  //   - queue-keyboard-review: keymap registry, WCAG 2.1.4 single-key
  //     switch, ? sheet host, Cmd-K verbs: +7.15 / +2.60.
  //   - session-recovery: the session dialog and connection banners, which
  //     must be in the entry because no lazy chunk loads after expiry:
  //     +5.70 / +1.70.
  //   - formatters: cached Intl formatters + <Timestamp>: +4.08 / +1.33.
  // ~5% headroom per policy.
  // Re-baselined 2026-09-24 for the wave-2 minor dependency batch (lane
  // w2-build-currency, audit stack-10). No app code changed; each delta was
  // measured by building the batch with one package held back:
  //   - React 19.3.0 (react + react-dom; react-dom-client.production.js
  //     grows 536 -> 625 KB unminified): +28.59 / +8.40 gzip / +7.22 br.
  //     Wave 3 needs 19.3, and react-dom cannot be lazy.
  //   - React Router 8.4.0 (adds @remix-run/route-pattern): +2.58 / +0.83 /
  //     +0.66 br.
  //   - Vite 8.3.0 (rolldown 1.0.3 -> 1.2.10 output): -3.35 raw.
  // Measured: 536.47 / 166.58 (br 142.81); ~5% headroom per policy.
  // Gzip re-baselined the same day for the lane's bundle-03 vendor split
  // (five initial chunks compress separately: +0.84 gzip), which left it
  // 4.3%; raw kept 4.8% (+0.27). Measured: 536.74 / 167.41.
  initialJsBytes: 564 * KiB, // actual 536.74 (index + rolldown-runtime + vendor-react + vendor-data + apiPaths)
  initialJsGzipBytes: 176 * KiB, // actual 167.41
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
  // Re-baselined 2026-07-15 after the intelligence + trust UX and signed
  // campaign-handoff slices settled. The inherited 141 KiB gate left only
  // 0.05 KiB above the measured artifact, violating the documented ~5%
  // cross-platform headroom policy. Measured: CSS 140.95 / gzip 23.89.
  // Re-baselined 2026-09-22 at the wave-0 integration of the UI/UX audit
  // (docs/audits/ui-ux-technology-audit-2026-09-21.md). Four lanes each added
  // shell CSS that must ship in the initial stylesheet, and the gate had
  // drifted to 0.09 KiB of headroom over the 147.91 KiB base, so every one of
  // them tripped it: the ErrorBoundary recovery surface (15-error-surface.css,
  // +0.70 KiB; it renders when a lazy chunk cannot load, so it cannot be
  // lazy), the "A new version is available" notice (.degraded-banner--info,
  // +0.24 KiB; glossary :target styling was moved to a lazy route stylesheet
  // to keep it out of this number), the Genie launcher running/answer-ready
  // states (16-genie-fab-status.css, +1.43 KiB) and the sparkline draw/fade
  // tokens plus print reveal rule (+0.29 KiB). Measured after the merge:
  // CSS 150.57 / gzip 25.48; ~5% headroom per policy (the gzip gate keeps
  // 4% and is unchanged).
  // Re-baselined 2026-09-24 at the wave-1c integration: +2.05 KiB of shell
  // CSS (29-shell-wayfinding breadcrumbs/identity trigger, 31-session-recovery
  // banners + shaped skeleton sweep, 32-feedback toast region + guard
  // dialog; each must style a surface that can appear before any lazy chunk).
  // Measured: 158.85 / gzip 27.20; ~5% headroom.
  initialCssBytes: 167 * KiB, // actual 159.25 (2026-09-24, manifest CSS closure; 158.81 before the variable fonts, 158.56 before the bold fallback faces)
  // Re-baselined 2026-09-08 for the Genie thread view + deep-research
  // sections slice. The 25 KiB gzip gate had drifted to 4 bytes above the
  // measured artifact (25,596 B) — the next CSS line from anyone would have
  // tripped it — so restore ~5% headroom per this file's policy. Measured:
  // gzip 25.00 (macOS zlib; the file documents ~0.5 KiB Linux variance).
  // Re-baselined 2026-09-23 at the wave-1 integration of the UI/UX audit.
  // The shell stylesheet now carries the theme x accent AA token matrix,
  // color-scheme and the single focus-ring system (theme-contrast), plus the
  // wave-0 shell rules above; raw CSS stays inside its gate. The four
  // wave-1 feature stylesheets that belong to lazy surfaces (decision
  // receipt, Genie refusal card, analytics rate window, Genie turn controls)
  // ship with their lazy chunks instead, and the dead `.admin-filter-input`
  // rules were removed, before this bump. Measured: CSS 155.76 / gzip 26.55;
  // ~5% headroom per policy.
  initialCssGzipBytes: 29 * KiB, // actual 27.30 (2026-09-24; 27.25 before the bold fallback faces)
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
  // Re-baselined 2026-09-22 at the wave-0 integration of the UI/UX audit:
  // the initial-chunk growth above plus four new lazy chunks (the admin
  // 403 surface, the keyboard-operable MultiFilterSelect shared by the
  // Portfolio Builder state picker, the glossary route stylesheet's module,
  // and the Genie launcher status helpers). Measured: total JS 1178.21 KiB
  // / gzip 385.07 KiB across 48 chunks; ~5% headroom on both totals. The
  // largest-lazy-chunk gates are unchanged (states-albers 79.68 / 28.88).
  // Re-baselined 2026-09-23 at the wave-1 integration: eight new lazy
  // chunks and route-chunk growth for the Decision receipt read-back UI,
  // the audited lead export receipt and the URL-driven audit explorer
  // (filters, labels, page CSV), the Genie refusal card, the Genie turn
  // controls and page-context templates, the analytics "why now" rate
  // window, and the RouteErrorBoundary retry. Initial JS is 421.07 / gzip
  // 130.17, inside its gate. Measured: total JS 1256.01 KiB / gzip 412.22
  // KiB across 56 chunks; ~5% headroom on both totals.
  // Re-baselined 2026-09-23 at the wave-1b integration. Each lane fit the old
  // gate alone; together they add 63.9 KiB raw / 23.2 KiB gzip, almost all
  // lazy (measured chunk by chunk against a main build): the geography map's
  // table view, keyboard and screen-reader access, legend breaks and URL /
  // query-cache state (+16.4 / +6.1; the shared map chunk is now named after
  // useMapSelectionParams); the conversation-first /ask-genie tabs, docked
  // composer and extracted Growth Agent workspace (+17.1 / +5.9); the Lead
  // Queue column model, Status cell, overflow chips, workflow strip and view
  // presets (+15.9 / +5.0); the Home answer band (+6.6 / +2.2); segment
  // filters in the URL (+2.1 / +1.1); and the shared listbox + tabs in the
  // initial chunk (+3.8 / +1.4, inside the initial gate). Measured: total JS
  // 1319.94 KiB / gzip 435.39 KiB across 56 chunks; ~5% headroom.
  // Re-baselined 2026-09-24 at the wave-1c integration: +137.1 KiB raw /
  // +47.4 gzip over 726106a1 (1319.95 / 435.39 -> 1457.07 / 482.78, 56 -> 64
  // chunks): the +83.8 initial growth above, plus lazy chunks for the approve
  // review and bulk review, the ? sheet, the Borrower 360 pager, the Offer
  // Orchestrator decision bar and certified-copy preview, the identity popup
  // and the toast/guard styles. ~5% headroom.
  // Re-baselined 2026-09-24 by lane w2-build-currency (audit bundle-08, on
  // its stack-10 dependency batch): React 19.3 + Router 8.4 + Vite 8.3 add
  // +27.82 raw / +8.20 gzip to total JS, all of it in the initial closure
  // (see initialJsBytes), which left 3.0% / 3.2% headroom. Measured: 1485.04
  // / gzip 491.08 across 64 chunks; ~5% headroom.
  // Gzip re-baselined the same day for the lane's bundle-03 vendor split
  // (+1.78 gzip: 64 -> 67 chunks, and lazy chunks import two more
  // specifiers), which left it 4.47%; raw kept 4.65% (+2.40). Measured:
  // 1487.44 / 492.91.
  // Re-baselined 2026-09-25 at the wave-3 integration (UI/UX audit wave 3),
  // attributed by building every first-parent merge step of ux/wave-3 (br
  // KiB, total JS): api-contract -0.02, score-anatomy +6.88 (score spine,
  // proof margins, receipt seal, explorer receipts), rate-lever +4.45 (the
  // Rate Lever scrubber and map facts), genie-jobs +0.91 (job polling client),
  // genie-reading +7.41 (markdown grammar, collapse, audited CSV export),
  // queue-place +5.33 (URL place, system views, bulk progress), motion-nav
  // +1.19 (View Transitions, anchored hover card, nav). 437.77 -> 463.92 br,
  // 1526.70 -> 1603.11 raw, 507.53 -> 537.66 gzip, 67 -> 77 chunks; every new
  // surface is a lazy chunk (initial JS moved +0.76 br). ~5% headroom.
  totalJsBytes: 1684 * KiB, // actual 1603.11 (77 chunks, wave-3 integration)
  totalJsGzipBytes: 565 * KiB, // actual 537.66 (wave-3 integration)
  // Re-baselined 2026-09-23 for wave 1c (lane queue-keyboard-review): the
  // shared LeadTable chunk (Lead Queue + Segment Intelligence) grew from
  // 92.96 / 29.59 to 105.51 / 33.56 with the keyboard triage that must be
  // live when the table mounts: the row cursor and its virtualizer walk,
  // the focus-scoped keymap bindings, the approve-review state machine, the
  // Cmd-K selection publisher and the lazy-module loader. The approve review
  // (7.8 KiB), the bulk gate's review (3.4 KiB) and the `?` sheet already
  // ship as their own lazy chunks. ~5% headroom per policy.
  // Re-baselined 2026-09-24 at the wave-2 integration: the LeadTable chunk
  // grew 109.32 -> 119.36 raw / 34.72 -> 38.37 gzip / 30.07 -> 33.08 br, the
  // w2-queue-query-layer lane moving the queue's governed writes onto
  // TanStack mutations (lib/mutations, useMutation / useMutationState /
  // MutationObserver, which stay lazy: see LAZY_ONLY_VENDOR_MODULES), the
  // decision-on-the-wire guard and the export provenance read. ~5% headroom.
  // Re-baselined 2026-09-25 at the wave-3 integration: LeadTable grew
  // 119.36 -> 127.27 raw / 38.37 -> 41.07 gzip / 33.08 -> 35.32 br with the
  // w3-queue-place lane (sort, focused row and scroll in the URL, honest
  // bulk-run progress, range selection); +2.19 br of it is that lane's.
  maxLazyJsBytes: 134 * KiB, // actual 127.27 (LeadTable, wave-3 integration)
  maxLazyJsGzipBytes: 44 * KiB, // actual 41.07 (LeadTable, wave-3 integration)
  // Re-baselined 2026-09-24 for audit bundle-05 / css-v2 (lane
  // w2-build-currency): the seven static @fontsource faces (7 woff2 + their
  // 7 never-requested woff twins, 215.42 KiB) became one variable woff2 each
  // for Geist and Geist Mono (@fontsource-variable 5.3.0, latin wght:
  // 29,400 + 23,128 B), preloaded by vite.config.ts. Ratcheted down to the
  // measured actual + ~5%.
  fontAssetCount: 2, // exact by policy, both ways
  fontBytes: 54 * KiB, // actual 51.30
  // Brotli q11, the primary dimension (audit bundle-08; see the header). First
  // baselined 2026-09-24 by lane w2-build-currency on its own dependency batch
  // (React 19.3 is +7.22 br of the initial number, Router 8.4 +0.66; see
  // initialJsBytes). ~5% headroom, rounded up to a whole KiB.
  // The bundle-03 vendor split (same lane, same day) costs +0.27 raw / +0.84
  // gzip / +1.15 br on the initial closure and +2.40 / +1.78 / +2.00 on
  // total JS (five initial chunks compress separately; lazy chunks import two
  // more specifiers), and buys 91.58 KiB br (vendor-react 82.73 + vendor-data
  // 8.85, 21.5% of total JS br) that a deploy changing only app code no
  // longer makes a returning browser re-download. That left initial JS br at
  // 4.0% and total JS br at 4.4% headroom, so both were re-baselined to ~5%
  // (review of the lane, 2026-09-24; every gate the lane left under 4.5% was
  // restored, initial CSS br keeps 4.6%).
  // Initial CSS br re-baselined the same day (second review of the lane) for
  // the bold metric-matched fallback faces in tokens.css (a real local Arial
  // Bold / Courier New Bold face per family, so bold text during the font
  // swap is not a synthesized bold): +0.69 raw / +0.05 gzip / +0.07 br, which
  // left it 4.3%. Measured: 22.96 br; +~5% rounded up to a whole KiB.
  initialJsBrBytes: 152 * KiB, // actual 143.92 (5 chunks; 142.81 before the vendor split)
  initialCssBrBytes: 25 * KiB, // actual 22.96 (22.89 before the bold fallback faces; 22.82 with the static @fontsource CSS)
  totalJsBrBytes: 488 * KiB, // actual 463.92 (77 chunks, wave-3 integration; see totalJsBytes)
  maxLazyJsBrBytes: 38 * KiB, // actual 35.32 (LeadTable, wave-3 integration)
  // What a navigation to each route fetches beyond the initial closure: the
  // route chunk plus its static imports, JS + CSS, brotli q11. Keyed by the
  // manifest's source path; every route module must have an entry and every
  // entry a route module (a route added in a later wave goes through the
  // integrator). Baselined 2026-09-24 at measured + ~5%, whole KiB, on
  // the dependency batch; the actuals below are the lane's final tree
  // (vendor split + variable fonts), each within 0.3 KiB of the baseline.
  routes: {
    // wave-3 integration: 4.52 -> 5.01, w3-motion-nav's icon stroke floor
    // and audit glyph in the shared Icon module. ~5% headroom.
    'src/routes/admin-config.access-denied.tsx': 6 * KiB, // actual 5.01
    'src/routes/admin-config.tsx': 35 * KiB, // actual 32.79
    'src/routes/analytics.tsx': 44 * KiB, // actual 41.60
    // wave-2 integration: 50.03 -> 53.12, the w2-genie-turn lane's in-flight
    // turn store, route Stop and single announcer. ~5% headroom.
    // wave-3 integration: 53.01 -> 59.82, w3-genie-reading +5.13 (markdown
    // grammar, collapse, audited CSV), w3-genie-jobs +0.94 (job polling),
    // w3-motion-nav +0.53. ~5% headroom.
    'src/routes/ask-genie.tsx': 63 * KiB, // actual 59.82
    'src/routes/asset.tsx': 8 * KiB, // actual 7.08
    'src/routes/borrower-360.tsx': 38 * KiB, // actual 35.62
    'src/routes/glossary.tsx': 9 * KiB, // actual 8.42
    // wave-3 integration: 40.40 -> 43.24, w3-rate-lever +2.37 (the Rate
    // Lever on the geography hero), w3-motion-nav +0.49. ~5% headroom.
    'src/routes/home.tsx': 46 * KiB, // actual 43.24
    // wave-3 integration: 66.10 -> 67.13 net: w3-score-anatomy moved the
    // proof out (-4.23), w3-queue-place added place + bulk progress (+4.22).
    'src/routes/lead-queue.tsx': 71 * KiB, // actual 67.13
    'src/routes/not-found.tsx': 4 * KiB, // actual 3.44
    // wave-3 integration: ratcheted DOWN, 34.29 -> 31.29 (w3-score-anatomy
    // moved the proof surfaces into lazy chunks). ~5% headroom.
    'src/routes/offer-orchestrator.tsx': 33 * KiB, // actual 31.29
    'src/routes/portfolio-builder.tsx': 34 * KiB, // actual 31.76
    // wave-3 integration: 82.46 -> 83.57 (w3-rate-lever map facts +1.95,
    // w3-queue-place +2.39, w3-score-anatomy -4.24), 0.5% left: restored
    // to ~5% headroom.
    'src/routes/segment-intelligence.tsx': 88 * KiB, // actual 83.57
  },
};

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

function readBuildMeta(file) {
  const abs = path.join(buildMetaDir, file);
  try {
    return JSON.parse(readFileSync(abs, 'utf8'));
  } catch (err) {
    console.error(`Frontend budget check requires ${abs}.`);
    console.error('Run `npm --prefix frontend run build` (tools/postbuild_artifacts.mjs moves it there).');
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
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
  const manifest = readBuildMeta('build-manifest.json');
  const chunkModules = readBuildMeta('build-modules.json');

  const overages = [];
  const failIf = (condition, message) => {
    if (condition) overages.push(message);
  };
  const distChunks = files.filter((f) => /\.(?:js|css)$/.test(f)).map((f) => `assets/${f}`);
  overages.push(...staleManifestProblems(manifest, distChunks));

  const sizes = new Map();
  const sizeOf = (file) => {
    if (!sizes.has(file)) sizes.set(file, { file, ...measureBuffer(readFileSync(path.join(distDir, file))) });
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
  const routes = Object.entries(routeClosures(manifest, initial)).map(([key, closure]) => ({
    key,
    ...sumSizes(measurable([...closure.js, ...closure.css]), sizeOf),
  }));
  const fonts = files.filter((f) => /\.(woff2?|ttf|otf)$/.test(f));
  const fontBytes = fonts.reduce((sum, f) => sum + readFileSync(path.join(assetsDir, f)).length, 0);

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

  if (overages.length > 0) {
    console.error('\nFrontend budget check failed:');
    for (const overage of overages) console.error(`  - ${overage}`);
    process.exit(1);
  }
  console.log('Frontend budget check passed.');
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.join(repoRoot, 'tools', 'check_frontend_budgets.mjs')) {
  main();
}
