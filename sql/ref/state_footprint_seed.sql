-- =============================================================================
-- state_footprint_seed.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Seed `mip.ref.state_footprint` with stable US-state display
--            metadata. This table is NOT data-bearing Cotality coverage. The
--            current coverage scope is discovered from refreshed gold
--            geography rollups; these rows are only labels/default metadata
--            used when coverage rows are present or as a degraded fallback.
--
--              1. `backend/services/state_footprint.py`, which enriches live
--                 county_rollup states with human-readable names.
--              2. `/api/config/footprint`, which tells the frontend the live
--                 coverage list or an explicit metadata-only degraded state.
--
-- Provenance:
--            `source = 'us_state_metadata_seed'` on every row. These rows are
--            stable public geography labels, not a Cotality license scope and
--            not a Summit Mortgage operating footprint.
--
-- Idempotency: MERGE on state_code. Re-running is a no-op for unchanged
--            rows and refreshes `last_updated` for any row whose
--            `display_order` / `is_default_state` / `state_name` changed.
--
-- `is_default_state`:
--            Exactly one row carries TRUE as generic anchor metadata only.
--            User-facing broad defaults use the dynamic "All N states" option
--            built from live coverage, not this seed.
-- =============================================================================

MERGE INTO mip.ref.state_footprint AS t
USING (
  SELECT col1 AS state_code, col2 AS state_name, col3 AS display_order, col4 AS is_default_state
  FROM VALUES
    ('AL', 'Alabama',         1, TRUE),
    ('AK', 'Alaska',          2, FALSE),
    ('AZ', 'Arizona',         3, FALSE),
    ('AR', 'Arkansas',        4, FALSE),
    ('CA', 'California',      5, FALSE),
    ('CO', 'Colorado',        6, FALSE),
    ('CT', 'Connecticut',     7, FALSE),
    ('DE', 'Delaware',        8, FALSE),
    ('FL', 'Florida',         9, FALSE),
    ('GA', 'Georgia',        10, FALSE),
    ('HI', 'Hawaii',         11, FALSE),
    ('ID', 'Idaho',          12, FALSE),
    ('IL', 'Illinois',       13, FALSE),
    ('IN', 'Indiana',        14, FALSE),
    ('IA', 'Iowa',           15, FALSE),
    ('KS', 'Kansas',         16, FALSE),
    ('KY', 'Kentucky',       17, FALSE),
    ('LA', 'Louisiana',      18, FALSE),
    ('ME', 'Maine',          19, FALSE),
    ('MD', 'Maryland',       20, FALSE),
    ('MA', 'Massachusetts',  21, FALSE),
    ('MI', 'Michigan',       22, FALSE),
    ('MN', 'Minnesota',      23, FALSE),
    ('MS', 'Mississippi',    24, FALSE),
    ('MO', 'Missouri',       25, FALSE),
    ('MT', 'Montana',        26, FALSE),
    ('NE', 'Nebraska',       27, FALSE),
    ('NV', 'Nevada',         28, FALSE),
    ('NH', 'New Hampshire',  29, FALSE),
    ('NJ', 'New Jersey',     30, FALSE),
    ('NM', 'New Mexico',     31, FALSE),
    ('NY', 'New York',       32, FALSE),
    ('NC', 'North Carolina', 33, FALSE),
    ('ND', 'North Dakota',   34, FALSE),
    ('OH', 'Ohio',           35, FALSE),
    ('OK', 'Oklahoma',       36, FALSE),
    ('OR', 'Oregon',         37, FALSE),
    ('PA', 'Pennsylvania',   38, FALSE),
    ('RI', 'Rhode Island',   39, FALSE),
    ('SC', 'South Carolina', 40, FALSE),
    ('SD', 'South Dakota',   41, FALSE),
    ('TN', 'Tennessee',      42, FALSE),
    ('TX', 'Texas',          43, FALSE),
    ('UT', 'Utah',           44, FALSE),
    ('VT', 'Vermont',        45, FALSE),
    ('VA', 'Virginia',       46, FALSE),
    ('WA', 'Washington',     47, FALSE),
    ('WV', 'West Virginia',  48, FALSE),
    ('WI', 'Wisconsin',      49, FALSE),
    ('WY', 'Wyoming',        50, FALSE),
    -- 2026-06-11 audit P3: display names for non-state jurisdictions that
    -- appear in public-record property data. Coverage stays data-driven
    -- (this table is labels, not footprint); without these rows a share
    -- refresh containing DC/PR/VI parcels would render bare codes in the
    -- geography drill-down.
    ('DC', 'District of Columbia', 51, FALSE),
    ('PR', 'Puerto Rico',          52, FALSE),
    ('VI', 'U.S. Virgin Islands',  53, FALSE)
) AS s
ON t.state_code = s.state_code
WHEN MATCHED THEN UPDATE SET
  state_name       = s.state_name,
  display_order    = s.display_order,
  is_default_state = s.is_default_state,
  last_updated     = CURRENT_TIMESTAMP(),
  source           = 'us_state_metadata_seed'
WHEN NOT MATCHED THEN INSERT (
  state_code, state_name, display_order, is_default_state, last_updated, source
) VALUES (
  s.state_code, s.state_name, s.display_order, s.is_default_state,
  CURRENT_TIMESTAMP(), 'us_state_metadata_seed'
);
