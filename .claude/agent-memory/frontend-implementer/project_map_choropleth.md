---
name: US choropleth map dependency
description: How the Module 0 interactive US map is built, why the drill is state -> ZIP (no county rung), and the onSelectionChange contract for filtering LeadTable.
metadata:
  type: project
---

The interactive US map at `frontend/src/components/mortgage/USChoroplethMap.tsx`
uses `us-atlas` state TopoJSON decoded by `topojson-client`, via the
`loadUsaStateMap()` adapter in `USStateMapData.ts` (lazy-loaded so the
geography lands in its own chunk).

**The drill is state -> ZIP. There is no county level (removed 2026-08-08,
PR #182).** The Cotality share carries exactly one county FIPS per state
(audit C2), so `county_fips_5` is NULL on every `borrower_360` (5,156,184)
and `zip_rollup` (677) row once the fabricated keys were removed. The county
level rendered unfilled polygons whose every hover read "outside the
footprint", and `/api/geo/zip-rollups?county_fips=NNNNN` returned `[]` for
every county. County boundaries cannot be rendered honestly at all.

**Why:** Prototype used stylized polygons and a three-level drill
(`design_files/Module 0 Prototype.html` ~L1802-1812). Real geography was
explicit for the DAIS booth. Dropping the county rung is a documented
design-contract deviation argued at the top of `USChoroplethMap.tsx`.

**How to apply:**

- Viewbox is `192 9 1028 746` (pre-projected Albers USA). State ids are
  **lowercase** (`il`, `ca`, `tx`, …) — keep `USCODE_TO_FIPS` keyed that way.
- `/api/geo/zip-rollups` takes **`state=XX` XOR `county_fips=NNNNN`** (422 on
  both/neither). `api.zipRollups()` takes a `ZipRollupKey` union so "both" is
  a compile error. `liveZipFacts` is keyed by UPPERCASE state code.
  `/api/geo/assignment-overlay?level=zip` follows the same XOR contract.
- **The county path is kept but dead on purpose**, for a future licensed
  county dataset: `/api/geo/county-rollups`, `_ZIP_SQL` /
  `_LEAD_ZIP_SQL` (FIPS-keyed), `buildCountiesPayload` + `countyDisplayName`
  in `USChoroplethMap.utils.ts`, `MapSelection.county` (always null now), and
  `frontend/public/us-counties.json`. **Do not delete the TopoJSON asset** —
  `backend/services/county_names.py` reads it for FIPS->name lookup.
- Per-state counts come from `/api/geo/state-rollups`
  (`mip.gold.funnel_snapshot_daily`). There is no static fallback; states
  with no live row render "—" rather than a fabricated number.
- `StateRollup.zip_unassigned_count` is a **disclosure, not a metric**: how
  many of a state's addressable borrowers the ZIP layer cannot show (no
  usable 5-digit ZIP). Backend derives it as (state total − sum of ZIP
  tiles) off one refresh anchor, so it IS the on-screen gap. Surface it on
  the state hover AND the ZIP drill header when > 0; render nothing at 0.
- Two drill behaviors via `drillBehavior`: `'filter'` fires
  `onSelectionChange({state, county, zip})` for in-place filtering
  (segment-intelligence, and the home map); `'navigate'` deep-links to
  `/lead-queue?state=XX`.
- ZIP-tile clicks deep-link to `/lead-queue?state=XX&zip=NNNNN`. Never add
  `&county=` — it can only ever match nothing.
