/**
 * The per-lazy-module budget gates (budgets.lazyModules) that
 * tools/check_frontend_budgets.mjs enforces. Moved verbatim out of
 * frontend_budget_gates.mjs at the W5e integration (2026-10-15) so that file
 * stays under the 900-line cap; frontend_budget_gates.mjs imports it.
 */
const KiB = 1024;
// A br gate per named lazy module over its closure beyond the initial
// closure and every dynamic importer's (tools/build_manifest.mjs
// lazyEntryClosures; added by w5-zcta-watchlist, D-dataviz-geo-e). Set at
// the wave-5c integration from measured actuals + ~5%, whole KiB.
export const lazyModuleBudgets = {
  // The ZCTA polygon rung (JS + CSS, 2 files), loaded on a Segments ZIP
  // drill only. Measured 6.71 (the lane's 6.42-6.68 plus the
  // integration's 'absent' outcome); the record target is 8 KiB br.
  // post-W5 zcta-geometry (2026-10-26): 6.72 -> 7.50 with the committed
  // geometry, whose 51 hashed asset URLs fill the eager glob map (raw
  // 17.62 -> 20.95); the JSON itself is a separate asset, never in the chunk.
  'src/components/mortgage/USChoroplethMapZctaLevel.tsx': 8 * KiB, // actual 7.51 (post-W5 integration)
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
  // wave-5e: 4.75 -> 5.03 (lexicon-copy +0.27: the In the Money lever copy). ~5%.
  'src/components/mortgage/RateScenarioControl.tsx': 6 * KiB, // actual 5.03
  // wave-5e integration (2026-10-15): every new W5e lazy entry, measured on
  // the final tree (closure beyond the initial closure and its importers,
  // JS + CSS) + ~5%, whole KiB. ProofPanels.css (css-cascade) is not an
  // entry; it rides with the two dynamic entries that import it.
  'src/components/mortgage/SalesWorkloadTable.tsx': 3 * KiB, // actual 2.31 (analytics)
  'src/components/mortgage/RevokeApprovalPanel.tsx': 3 * KiB, // actual 2.70 (lead-table-compile)
  'src/components/mortgage/RevokeApprovalPanel.failure.ts': 2 * KiB, // actual 1.14 (lead-table-compile)
  'src/components/mortgage/SimilarCohortPanel.tsx': 2 * KiB, // actual 1.76 (lead-queue-columns)
  // post-W5: 3.70 -> 3.86-3.88 (2 files): the LeadTable cut put leadCount in
  // its own chunk, which the popover shares with LeadRowExpanded. 0.12 left.
  'src/components/mortgage/LeadQueueOmniboxPopover.tsx': 4 * KiB, // actual 3.88 (the omnibox + filterGrammar chunk)
  'src/components/mortgage/LeadTableHideControl.tsx': 3 * KiB, // actual 2.17 (lead-queue-columns)
  'src/components/mortgage/EvidenceDrawerBody.tsx': 18 * KiB, // actual 16.30 (carries ProofPanels.css)
  'src/components/mortgage/ScoreAnatomy.tsx': 20 * KiB, // actual 19.03 (carries ProofPanels.css)
  // post-W5 integration (2026-10-27): the expanded Lead Queue row's body
  // (leadtable-chunk-react-virtual), warmed on idle and on row engagement;
  // 7 files (the chunk, ConfidenceMeter, leadCount, CSS). Measured 12.85-12.93
  // across the merge steps + ~5%, whole KiB.
  'src/components/mortgage/LeadRowExpanded.tsx': 14 * KiB, // actual 12.91
};
