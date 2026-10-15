/**
 * The cascade-layer map (2026-09-21 audit css-05 / stack-v3 / bundle-06):
 * which layer each colocated stylesheet's own rules belong to. The data
 * lives in cssLayerMap.json so both guards read one copy:
 * src/design-system/cssLayers.test.ts (source, through this module) and
 * tests/e2e/fixture/css-layers.cross-engine.fixture.spec.ts (served CSSOM,
 * which reads the JSON with node:fs: fixture files may only `import type`
 * from frontend/src).
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
import map from './cssLayerMap.json';

export type LayerName = 'reset' | 'tokens' | 'base' | 'components' | 'routes' | 'modes' | 'print';

/** The layer order, declared once in design-system/layers.css. */
export const LAYER_MANIFEST: readonly LayerName[] = map.manifest as LayerName[];

/** Colocated sheets in the initial stylesheet, before tokens.css in document order. */
export const INITIAL_COMPONENT_SHEETS: readonly string[] = map.initialComponentSheets;

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
export const PENDING_INTEGRATOR_WRAP: readonly string[] = map.pendingIntegratorWrap;

/**
 * Lazy sheets whose rules stay in `components`: path -> why routes cannot
 * reach parity. The blocks css-05 step 2 moved out of the initial sheet keep
 * the layer they cascaded in: in routes they would outrank every initial
 * partial by layer and race the lazy sheets that override them by load
 * order.
 */
export const ROUTES_EXCEPTIONS: Readonly<Record<string, string>> = map.routesExceptions;

/**
 * Routes sheets that keep one rule (or block) in an in-place `@layer
 * components { }` block: path -> the more specific initial rule it must keep
 * losing to (or the print.css override that must keep beating it). Found by
 * the css-05 inversion scan over every captured state; in routes the rule
 * would outrank them by layer.
 */
export const COMPONENTS_BLOCKS: Readonly<Record<string, string>> = map.componentsBlocks;

/** The layer a colocated sheet's own (non-mode, non-print) rules belong to. */
export function mainLayer(path: string): LayerName {
  return INITIAL_COMPONENT_SHEETS.includes(path) || path in ROUTES_EXCEPTIONS ? 'components' : 'routes';
}
