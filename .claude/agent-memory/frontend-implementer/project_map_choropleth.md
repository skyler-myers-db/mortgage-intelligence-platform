---
name: US choropleth map dependency
description: How the Module 0 interactive US map is built, the county TopoJSON footprint it ships, and the onSelectionChange contract for filtering LeadTable.
type: project
---

The interactive US map at `frontend/src/components/mortgage/USChoroplethMap.tsx`
uses `@svg-maps/usa` — pre-projected Albers USA paths bundled as a single
default export. Loaded via dynamic `import()` so its ~140 KB raw / ~53 KB
gzipped lands in its own chunk.

County drill-down uses `frontend/public/us-counties.json` — a TopoJSON trimmed
from `us-atlas@3 counties-albers-10m.json`. As of 2026-04-23 this contains the
full 6-state Delta Share footprint: IL (17), CA (06), FL (12), TX (48), WA
(53), CO (08) — 584 counties, ~497 KB raw / ~186 KB gzipped. Lazy-loaded on
first county drill via dynamic import + fetch of the static asset.

**Why:** Prototype used stylized polygons. Real geography was explicit for the
DAIS booth. `@svg-maps/usa` avoids runtime projection (`us-atlas` would
require `d3-geo`). The TopoJSON county set was trimmed to the footprint
states to keep the asset small while still powering real drill-throughs.

**How to apply:**
- Viewbox is `192 9 1028 746` (pre-projected Albers USA) for state-level
  overlays. County-level viewBox is computed per-state from the TopoJSON
  feature bboxes.
- State ids are **lowercase** (`il`, `ca`, `tx`, `fl`, `wa`, `co`). Keep
  STATE_FACTS + SUPPORTED_COUNTY_STATES keyed that way.
- The `svg-maps__common` type dep is not published; a minimal ambient
  `declare module "@svg-maps/usa"` lives in `frontend/src/vite-env.d.ts`.
- Per-state borrower counts + avg_score come from `/api/geo/state-rollups`
  (backed by `mip.gold.funnel_snapshot_daily`). STATE_FACTS is a silent
  fallback if the API is down — not the primary source.
- **County + ZIP rollups do NOT exist in gold yet.** County/ZIP hover
  tooltips render "—" for count/avgScore rather than fabricating numbers.
  When gold county rollups land, wire `/api/geo/county-rollups` the same way.
- Two drill behaviors via `drillBehavior` prop: `'filter'` fires
  `onSelectionChange({state, county, zip})` for in-place filtering
  (segment-intelligence wires this into `LeadSummary.state` predicates);
  `'navigate'` deep-links to `/lead-queue?state=XX` (home-page teaser).
- The three ZIP tiles in `CHI_ZIPS` (60611/60647/60613) each have a real
  `borrowerId` — clicking drills to `/borrower-360/B-XXX`. Adding more ZIPs
  requires a matching sample borrower or they no-op on click.
