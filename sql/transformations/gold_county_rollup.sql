-- =============================================================================
-- gold_county_rollup.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.county_rollup` via CTAS. One row per (fips_5,
--            snapshot_date) -- aggregates the addressable population per
--            county for the USChoroplethMap county-drill tooltips.
--
-- Grain:     (fips_5, snapshot_date).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent single-day
--            rebuild; matches the sibling gold CTAS posture. A future slice
--            that needs multi-day history can swap to MERGE.
-- Slice:     slice13-accuracy-validation.
--
-- Source:    mip.gold.borrower_360 (county_fips_5 column, slice13-accuracy-
--            validation). No ZIP->county crosswalk is needed: silver.property_
--            master already carries fips_county_code; borrower_360 projects
--            it.
--
-- top_segment_code: dominant segment_code BY COUNT per county. Computed with
--            a per-county window rank over exploded segment_codes. Ties are
--            broken lexicographically on segment_code so the output is
--            deterministic across refreshes.
--
-- Honest-null: county_fips_5 can be NULL for ~0.2% of rows (silver lacked a
--            geocode) -- those rows are filtered out here since a NULL-key
--            county rollup row has no meaningful identity.
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_county_rollup.sql.
-- =============================================================================

-- 2026-05-04 (FIX α, round 3): revert the score >= 50 filter introduced
-- in round 2 (FIX F). Aligning the county count to the Lead Queue's
-- score-quality floor broke the alignment with the home-page Marketable
-- Population KPI, which reads borrower_360 unfiltered. The right
-- semantic is "addressable population per county"; the Lead Queue
-- alignment is implemented in LeadRepository.list (FIX β) by querying
-- borrower_360 directly when filtered to a geo.
CREATE OR REPLACE TABLE mip.gold.county_rollup
CLUSTER BY (state, fips_5)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH base AS (
  -- 2026-08-08: state-grain by construction. borrower_360.county_fips_5 is
  -- NULL now (audit C2 — the share has no honest county key), so grouping by
  -- it emptied this table and tripped the footprint resolver's fail-closed
  -- path. The data was always one row per state; group by the real grain.
  SELECT
    b.state,
    b.borrower_id,
    b.opportunity_score,
    b.in_the_money,
    b.segment_codes
  FROM mip.gold.borrower_360 AS b
  WHERE b.state IS NOT NULL
),
aggregates AS (
  SELECT
    state,
    CAST(COUNT(*) AS INT)                                                     AS addressable_borrowers,
    CAST(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS INT)                AS in_the_money_borrowers,
    CAST(SUM(CASE WHEN mip.gold.fn_high_opportunity(opportunity_score) THEN 1 ELSE 0 END) AS INT) AS high_opportunity_borrowers,
    CAST(ROUND(AVG(opportunity_score)) AS INT)                                AS avg_opportunity_score
  FROM base
  GROUP BY state
),
-- Explode segment_codes per CLIP and count per (fips_5, segment_code). Rank
-- by count DESC (segment_code ASC tiebreak) and pick rank 1 per county.
exploded_segments AS (
  SELECT
    b.state,
    sc AS segment_code
  FROM base AS b
  LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
  WHERE sc IS NOT NULL
),
segment_counts AS (
  SELECT
    state,
    segment_code,
    COUNT(*) AS cnt,
    ROW_NUMBER() OVER (
      PARTITION BY state
      ORDER BY COUNT(*) DESC, segment_code ASC
    ) AS rn
  FROM exploded_segments
  GROUP BY state, segment_code
),
top_segment_per_county AS (
  SELECT state, segment_code AS top_segment_code
  FROM segment_counts
  WHERE rn = 1
)
SELECT
  -- 2026-08-07 audit C2: the Cotality share carries exactly ONE distinct
  -- fips_county_code per state (verified against the raw share), so these
  -- aggregates are STATE-grain. Emitting the degenerate FIPS let the map
  -- join a real county polygon name onto a whole-state number ("Cook
  -- County, Illinois — 1,851,040"). Until a real county source exists, no
  -- county key ships: the state column is the honest grain, and consumers
  -- (state footprint resolver, map state layer) read state totals.
  CAST(NULL AS STRING)                               AS fips_5,
  a.state,
  CAST(NULL AS STRING)                               AS county_name,
  a.addressable_borrowers,
  a.in_the_money_borrowers,
  a.high_opportunity_borrowers,
  a.avg_opportunity_score,
  ts.top_segment_code,
  -- snapshot_date stays CURRENT_DATE() (day-grain; across-midnight drift is
  -- an intentional cutoff). snapshot_at shares the run-anchored refresh_at
  -- so every "Refreshed ..." chip rendered from a gold rollup agrees to
  -- the second. See audit-holes-round-3 #7.
  CAST((SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1) AS DATE) AS snapshot_date,
  (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1)              AS snapshot_at
FROM aggregates AS a
LEFT JOIN top_segment_per_county AS ts
  ON ts.state = a.state;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.county_rollup.fips_5 IS '5-char FIPS: 2-char state + 3-char county. PK part.';
COMMENT ON COLUMN mip.gold.county_rollup.state IS '2-char USPS state code (uppercase).';
COMMENT ON COLUMN mip.gold.county_rollup.county_name IS 'Human county name. NULL until a FIPS->name crosswalk seed lands; UI falls back to fips_5.';
COMMENT ON COLUMN mip.gold.county_rollup.addressable_borrowers IS 'Population count for this county on snapshot_date.';
COMMENT ON COLUMN mip.gold.county_rollup.in_the_money_borrowers IS 'COUNT where borrower_360.in_the_money = TRUE.';
COMMENT ON COLUMN mip.gold.county_rollup.high_opportunity_borrowers IS 'COUNT where mip.gold.fn_high_opportunity(borrower_360.opportunity_score) = TRUE (canonical high-opportunity threshold).';
COMMENT ON COLUMN mip.gold.county_rollup.avg_opportunity_score IS 'AVG(borrower_360.opportunity_score) rounded to int.';
COMMENT ON COLUMN mip.gold.county_rollup.top_segment_code IS 'Dominant segment_code by count. NULL when every borrower in the county has empty segment_codes.';
COMMENT ON COLUMN mip.gold.county_rollup.snapshot_date IS 'Refresh date; daily grain. PK part.';
COMMENT ON COLUMN mip.gold.county_rollup.snapshot_at IS 'Precise refresh timestamp.';
