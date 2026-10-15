/**
 * The frontend budget gates that tools/check_frontend_budgets.mjs enforces:
 * the numbers, their headroom policy and every re-baseline's attribution.
 * Moved verbatim out of check_frontend_budgets.mjs on 2026-10-01 (the W5b
 * integration) so both files stay under the 900-line cap; the measuring
 * and the gate logic stay there.
 */
export const KiB = 1024;
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
export const budgets = {
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
  // wave-5c integration (2026-10-07): ratcheted DOWN per rule 2. The batch
  // shrank initial JS raw 544.45 -> 530.62 (w5-evidence-drawer moved the
  // registry prose out of the shell, -16.53 raw; the other eight lanes and
  // the integration added +2.70 raw; per-step br attribution under
  // initialJsBrBytes). ~5% headroom. Gzip 169.21 keeps 4.0% under 176 (the
  // gate is not raised for an initial chunk; see initialJsBrBytes).
  // wave-5d integration (2026-10-14): 530.62 -> 535.35 raw / 169.21 -> 171.01
  // gzip (per-step br attribution under initialJsBrBytes). Not raised: 4.1%
  // raw and 2.9% gzip left; an initial-chunk regression is made lazy.
  initialJsBytes: 558 * KiB, // actual 535.35 (8 chunks, wave-5d integration)
  initialJsGzipBytes: 176 * KiB, // actual 171.01 (wave-5d integration; 169.21 at W5c)
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
  // Re-baselined 2026-09-29 at the wave-4b integration: raw 166.75 -> 167.26
  // and gzip 28.91 -> 29.09 left both over their gates. The shell CSS grew
  // with w4-workflow's Field primitive and its QueuePager (+0.04 br),
  // w4-lead-queue's focus-clearance partial (+0.06) and w4-charts'
  // --accent-data token and forced-colors data marks (+0.05). ~5% headroom.
  // wave-5d integration (2026-10-14): 170.38 -> 172.99 raw (1.7% left).
  // w5-print-glossary-sales-manager +2.33 (print.css: the paper ink, the
  // provenance block, page-number margin boxes and the overlays kept off
  // paper; its +0.26 br over the +0.20 lane cap is ruled accepted, 12.4
  // #11), w5-followups-snooze +0.28 (--shell-rail-w, .rail__item.has-due,
  // --z-tray). ~5% headroom.
  initialCssBytes: 182 * KiB, // actual 172.99 (wave-5d integration; 167.26 at wave 4b)
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
  // wave-4b integration: 28.91 -> 29.09 (see initialCssBytes). ~5% headroom.
  // wave-5d integration: 29.80 -> 30.31 (the same two lanes), 2.2% left. ~5%.
  initialCssGzipBytes: 32 * KiB, // actual 30.31 (wave-5d integration; 29.09 at wave 4b)
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
  // Re-baselined 2026-09-25 at the wave-4a integration, attributed by
  // building every first-parent merge step of ux/wave-4a (br KiB, total JS
  // 463.80 -> 484.23): test-infra-pr1 +0.14, delivery-boot +1.42 (boot
  // module, shell skeleton), overlays +4.50 (lazy palette + drawer body
  // chunks; initial JS -4.60), error-surfaces +12.50 (AsyncState, EmptyState,
  // FetchedAt, RetryClock and the error vocabulary shipped per route chunk),
  // growth-agent-trust +1.33, genie-server +0.63, the Home re-point -0.09.
  // Initial JS 146.80 -> 144.09 br. ~5% headroom restored for wave 4b.
  // Re-baselined 2026-09-29 at the wave-4b integration, again attributed by
  // building every first-parent merge step of ux/wave-4b (br KiB, total JS
  // 484.31 -> 501.92):
  //  - w4-workflow +7.93: React Compiler memo code for the newly compiled
  //    Offer Orchestrator and Portfolio Builder routes (runtime-03, about
  //    +4.4), the shared outreach/requestIds mutation chunks moved out of
  //    LeadTable, and the QueuePager chunk. Its fix round cut the draft
  //    restore and the Field adornments (cuts 1-2).
  //  - w4-genie-client +4.43: the conversation deep link, pre-open turn
  //    survival and per-response answer memory.
  //  - w4-delivery-warehouse 0 (backend only).
  //  - w4-test-infra-pr2 +0.05.
  //  - w4-lead-queue +1.92: LeadTableBody, the approval banner, compliance
  //    text, toasts and focus clearance, after its cuts 2-5.
  //  - w4-charts +3.28: the compiled ChartFrame kit, 1-2-5 axes and the
  //    histograms with governed thresholds, after its cuts (a)-(b).
  // The planned W4b caps summed to 11.4; the batch took 17.61. Lanes that
  // could not cut never-cut items asked for this re-baseline in their
  // reports. Initial JS 144.03 -> 144.53 br. Totals: 1701.66 raw /
  // 581.56 gzip / 501.92 br across 95 chunks. ~5% headroom.
  // Re-baselined 2026-10-01 at the wave-5a integration (every first-parent
  // merge step built and measured; br deltas): contract-tooling +0.02,
  // platform-backend +1.90 (lazy tooltip controller + anchor placement),
  // identity-foundation +4.48 (actorScope gate, lazy persist-client +
  // hydration), design-contract +0.18, approval-core +6.42 (bulk reject gate,
  // stratified samples, canary, receipt review row), filters-saved-views
  // +6.13 (range filters, facet counts, saved views route chunk),
  // geo-foundation +5.75 (map split, delegated hover, Signal Stack),
  // growth-agent-trust +3.79 (plan diff, run save, watchlist briefings),
  // integration fixes -0.11. Totals: 1792.25 raw / 615.23 gzip / 530.74 br
  // across 100 chunks (502.18 br before). Every lane reported its growth;
  // none of it is initial JS (144.50 -> 146.41 br, still under the 152
  // gate held for wave 5). ~5% headroom.
  // Re-baselined 2026-10-01 at the wave-5b integration, every first-parent
  // merge step of ux/wave-5b built and measured on one host (br deltas;
  // base 531.12 br, the nine lanes' shared build of 8343364e):
  //  - w5-wire-contract-deps -0.02: the non-major dependency batch is
  //    byte-neutral once @tanstack/react-virtual is held at 3.13.24 (3.14.13
  //    added +2.28 br to the LeadTable chunk; see supply-chain-audit.md).
  //  - w5-approval-ledger-api -0.02 (backend; chunk-hash churn).
  //  - w5-identity-reset +2.03: the reset-document gate, the toast platform
  //    (info tone, one action, Retry-After countdown, F8) and the Genie
  //    pagehide decision window.
  //  - w5-portfolio-forms +1.74: the per-tab campaign draft, Field affixes /
  //    readouts / states and AsyncStatus on Portfolio Builder.
  //  - w5-audit-ledger-presenter +5.62: the /audit-ledger route and its 403
  //    page (the explorer moved out of admin-config), the section nav, the
  //    Run confirm and the lazy PROTOTYPE preview chunk.
  //  - w5-theme-white-label +1.29: the selector store and keyed useApp
  //    facade, the lazy lender mark, Button loading / Chip leading (and the
  //    integrator's auditor palette entry, lazy).
  //  - w5-lead-triage-export +7.81: three new lazy chunks (the Triage deck,
  //    the lifecycle stepper, a split) plus the export honesty notice and
  //    the canary helper (ruled: accepted with attribution).
  //  - w5-home-geo-lever +13.33: the Delta Explainer, watchlist briefings,
  //    the map table / scale split, the stale-data note and FetchedAt as its
  //    own chunk, the Rate Lever change columns (ruled: the lane's +12.95
  //    accepted with attribution).
  //  - w5-genie-verified-reveal +3.36: the verified-sections reveal, the
  //    Figures claims list and the Genie line / bar charts on the shared
  //    chart kit.
  //  - integration +0.42: TanStack Query held at 5.100.10 (5.104.0 failed
  //    the fixture suite; see supply-chain-audit.md), measured on the final
  //    tree.
  // Totals: 1888.91 raw / 656.11 gzip / 566.68 br across 118 chunks (100
  // before). Initial JS 146.40 -> 148.78 br (+2.38, inside the 3.0 KiB batch
  // cap; the 152 gate is held). ~5% headroom.
  // Re-baselined 2026-10-07 at the wave-5c integration: every first-parent
  // merge step of ux/wave-5c built and measured on one host (br deltas; base
  // d99eb60b, the W5b head, 566.40 br / 1889.23 raw / 656.29 gzip across 118
  // chunks). The nine lanes' total-JS caps summed to 31.5 KiB br against
  // ~29.6 of headroom; the batch took 41.78, each lane reported its growth
  // with attribution and G5 forbids cutting a registered feature for it:
  //  - w5-evidence-drawer +4.95: EvidenceDrawerBody 4.50 -> 11.09 (the
  //    registry prose moved out of the initial chunk, about 3.5, plus How we
  //    got, freshness, the KPI proof card and Under the hood), the
  //    useWarmingUpRetry split and the asset route chunk.
  //  - w5-lead-queue-paging +3.88: the shared useInfiniteQuery chunk,
  //    lead-queue.pages.ts, leadsPaged.ts, the keep-alive slot and the paged
  //    footer.
  //  - w5-refusal-capture-sales +5.86: RefusalReportsPanel / RefusalReportRow,
  //    the shared genieRefusal split, the lazy consent confirm and
  //    optionalQueryReads.
  //  - w5-dossier-decisions +5.52: BorrowerDecisionHistory, the shared
  //    auditEventPresentation split and the lazy borrowerMorph chunk.
  //  - w5-gold-slot-cache +3.71: the lazy ScoreAnatomy proof chunk carries
  //    SpreadHistoryChart, its model and copy, and the margins section.
  //  - w5-genie-stop-context +0.32: the Stop client and the action receipt
  //    (lazy), net of the deleted genieVerifiedReveal side channel.
  //  - w5-shell-nav-followups +0.87: the dock hook and clustered RouteNav, the
  //    lazy RailRoadmap chunk (0.54).
  //  - w5-field-vitals +8.77: the lazy web-vitals/attribution chunk (+4.80),
  //    the Field performance panel (+2.57), the rum chunk's glue and closed
  //    vocabularies (+0.86), the admin-config mapDeps list (and the
  //    integrator's two-step Rail roadmap guard, a few bytes).
  //  - w5-zcta-watchlist +7.80: the lazy ZCTA rung (6.27 JS), the Segments
  //    ZIP-areas stage and the shared map chunk's seam.
  //  - integration +0.10: the rung's 'absent' outcome (no false load-failure
  //    line while no ZCTA geometry is committed).
  // Totals: 2011.14 raw / 703.86 gzip / 608.18 br across 131 chunks. Initial
  // JS 148.72 -> 146.15 br (net -2.57; see initialJsBrBytes). ~5% headroom.
  // wave-5d integration (2026-10-14; per merge step, br; base 05d639b3
  // measured 608.15 across 131 chunks):
  //  - w5-actor-binding +0.58: the transport binding and rotation aliases,
  //    the workspace mutations on the shared mutation cache.
  //  - w5-lq-requests-binding +10.03: the lazy RequestApprovalForm (3.67
  //    closure), My requests (2.77) and Approval requests (1.29) panels, the
  //    approvalRequests client, the request marks, scope row and decision
  //    toast (route / review chunks), GOVERNED_TEXT_REFUSED copy.
  //  - w5-sales-loop +8.77: the lazy DistributionBoard (7.33 closure),
  //    salesUndoRun (1.91), SalesPeriodRange (2.58, Field rides it), the
  //    shared salesLoop client / salesUndoToasts chunks.
  //  - w5-palette-offer-sales-loop +5.57: the palette dialog's provider
  //    sources, ranking and keycaps (+2.08), the Offer route chunk (+1.22),
  //    Field joining the Offer closure (+1.47), the lazy OfferFailureSentence
  //    (1.39), the QueuePager scope.
  //  - w5-followups-snooze +5.91: the lazy FollowUpsRailItem (2.08 closure)
  //    and FollowUpsTray (14.69 closure; its own chunk 4.3, the rest shared
  //    status / mutation chunks first loaded by the tray), the summary
  //    client.
  //  - w5-print-glossary-sales-manager +5.76: usePrintReceipt (1.35, shared
  //    by Borrower 360 and the ledger), the explorer's compiled route chunk
  //    and Action menu, the lazy AdminAuditExplorer.export (1.23), the exit
  //    presence helper, glossary search.
  //  - w5-genie-context-deeplink +2.38: the shared genieContext chunk (+0.62
  //    in six route closures), the context-scope client, the conversation
  //    switch, the outline.
  //  - w5-genie-provenance-tiles +1.38: genieFigureTokens and the figures
  //    model in the genie-answer chunk, the lazy GenieFigureMark (0.73).
  //  - w5-growth-watchlists +2.06: the RateWatchControl in the lazy
  //    RateScenarioControl (4.71 closure), the plan progress mapping.
  //  - integration +0.15: the Offer's actor_changed sentence (c7c94d5d).
  // Totals: 2129.35 raw / 753.13 gzip / 650.74 br across 148 chunks
  // (+42.59 br against 29.0 of summed lane caps; no registered feature was
  // cut, G5). Initial JS 146.13 -> 147.60 br (see initialJsBrBytes). ~5%.
  totalJsBytes: 2236 * KiB, // actual 2129.35 (wave-5d integration)
  totalJsGzipBytes: 791 * KiB, // actual 753.13 (wave-5d integration)
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
  // Re-baselined 2026-09-29 at the wave-4b integration. The LeadTable chunk
  // (built as FetchedAt-*.js since wave 4a) went 128.41 -> 131.07 raw /
  // 41.43 -> 42.33 gzip / 35.68 -> 36.33 br. w4-workflow moved the mutation
  // runtime out (-1.37 br), and w4-lead-queue added LeadTableBody, the
  // approval banner, compliance text and write-failure toasts (+1.98 br).
  // ~5% headroom.
  // wave-5a integration: the LeadTable chunk 131.07 -> 137.01 raw / 42.33 ->
  // 44.56 gzip / 36.33 -> 37.96 br, almost all w5-approval-core (+5.98 raw /
  // +2.21 gz / +1.59 br: the arming status line, kind-aware runs, the canary
  // and the WebKit pinned-focus guard), 0.7% raw left. ~5% headroom.
  // wave-5b integration: the LeadTable chunk 137.41 -> 139.46 raw / 44.75 ->
  // 45.68 gzip / 38.03 -> 38.72 br. w5-lead-triage-export +3.03 raw / +1.33
  // gz / +0.91 br (the Triage entry, the export label and notice wiring; the
  // deck, stepper and canary are lazy), then w5-home-geo-lever split
  // FetchedAt into its own chunk (-0.98 / -0.39 / -0.23). 2.8% gzip left;
  // ~5% headroom restored before W5c's w5-lead-queue-paging.
  // wave-5c integration: rolldown names the LeadTable chunk leadsQuery-*
  // since w5-lead-queue-paging (the shared paged-read module now leads it).
  // 139.63 -> 142.02 raw / 45.74 -> 46.61 gzip / 38.73 -> 39.52 br: paging
  // +2.62 raw / +0.84 gz / +0.76 br (the paged footer, the keyset cursor
  // plumbing and the server-sort switch), evidence-drawer +0.08 br, the rest
  // hash churn. It did not cross 41 br; ~5% headroom restored on all three.
  // wave-5d integration: leadsQuery 142.02 -> 146.86 raw / 46.62 -> 48.52
  // gzip / 39.47 -> 40.89 br. w5-lq-requests-binding +1.78 br (the request
  // read wiring, the per-row mark slot, the bulk stop on actor_changed and
  // Button loading in the toolbar; the chips, marks markup, scope row and
  // toast moved out first), w5-sales-loop -0.39 (mutations/sales moved to
  // the shared salesUndoToasts chunk), the rest hash churn. 1.0% gzip
  // left: ~5% headroom restored on all three.
  maxLazyJsBytes: 155 * KiB, // actual 146.86 (leadsQuery chunk = LeadTable, wave-5d integration)
  maxLazyJsGzipBytes: 51 * KiB, // actual 48.52 (leadsQuery chunk = LeadTable, wave-5d integration)
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
  // wave-5b integration (per merge step, br): wire-contract-deps -0.05,
  // approval-ledger-api -0.06, identity-reset +0.96 (the reset gate in the
  // actorScope chunk, healthPoll recheck, toast store fields, the F-key
  // rule; its +0.08 Console skip-link fix 38e67d79 is ruled accepted),
  // portfolio-forms -0.03, audit-ledger-presenter +0.41 (the route registry
  // and preload entries for two lazy routes, the shared SessionRouteGate;
  // its +0.15 over the lane cap is ruled accepted), theme-white-label +0.63
  // (selector store, keyed facade, tenant defaults, lazy mark gate),
  // lead-triage-export -0.01, home-geo-lever +0.29 (geo path builders,
  // headers.ts), genie-verified-reveal +0.02, integration +0.22 (TanStack
  // Query held at 5.100.10 in vendor-data). 146.40 -> 148.78 (+2.38, inside
  // the 3.0 KiB batch cap). The gate is HELD at 152: an initial-chunk
  // regression is made lazy, never paid for by raising this number.
  // wave-5c integration (per merge step, br; base d99eb60b measured 148.72):
  // evidence-drawer -3.38 (index 46.34 -> 42.78: the prose registry went lazy;
  // queryKeys +0.19), lead-queue-paging +0.16 (the keep-alive mount in
  // RouteTransition, setQueryDefaults, the auditedPages predicate; ruled
  // accepted net of the evidence return), refusal-capture-sales +0.08
  // (mapDeps entries for four lazy chunks), dossier-decisions +0.24 (the
  // dynamic borrowerMorph import in AppShell, the decisions query key),
  // gold-slot-cache +0.04 (hash churn, raw +0), genie-stop-context -0.06,
  // shell-nav-followups +0.25 (the dock hook and policy, the clustered
  // RouteNav, usePresenterMode), field-vitals +0.09 (the bridge timeline and
  // committed-route read; the Rail guard), zcta-watchlist +0.04 (raw +0),
  // integration -0.03. 148.72 -> 146.15 (-2.57): evidence-drawer's 3.38
  // return funded the batch's +0.81. The gate is HELD at 152.
  // wave-5d integration (per merge step, br; base 05d639b3 measured 146.13;
  // lane caps summed to 1.00): actor-binding +0.56 (cap 0.30: items 6+8
  // the binding and aliases, item 13 the AppContext cleanup; cuts 1 and 2
  // taken, the rest never-cut), lq-requests-binding +0.07, sales-loop
  // -0.13 (rolldown regrouping, same 187 modules), palette-offer-sales-loop
  // +0.58 (cap 0.15: useBorrowerSearch compiled in the index chunk, the
  // queue-pager keymap scope, queryKeys; the compiled Topbar would add
  // +1.03 more and stays behind its measured budget-trade pragma),
  // followups-snooze +0.11 (the Rail summary wiring, two query keys),
  // print-glossary-sales-manager +0.31 (cap 0.15: the shell exits in
  // IdentityMenu, the ? sheet host and DrawerBoundary), context-deeplink
  // -0.03, provenance-tiles -0.06, growth-watchlists +0.09 (two additive
  // RUM vocabulary lines), integration -0.03 (hash churn, raw +0). 146.13
  // -> 147.60 (+1.47; the three overages
  // are ruled accepted with this attribution, 12.4 #11). The gate is HELD
  // at 152: W5e keeps 4.40 KiB br.
  initialJsBrBytes: 152 * KiB, // actual 147.60 (8 chunks, wave-5d integration)
  // wave-4b integration: 24.17 -> 24.32 br (see initialCssBytes), which left
  // 2.7%. Re-baselined to ~5%.
  // wave-5b integration: 24.53 -> 24.74 br (theme-white-label +0.23: the
  // success-fill, spin and mark-plate tokens and the forced-colors Highlight
  // states; genie-verified-reveal -0.05 dead rules), 4.9% left: unchanged.
  // wave-5c measured 24.88 (the comment above stayed at the W5b number).
  // wave-5d integration: 24.88 -> 25.20 br (print-glossary-sales-manager
  // +0.26, followups-snooze +0.06; see initialCssBytes), 3.1% left. ~5%.
  initialCssBrBytes: 27 * KiB, // actual 25.20 (wave-5d integration; 24.88 at W5c)
  totalJsBrBytes: 684 * KiB, // actual 650.74 (148 chunks, wave-5d integration; see totalJsBytes)
  maxLazyJsBrBytes: 43 * KiB, // actual 40.89 (leadsQuery = LeadTable chunk, wave-5d integration; see maxLazyJsBytes)
  // What a navigation to each route fetches beyond the initial closure: the
  // route chunk plus its static imports, JS + CSS, brotli q11. Keyed by the
  // manifest's source path; every route module must have an entry and every
  // entry a route module (a route added in a later wave goes through the
  // integrator). Baselined 2026-09-24 at measured + ~5%, whole KiB, on
  // the dependency batch; the actuals below are the lane's final tree
  // (vendor split + variable fonts), each within 0.3 KiB of the baseline.
  // A br gate per named lazy module over its closure beyond the initial
  // closure and every dynamic importer's (tools/build_manifest.mjs
  // lazyEntryClosures; added by w5-zcta-watchlist, D-dataviz-geo-e). Set at
  // the wave-5c integration from measured actuals + ~5%, whole KiB.
  lazyModules: {
    // The ZCTA polygon rung (JS + CSS, 2 files), loaded on a Segments ZIP
    // drill only. Measured 6.71 (the lane's 6.42-6.68 plus the
    // integration's 'absent' outcome); the record target is 8 KiB br.
    'src/components/mortgage/USChoroplethMapZctaLevel.tsx': 8 * KiB, // actual 6.71
    // The presenter-only M1-M4 roadmap slots (w5-shell-nav-followups);
    // a customer session never requests it.
    'src/components/layout/RailRoadmap.tsx': 1 * KiB, // actual 0.54
    // wave-5d integration (2026-10-14): every new W5d lazy entry, measured
    // on the merged tree (closure beyond the initial closure and its
    // importers, JS + CSS) + ~5%, whole KiB. Not entries: the salesLoop
    // client and usePrintReceipt are shared chunks (inside DistributionBoard
    // / salesUndoRun and the two print route closures), GenieHistoryMenu's
    // exit CSS ships with the route, and SalesWorkloadTable and
    // FirstRunChecklist were cut to W5e (no chunk exists).
    // w5-lq-requests-binding: the request form and both request panels.
    'src/components/mortgage/RequestApprovalForm.tsx': 4 * KiB, // actual 3.67
    'src/components/mortgage/ApprovalRequestsPanel.tsx': 2 * KiB, // actual 1.29
    'src/components/mortgage/MyApprovalRequestsPanel.tsx': 3 * KiB, // actual 2.77
    // w5-sales-loop: the Distribution Board, the Undo run and the custom range.
    'src/components/mortgage/DistributionBoard.tsx': 8 * KiB, // actual 7.33
    'src/components/mortgage/salesUndoRun.ts': 3 * KiB, // actual 1.91
    'src/components/mortgage/SalesPeriodRange.tsx': 3 * KiB, // actual 2.58
    // w5-palette-offer-sales-loop: the Offer's refusal sentence.
    'src/components/mortgage/OfferFailureSentence.tsx': 2 * KiB, // actual 1.39
    // w5-followups-snooze: the rail item and the tray (the tray closure
    // carries the status / mutation chunks the shell has not loaded yet).
    'src/components/layout/FollowUpsRailItem.tsx': 3 * KiB, // actual 2.08
    'src/components/layout/FollowUpsTray.tsx': 16 * KiB, // actual 14.69
    // w5-print-glossary-sales-manager: the click-time explorer CSV export.
    'src/components/admin/AdminAuditExplorer.export.ts': 2 * KiB, // actual 1.23
    // w5-genie-provenance-tiles: the in-prose figure mark.
    'src/components/mortgage/GenieFigureMark.tsx': 1 * KiB, // actual 0.73
    // w5-growth-watchlists: the Rate Lever with Watch this level.
    'src/components/mortgage/RateScenarioControl.tsx': 5 * KiB, // actual 4.71
  },
  routes: {
    // wave-3 integration: 4.52 -> 5.01, w3-motion-nav's icon stroke floor
    // and audit glyph in the shared Icon module. ~5% headroom.
    // wave-5b: 5.28 -> 5.40 (audit-ledger-presenter +0.05 copy,
    // theme-white-label +0.07 shared Primitives). Gate unchanged.
    'src/routes/admin-config.access-denied.tsx': 6 * KiB, // actual 5.40
    // wave-4a: 33.75 -> 34.63 (error-surfaces +0.84: AsyncState/describeApiError).
    // wave-5b: ratcheted DOWN, 35.40 -> 24.79 (w5-audit-ledger-presenter
    // -10.76: the explorer moved to /audit-ledger; the section nav and the
    // Run confirm came in; theme-white-label +0.05). ~5% headroom.
    // wave-5c: 24.79 -> 26.06 (field-vitals +0.79: the lazy Field
    // performance import's mapDeps list and its Suspense reserve;
    // evidence-drawer +0.35 shared; shell-nav +0.08 the section nav's
    // overflow cue). 3.6% left: ~5% headroom restored.
    'src/routes/admin-config.tsx': 28 * KiB, // actual 26.06
    // wave-5c (w5-field-vitals): the admin Field performance panel, a lazy
    // route module (11 files). The lane measured 12.05-12.10 with a shared
    // chunk the merged tree splits differently. ~5% headroom.
    'src/routes/admin-config.field-performance.tsx': 12 * KiB, // actual 11.35
    // wave-4a: 42.32 -> 43.24 (error-surfaces +0.95).
    // wave-4b: 43.30 -> 46.71 (w4-charts +3.42: the compiled ChartFrame kit,
    // histograms with governed thresholds, Evidence per day on the kit; its
    // cuts (a)-(b) applied; the per-tab lazy step needs a routes entry and
    // +1.1-1.2 total, recorded for wave 5).
    // wave-5b: 47.54 -> 49.01 (genie-verified-reveal +1.10: the chart kit
    // chunk is now shared with the Genie answer, so it re-split; lead-
    // triage-export +0.34; theme-white-label +0.07). ~5% headroom.
    // wave-5d: 49.53 -> 52.04 (sales-loop +1.61: the URL period control and
    // the every-officer conversion list in the route chunk, the Board opener;
    // the custom range and Board are lazy; context-deeplink +0.63 the shared
    // genieContext chunk; print +0.22 shared exits). ~5% headroom.
    'src/routes/analytics.tsx': 55 * KiB, // actual 52.04
    // wave-2 integration: 50.03 -> 53.12, the w2-genie-turn lane's in-flight
    // turn store, route Stop and single announcer. ~5% headroom.
    // wave-3 integration: 53.01 -> 59.82, w3-genie-reading +5.13 (markdown
    // grammar, collapse, audited CSV), w3-genie-jobs +0.94 (job polling),
    // w3-motion-nav +0.53. ~5% headroom.
    // wave-4a: 60.02 -> 61.87 (growth-agent-trust +1.33 plan execute,
    // genie-server +0.57 cancel).
    // wave-4b: 61.89 -> 64.07 (w4-genie-client +2.03: deep link state,
    // pre-open turn survival, answer memory; w4-charts +0.15), 1.4% left.
    // wave-5a: 64.06 -> 69.42 (w5-growth-agent-trust +3.89 plan diff, run
    // save and focus; w5-identity-foundation +1.06 actorScope readers in
    // the Genie stores; platform-backend +0.26).
    // wave-5b: 69.40 -> 76.50 (w5-genie-verified-reveal +6.76: the shared
    // chart kit enters the closure, CountChart JS 3.05 + CSS 0.51; the
    // genie-answer chunk +2.30 with line and bar charts, drill, claims,
    // reveal and preview, minus the old SVG charts; the rest is chunk
    // re-splitting and lazy sheets; identity-reset +0.17 pagehide window;
    // home-geo-lever +0.10). ~5% headroom.
    // wave-5c: 76.42 -> 78.68 (refusal-capture-sales +1.55: the shared
    // genieRefusal split, the lazy consent confirm and optionalQueryReads;
    // genie-stop-context +0.39: the Stop client and the receipt;
    // evidence-drawer +0.24 shared). ~5% headroom.
    // wave-5d: 78.58 -> 81.41 (context-deeplink +0.98: the conversation
    // switch, outline, feedback memory and History exit; provenance-tiles
    // +0.99: genieFigureTokens, the figures model and O2 copy, the mark lazy;
    // growth-watchlists +0.81: plan progress and the rate-watch card; 2.78
    // against the ruled 4.2 of caps). ~5% headroom.
    'src/routes/ask-genie.tsx': 86 * KiB, // actual 81.41
    // wave-5b: 7.87 -> 7.95 (theme-white-label +0.06), 0.7% left. ~5%.
    // wave-5c: 7.95 -> 8.50 (evidence-drawer +0.55: the route renders
    // failures through AsyncStatus and the slim registry). 5.9% left: unchanged.
    'src/routes/asset.tsx': 9 * KiB, // actual 8.50
    // wave-5b (w5-audit-ledger-presenter): the ledger's 403 page, a second
    // measured route module beside admin-config's (theme-white-label +0.07).
    'src/routes/audit-ledger.access-denied.tsx': 6 * KiB, // actual 5.37
    // wave-5b (w5-audit-ledger-presenter): the audit ledger route, the
    // explorer moved from admin-config (12 files). ~5% headroom.
    // wave-5c: 21.18 -> 21.86 (evidence-drawer +0.34 shared, refusal-capture-
    // sales +0.32: the 'Show refusal reports' toggle; the panel itself is a
    // lazy chunk loaded on an explicit open). 5.2% left: unchanged.
    // wave-5d: 21.89 -> 25.60 (print-glossary-sales-manager +3.67 against
    // its +1.1 cap, ruled accepted: the compiled explorer route chunk +1.14,
    // the facet-fed Action menu and Exclude toggle +0.8, usePrintReceipt
    // 1.35 shared with Borrower 360, the FilterSelect exits; the CSV export
    // is a click-time lazy module). ~5% headroom.
    'src/routes/audit-ledger.tsx': 27 * KiB, // actual 25.60
    // wave-4a: 36.x -> 37.44 (delivery-boot +0.4, error-surfaces +0.37).
    // wave-5b: 38.26 -> 38.51 (audit-ledger-presenter +0.20 ledger access in
    // the receipt, portfolio-forms +0.06, theme-white-label +0.03).
    // wave-5c: 38.51 -> 40.01 (dossier-decisions +1.07: the Decision history
    // mount, its lazy-module loader and the title morph; evidence-drawer
    // +0.35 shared; gold-slot-cache +0.07). ~5% headroom.
    // wave-5d: 40.07 -> 42.29 (print-glossary-sales-manager +2.07 against
    // its +1.2 cap, ruled accepted: usePrintReceipt 1.35 and Export PDF,
    // the In the Money labels and the chunk-only Offer preload;
    // palette-offer-sales-loop +0.23, the shared search). ~5% headroom.
    'src/routes/borrower-360.tsx': 45 * KiB, // actual 42.29
    // w4 pre-cut: 8.42 -> 8.99 on the CI (Linux) build, which measures ~0.1
    // KiB br more per route than the macOS builds these gates were set on;
    // SurfaceTitle folded into the shared Primitives chunk adds ~0.1 (a
    // separate chunk cost +0.29). Re-baselined from the CI number, ~5%.
    // wave-5b: 9.17 -> 9.24 (theme-white-label +0.07). Gate unchanged.
    // wave-5d: 9.35 -> 10.21 (print-glossary-sales-manager +0.84 against its
    // +0.7 cap, ruled accepted: the ?q= search field, matcher and status).
    'src/routes/glossary.tsx': 11 * KiB, // actual 10.21 (macOS build)
    // wave-3 integration: 40.40 -> 43.24, w3-rate-lever +2.37 (the Rate
    // Lever on the geography hero), w3-motion-nav +0.49. ~5% headroom.
    // wave-4a: 43.14 -> 44.17 (error-surfaces +1.16 AsyncState on Home).
    // wave-5a: 44.26 -> 48.03 (w5-geo-foundation +2.75 delegated hover,
    // map split and table groups; identity +0.60; platform-backend +0.30).
    // wave-5b: 48.06 -> 51.10 (w5-home-geo-lever +2.93: FetchedAt in the
    // hero, the WHY NOW rate move, the briefings card host, the stale note;
    // the map table and the note were made lazy first; theme-white-label
    // +0.08). ~5% headroom.
    // wave-5c: 51.23 -> 52.22 (zcta-watchlist +0.52: the shared map chunk's
    // ZIP-stage seam, scale branch, caption / zoom / note pass-through and the
    // briefings slot fix; evidence-drawer +0.42: KpiCard proof props and the
    // slim registry). ~5% headroom.
    // wave-5d: 52.24 -> 52.84 (context-deeplink +0.63, the shared
    // genieContext chunk), 4.1% left. ~5% headroom.
    'src/routes/home.tsx': 56 * KiB, // actual 52.84
    // wave-3 integration: 66.10 -> 67.13 net: w3-score-anatomy moved the
    // proof out (-4.23), w3-queue-place added place + bulk progress (+4.22).
    // wave-4a: 67.13 -> 69.44 (error-surfaces +1.90 FetchedAt, EmptyState,
    // queue version; overlays +0.22).
    // wave-4b: 69.55 -> 72.71 (w4-lead-queue +2.27, w4-workflow +0.78
    // shared mutation chunks), 0.4% left.
    // wave-5a: 72.67 -> 79.85 (w5-filters-saved-views +4.50 range
    // filters and facet counts, w5-approval-core +2.34, platform +0.21).
    // wave-5b: 80.02 -> 82.41 (lead-triage-export +1.11 Triage entry and
    // export honesty, portfolio-forms +0.64 the shared Field chunk,
    // home-geo-lever +0.51 FetchedAt chunk and headers, theme-white-label
    // +0.16, audit-ledger-presenter +0.04). ~5% headroom.
    // wave-5c: 82.50 -> 86.47 (lead-queue-paging +3.36: the shared
    // useInfiniteQuery chunk, lead-queue.pages.ts, leadsPaged.ts, the
    // keep-alive slot and the paged footer; evidence-drawer +0.45 shared; the
    // rest within +-0.1 of hash churn). ~5% headroom.
    // wave-5d: 86.47 -> 92.46 (lq-requests-binding +4.17: the request chips,
    // marks markup, scope row and request read in the route chunk, the
    // leadsQuery growth above; sales-loop +0.97: the shared salesUndoToasts
    // chunk the LeadTable closure imports; context-deeplink +0.62: the
    // scoped Ask on empty states; palette +0.19). ~5% headroom.
    'src/routes/lead-queue.tsx': 98 * KiB, // actual 92.46
    // wave-5a (w5-filters-saved-views): the Saved views panel, a lazy route
    // module whose closure statically reaches the lead-queue chunk.
    // wave-5b: 82.79 -> 85.17, the lead-queue closure's growth above.
    // wave-5c: 85.26 -> 89.24, the lead-queue closure's growth above
    // (paging +3.35, evidence-drawer +0.46). ~5% headroom.
    // wave-5d: 89.24 -> 95.31, the lead-queue closure's growth above
    // (lq +4.16, sales +0.97, deeplink +0.62, palette +0.31). ~5% headroom.
    'src/routes/lead-queue.savedViews.tsx': 101 * KiB, // actual 95.31
    // w4 pre-cut: 3.93 (wave 3) -> 4.00 on the CI build (see glossary above).
    // wave-5b: 4.18 -> 4.26 (theme-white-label +0.07). Gate unchanged.
    'src/routes/not-found.tsx': 5 * KiB, // actual 4.26 (macOS build)
    // wave-3 integration: ratcheted DOWN, 34.29 -> 31.29 (w3-score-anatomy
    // moved the proof surfaces into lazy chunks). ~5% headroom.
    // wave-4a: 31.40 -> 31.77 (error-surfaces +0.37).
    // wave-4b: 31.75 -> 38.47 (w4-workflow +6.71: compiler memo code for
    // the newly compiled route about +1.9, the shared mutation chunks
    // +1.2, the QueuePager chunk +1.25, the query-layer reads and writes;
    // test-infra-pr2's ActivationLoopPanel move was dropped). The
    // queue-context-only QueuePager load is a wave-5 lever.
    // wave-5b: 38.97 -> 38.38 (audit-ledger-presenter -0.79: the PROTOTYPE
    // preview is its own lazy chunk; lead-triage-export +0.25 the shared
    // roster read). Gate unchanged.
    // wave-5c: 38.40 -> 38.97 (evidence-drawer +0.39 shared,
    // dossier-decisions +0.28: the Prior decisions mount). 5.2% left: unchanged.
    // wave-5d: 39.04 -> 42.07 (palette-offer-sales-loop +3.04 against its
    // +1.2 cap, ruled accepted: Field joins the closure +1.47, the route
    // chunk +1.22 (the verified binding, describeApiError copy, the pending
    // banner, the mock exit), the QueuePager scope +0.17; the refusal
    // sentence is lazy; integration +0.03, the actor_changed branch).
    // ~5% headroom.
    'src/routes/offer-orchestrator.tsx': 45 * KiB, // actual 42.10
    // wave-4b: 32.88 -> 37.48 (w4-workflow +4.61: compiler memo code for
    // the newly compiled route about +2.6, campaign mutations, the Field
    // primitive on the numeric editors).
    // wave-5b: 38.41 -> 42.41 (w5-portfolio-forms +3.94, ruled accepted:
    // AsyncStatus brings the shared AsyncState + useLazyModule chunks 1.52,
    // DescribedError 0.54, the Field affixes / readouts / states 0.62, the
    // route chunk +1.26 of which the per-tab draft is about 0.97;
    // theme-white-label +0.05). ~5% headroom.
    // wave-5d: 42.88 -> 43.71 (context-deeplink +0.64 the shared
    // genieContext chunk, print +0.19 shared exits), 2.9% left. ~5%.
    'src/routes/portfolio-builder.tsx': 46 * KiB, // actual 43.71
    // wave-3 integration: 82.46 -> 83.57 (w3-rate-lever map facts +1.95,
    // w3-queue-place +2.39, w3-score-anatomy -4.24), 0.5% left: restored
    // to ~5% headroom.
    // wave-4a: 83.43 -> 86.37 (error-surfaces +2.59 FetchedAt/EmptyState on
    // Segments, overlays +0.29).
    // wave-4b: 86.39 -> 89.57 (w4-lead-queue +2.21 via the shared LeadTable
    // chunk, w4-workflow +0.89 shared mutation chunks), 1.6% left.
    // wave-5a: 89.57 -> 98.03 (w5-geo-foundation +5.90 Signal Stack and the
    // map, w5-approval-core +1.76 shared LeadTable chunk, filters +0.41).
    // wave-5b: 98.20 -> 99.56 (lead-triage-export +0.71 via the shared
    // LeadTable chunk, home-geo-lever +0.49 the stale note and FetchedAt
    // chunk, theme-white-label +0.13). ~5% headroom.
    // wave-5c: 99.61 -> 102.34 (zcta-watchlist +1.36: the ZIP-areas stage
    // +0.77 and the shared map chunk; lead-queue-paging +0.73 via the shared
    // LeadTable chunk; evidence-drawer +0.43 shared; integration +0.09 the
    // rung's 'absent' outcome). ~5% headroom.
    // wave-5d: 102.28 -> 106.15 (lq-requests-binding +1.82 and sales-loop
    // +1.19 via the shared LeadTable chunks, context-deeplink +0.65 the
    // shared genieContext chunk), 1.7% left. ~5% headroom.
    'src/routes/segment-intelligence.tsx': 112 * KiB, // actual 106.15
  },
};
