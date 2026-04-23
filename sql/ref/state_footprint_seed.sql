-- =============================================================================
-- state_footprint_seed.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Seed `mip.ref.state_footprint` with the canonical list of US
--            states the current tenant (Summit Mortgage) operates in. The
--            footprint is the single source of truth consumed by:
--
--              1. backend `_STATE_SETS` builder in
--                 `backend/services/repositories/databricks_repo.py` (the
--                 "All N states" option and the default-population filter).
--              2. `sql/transformations/gold_borrower_360.sql` (`WHERE
--                 lc.situs_state IN (SELECT state_code FROM state_footprint)`).
--              3. `/api/config/footprint` endpoint (consumed by the frontend
--                 AppShell to hydrate `SUPPORTED_COUNTY_STATES` in
--                 `USChoroplethMap.tsx` and `LOCATION_TO_STATES` in
--                 `routes/segment-intelligence.tsx`).
--
--            Moving the footprint here eliminates the 5-way hardcoded
--            duplication that previously broke silently for any tenant with
--            a different state mix (e.g. NY/NJ/PA).
--
-- Provenance:
--            `source = 'manual_seed'` on every row. These 6 states are the
--            Cotality Delta Share footprint for Summit Mortgage as of
--            slice 9. A tenant with a different footprint overrides these
--            rows by re-running this seed after editing the VALUES list --
--            no Python, TypeScript, or SQL transformation deploy required.
--
-- Idempotency: MERGE on state_code. Re-running is a no-op for unchanged
--            rows and refreshes `last_updated` for any row whose
--            `display_order` / `is_default_state` / `state_name` changed.
--
-- `is_default_state`:
--            Exactly one row should carry TRUE. That row is the default
--            anchor state for empty-filter cases in the UI (e.g. the
--            default "Chicago MSA" dropdown value in portfolio-builder).
-- =============================================================================

MERGE INTO mip.ref.state_footprint AS t
USING (
  SELECT col1 AS state_code, col2 AS state_name, col3 AS display_order, col4 AS is_default_state
  FROM VALUES
    ('IL', 'Illinois',    1, TRUE),   -- default anchor (largest state in footprint)
    ('CA', 'California',  2, FALSE),
    ('FL', 'Florida',     3, FALSE),
    ('TX', 'Texas',       4, FALSE),
    ('WA', 'Washington',  5, FALSE),
    ('CO', 'Colorado',    6, FALSE)
) AS s
ON t.state_code = s.state_code
WHEN MATCHED THEN UPDATE SET
  state_name       = s.state_name,
  display_order    = s.display_order,
  is_default_state = s.is_default_state,
  last_updated     = CURRENT_TIMESTAMP(),
  source           = 'manual_seed'
WHEN NOT MATCHED THEN INSERT (
  state_code, state_name, display_order, is_default_state, last_updated, source
) VALUES (
  s.state_code, s.state_name, s.display_order, s.is_default_state,
  CURRENT_TIMESTAMP(), 'manual_seed'
);
