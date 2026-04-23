---
name: Footprint context is the source of truth for tenant states
description: FootprintProvider hydrates from /api/config/footprint and drives three UI locations instead of hardcoded 6-state lists.
type: project
---

The tenant footprint (US states the lender writes business in) is hydrated
once at app-shell mount by `frontend/src/components/FootprintProvider.tsx`
from the already-existing `GET /api/config/footprint` endpoint.

**Why:** Summit ships with IL/CA/FL/TX/WA/CO, but the product is sold to
multiple lenders; the list must be data-driven. The backend's
`StateFootprintResolver` reads `mip.ref.state_footprint` and falls back to
the canonical 6-state list on UC outage; `FootprintProvider` mirrors that
fallback verbatim so the three fall back together.

**How to apply:** When adding UI that needs "which states" / "how many
states" — pull from `useFootprint()` (or `useOptionalFootprint()` if the
component may render in tests without a provider), do NOT hardcode a new
list. The three existing consumers are:

- `USChoroplethMap.tsx` — filters a static `USCODE_TO_FIPS` table to build
  the drill-supported county allowlist (FIPS codes are intrinsic, not
  tenant-configurable; the set of active ones is).
- `segment-intelligence.tsx` — `buildLocationToStates` wraps footprint
  rows with a curated `STATE_CODE_TO_METRO_LABEL` map (IL→"Chicago MSA"),
  falling back to `state_name` for uncurated states.
- `portfolio-builder.tsx` — `buildGeoOptions` emits "All N states"
  (label count is live), plus per-state entries keyed by `state_name`.

The US TopoJSON (`public/us-counties.json`) is trimmed to the 6-state
Summit footprint; tenants with a state outside that set will see that
state at the state level but the county drill will show the
"Loading counties…" fallback until the TopoJSON is regenerated to
include their counties. This is a packaging concern, not a FE one.
