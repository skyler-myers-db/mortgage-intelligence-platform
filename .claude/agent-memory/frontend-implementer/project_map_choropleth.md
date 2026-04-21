---
name: US choropleth map dependency
description: How the Module 0 interactive US map is built and why @svg-maps/usa was chosen over us-atlas/topojson.
type: project
---

The interactive US map at `frontend/src/components/mortgage/MapPlaceholder.tsx`
(also exported as `USChoroplethMap`) uses `@svg-maps/usa` — pre-projected Albers
USA paths bundled as a single default export. Loaded via dynamic `import()` so
its ~140 KB raw / ~53 KB gzipped lands in its own chunk and the initial bundle
stays under the 120 KB gzip budget.

**Why:** The prototype used stylized polygons (not real geography). Upgrading
to real states was explicitly asked for the DAIS booth. `us-atlas` +
`topojson-client` would require runtime projection and ship ~90 KB of TopoJSON
plus a client lib; `@svg-maps/usa` ships flat SVG path strings with no runtime
math. Simpler, lighter at the component boundary, no `d3-geo` dep.

**How to apply:**
- Viewbox is `192 9 1028 746` (pre-projected Albers USA). All cx/cy numerics
  in beacons / overlays on the state level use this coordinate space.
- State ids are **lowercase** (`ga`, `ca`, `tx`, `ca`). Keep the STATE_FACTS
  record keyed that way.
- The `svg-maps__common` type dep is not published; a minimal ambient `declare
  module "@svg-maps/usa"` lives in `frontend/src/vite-env.d.ts`. Don't
  re-import `SvgMap` from `svg-maps__common` — TS will fail.
- County/ZIP drill-downs (Atlanta MSA → 30305/30309/30324/30339) are stylized
  rectangular polygons in their own viewBoxes (340×310, 310×210). They are NOT
  geographic — that's acceptable for the demo path.
- When the borrower dataset expansion slice lands, replace the STATE_FACTS
  const with a derivation from `mocks/demoData.ts`. Marked `TODO:` in-file.
