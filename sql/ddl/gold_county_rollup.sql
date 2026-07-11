-- =============================================================================
-- gold_county_rollup.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.county_rollup` -- per-county aggregate of the
--            addressable borrower population, keyed on 5-char FIPS + snapshot
--            date. Feeds `/api/geo/county-rollups` so the USChoroplethMap can
--            render real hover numbers for counties in the refreshed source
--            footprint, not just the handful of
--            discovered counties the component shipped with in Slice 9.
--
-- Grain:     (fips_5, snapshot_date). One row per county per refresh day.
-- PK:        (fips_5, snapshot_date).
-- Clustering: Liquid cluster on (state, fips_5) -- the API always filters by
--            state first (the map only fetches the counties for the state
--            the user drilled into), then joins by FIPS.
--
-- Source:    `mip.gold.borrower_360` projected county_fips_5 column
--            (slice13-accuracy-validation). No ZIP->county crosswalk required
--            because silver.property_master already carries fips_county_code
--            straight from the Cotality share; `borrower_360` promotes it.
--
-- Honest-null posture: `top_segment_code` is NULL when a county has zero
--            segment rows (every borrower in the county came back with an
--            empty segment_codes array). `county_name` is NULL until a
--            public-domain FIPS -> name lookup seed lands; frontend falls back
--            to the FIPS string.
--
-- Data contract reference: docs/data-contract-module0.md §3 (new).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.county_rollup (
  fips_5                       STRING    NOT NULL COMMENT '5-char FIPS: 2-char state + 3-char county. PK part.',
  state                        STRING    NOT NULL COMMENT '2-char USPS state code (uppercase).',
  county_name                  STRING             COMMENT 'Human county name. NULL until a FIPS->name crosswalk seed lands; UI falls back to fips_5.',
  addressable_borrowers        INT       NOT NULL COMMENT 'Population count for this county on snapshot_date.',
  in_the_money_borrowers       INT       NOT NULL COMMENT 'COUNT where borrower_360.in_the_money = TRUE.',
  high_opportunity_borrowers   INT       NOT NULL COMMENT 'COUNT where mip.gold.fn_high_opportunity(borrower_360.opportunity_score) = TRUE (canonical high-opportunity threshold).',
  avg_opportunity_score        INT       NOT NULL COMMENT 'AVG(borrower_360.opportunity_score) rounded to int.',
  top_segment_code             STRING             COMMENT 'Dominant segment_code by count. NULL when every borrower in the county has empty segment_codes.',
  snapshot_date                DATE      NOT NULL COMMENT 'Refresh date; daily grain. PK part.',
  snapshot_at                  TIMESTAMP NOT NULL COMMENT 'Precise refresh timestamp.'
)
USING DELTA
CLUSTER BY (state, fips_5)
COMMENT 'Per-county aggregate keyed on 5-char FIPS, derived from mip.gold.borrower_360.county_fips_5. Feeds /api/geo/county-rollups for the USChoroplethMap.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
