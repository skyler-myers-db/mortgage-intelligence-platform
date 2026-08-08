---
name: county-key-removed-drill-dead
description: borrower_360.county_fips_5 is NULL for every row since audit C2 — every county-keyed geo read returns empty, including the map's county→ZIP drill
metadata:
  type: project
---

Audit C2 (landed ~2026-08-07/08) removed the county key as dishonest: the
Cotality share carries exactly one distinct `fips_county_code` per state, so a
county FIPS was really a state total wearing a county polygon's name.
`gold.county_rollup` now emits `fips_5 = NULL` and groups by `state`, and
`gold.borrower_360.county_fips_5` is NULL for all rows.

**Why:** Anything that filters `WHERE county_fips_5 = :fips_5` now matches
nothing. Measured live 2026-08-08: 0 of 5,156,184 borrower_360 rows and 0 of
677 zip_rollup rows carry a county key. `/api/geo/zip-rollups?county_fips=…`
returns `[]` on BOTH its snapshot path (`zip_rollup`) and its filtered path
(`borrower_360`), so the map's county→ZIP drill is dead end-to-end — silently,
because the frontend treats an empty list as "county outside the footprint".

**How to apply:**
- Never diagnose a geo surface by reading the SQL alone; query the live key
  cardinality first (`COUNT_IF(county_fips_5 IS NOT NULL)`).
- The honest re-key is `state` — `zip_rollup` already carries it and has 677
  rows. Do not restore a synthetic county key to make a drill work; C2 removed
  it on purpose.
- Changing the drill contract touches `/api/geo/zip-rollups`' `county_fips`
  query param, `ZipRollupResponse`, and `USChoroplethMap.tsx` — coordinate with
  whoever owns frontend rather than changing it unilaterally.
- Verify before assuming this is still true: the key could be restored if a real
  county source lands.
