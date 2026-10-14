/**
 * The cascade-layer map (2026-09-21 audit css-05 / stack-v3 / bundle-06):
 * which layer each colocated stylesheet's own rules belong to. Read by both
 * guards, src/design-system/cssLayers.test.ts (source) and
 * tests/e2e/fixture/css-layers.cross-engine.fixture.spec.ts (served CSSOM).
 *
 * The rule (docs/testing.md "Cascade layers (css-05)"):
 *  - a sheet in the INITIAL stylesheet (reached by static imports from
 *    main.tsx) puts its rules in `components`;
 *  - every lazy colocated sheet puts its rules in `routes`, unless
 *    ROUTES_EXCEPTIONS names it (with the reason routes cannot reach parity);
 *  - inside either, each forced-colors / prefers-contrast /
 *    prefers-reduced-transparency block sits in a `modes` block and each
 *    `@media print` block in a `print` block, split in place.
 * Paths are relative to the frontend package root.
 */

/** The layer order, declared once in design-system/layers.css. */
export const LAYER_MANIFEST = ['reset', 'tokens', 'base', 'components', 'routes', 'modes', 'print'] as const;

export type LayerName = (typeof LAYER_MANIFEST)[number];

/** Colocated sheets in the initial stylesheet, before tokens.css in document order. */
export const INITIAL_COMPONENT_SHEETS: readonly string[] = [
  'src/app.transitions.css',
  'src/components/EvidenceHoverCard.css',
];

/**
 * Sheets another W5e lane edits in the same wave, left unwrapped here. The
 * integrator wraps them at the w5-css-cascade merge (exact table in the lane
 * report: opener after the header comment, `}` last, their forced-colors
 * blocks into `@layer modes` in place, lead-queue.css 78-83 included), then
 * deletes each entry: cssLayers.test.ts fails on a wrapped sheet still listed
 * here, so the set ends empty on the integrated tree. LeadTable.css also
 * keeps its `.tbl-wrap.tbl-wrap--fill` rule in an in-place components block
 * (print.css's :root :is(.tbl-wrap...) max-block-size: none must keep beating
 * it on paper) and joins COMPONENTS_BLOCKS; its two @property registrations
 * stay top level. Until then the runtime guard exempts exactly the selectors
 * these sheets declare.
 */
export const PENDING_INTEGRATOR_WRAP: readonly string[] = [
  'src/routes/lead-queue.css',
  'src/routes/lead-queue.savedViews.css',
  'src/routes/analytics.rate-window.css',
  'src/routes/analytics.scatter.css',
  'src/routes/analytics.sales-ops.css',
  'src/routes/analytics.skeleton.css',
  'src/components/mortgage/LeadTable.css',
  'src/components/mortgage/GenieAnswerCharts.css',
];

/**
 * Lazy sheets whose rules stay in `components`: path -> why routes cannot
 * reach parity. The blocks css-05 step 2 moved out of the initial sheet keep
 * the layer they cascaded in: in routes they would outrank every initial
 * partial by layer and race the lazy sheets that override them by load
 * order.
 */
export const ROUTES_EXCEPTIONS: Readonly<Record<string, string>> = {
  'src/design-system/components/14-genie-markdown-and-proof.css':
    'moved out of the initial sheet (css-05 step 2); it cascaded in components, below every lazy sheet (the inversion scan finds no equal-specificity tie with partials 15-38 that loading later now flips)',
  'src/routes/asset.css':
    'the .asset-* blocks moved out of 05-command-palette.css (css-05 step 2) keep the components layer they cascaded in, against the initial .tbl / .surface partials and any lazy sheet',
  'src/components/mortgage/ProofPanels.css':
    'the .proof-* blocks moved out of 05-command-palette.css (css-05 step 2); EvidenceHowWeGot.css (.evidence-proof__sql) and ScoreSpine.css (.proof-component--focused) override them at equal specificity and must keep winning whatever order the chunks load in',
};

/**
 * Routes sheets that keep one rule (or block) in an in-place `@layer
 * components { }` block: path -> the more specific initial rule it must keep
 * losing to (or the print.css override that must keep beating it). Found by
 * the css-05 inversion scan over every captured state; in routes the rule
 * would outrank them by layer.
 */
export const COMPONENTS_BLOCKS: Readonly<Record<string, string>> = {
  'src/components/mortgage/USChoroplethMap.css':
    '.map-legend.stable-refresh-region (0,2,0) must keep losing to 08-map.css .map-wrap:has(.zip-tiles) .map-legend (static legend in the ZIP drill), as its own comment says',
  'src/components/layout/FollowUpsTray.css':
    ".followups__snooze's display: flex must keep losing to print.css's :root :is(.btn, ...) display: none on paper (the snooze control is a .btn)",
  'src/components/mortgage/DistributionBoard.css':
    ".distribution-board__wrap's overflow-x must keep losing to print.css's :root :is(.tbl-wrap, ...) overflow: visible on paper",
  'src/components/charts/charts.css':
    'the .analytics-chart* chrome moved here from 10-native-analytics.css (css-05 step 2) keeps the components layer it cascaded in, so analytics.scatter.css and the kit rules after it still win',
  'src/routes/glossary.css':
    "the glossary page layout moved here from 05-command-palette.css (css-05 step 2) keeps the components layer it cascaded in, below the route's own rules",
};

/** The layer a colocated sheet's own (non-mode, non-print) rules belong to. */
export function mainLayer(path: string): LayerName {
  return INITIAL_COMPONENT_SHEETS.includes(path) || path in ROUTES_EXCEPTIONS ? 'components' : 'routes';
}
