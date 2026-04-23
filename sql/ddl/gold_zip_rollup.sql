-- =============================================================================
-- gold_zip_rollup.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.zip_rollup` -- per-ZIP aggregate of the
--            addressable borrower population, carrying county FIPS so the
--            map can filter by the county the user drilled into. Feeds
--            `/api/geo/zip-rollups?fips=<FIPS>` so the USChoroplethMap ZIP
--            tiles render real numbers + a click-through borrower id.
--
-- Grain:     (zip, snapshot_date).
-- PK:        (zip, snapshot_date).
-- Clustering: Liquid cluster on (state, zip). Map read path always filters by
--            state first (the user drilled into a state-then-county), then
--            WHERE county_fips_5 = :fips.
--
-- Source:    `mip.gold.borrower_360` (CLIP-grain) rolled up to ZIP.
--            `sample_borrower_id` is the highest-opportunity-score borrower
--            in the ZIP (stable-ranked with borrower_id tiebreak) so the UI
--            can deep-link to a real borrower dossier; fully deterministic.
--
-- Honest-null posture: `county_fips_5` can be NULL for the ~0.2% of rows
--            where silver lacked a county geocode. `top_segment_code` is
--            NULL when a ZIP has zero segment rows.
--
-- Data contract reference: docs/data-contract-module0.md §3 (new).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.zip_rollup (
  zip                       STRING    NOT NULL COMMENT '5-digit ZIP (STRING preserves leading zeros). PK part.',
  state                     STRING    NOT NULL COMMENT '2-char USPS state code (uppercase).',
  county_fips_5             STRING             COMMENT '5-char county FIPS. Nullable when silver lacked a county geocode.',
  addressable_borrowers     INT       NOT NULL COMMENT 'Population count for this ZIP on snapshot_date.',
  avg_opportunity_score     INT       NOT NULL COMMENT 'AVG(borrower_360.opportunity_score) rounded to int.',
  top_segment_code          STRING             COMMENT 'Dominant segment_code by count. NULL when every borrower in the ZIP has empty segment_codes.',
  sample_borrower_id        STRING             COMMENT 'Stable-ranked top borrower_id in the ZIP (ORDER BY opportunity_score DESC, borrower_id ASC). Used by UI deep-link.',
  snapshot_date             DATE      NOT NULL COMMENT 'Refresh date; daily grain. PK part.',
  snapshot_at               TIMESTAMP NOT NULL COMMENT 'Precise refresh timestamp.'
)
USING DELTA
CLUSTER BY (state, zip)
COMMENT 'Per-ZIP aggregate derived from mip.gold.borrower_360. Feeds /api/geo/zip-rollups for the USChoroplethMap ZIP drill.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
