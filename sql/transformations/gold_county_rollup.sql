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
-- =============================================================================

-- 2026-05-04 fix (FIX F): apply the same opportunity_score >= 50 filter
-- the lead_population transformation uses, so the county-level map
-- tooltip and the Lead Queue (filtered by county) report the same
-- number of marketable borrowers. See gold_zip_rollup.sql header for
-- the original sibling mismatch this aligns with.
CREATE OR REPLACE TABLE mip.gold.county_rollup AS
WITH base AS (
  SELECT
    b.county_fips_5                                AS fips_5,
    b.state,
    b.borrower_id,
    b.opportunity_score,
    b.in_the_money,
    b.segment_codes
  FROM mip.gold.borrower_360 AS b
  WHERE b.county_fips_5 IS NOT NULL
    AND LENGTH(b.county_fips_5) = 5
    AND b.opportunity_score >= 50
),
aggregates AS (
  SELECT
    fips_5,
    ANY_VALUE(state)                                                          AS state,
    CAST(COUNT(*) AS INT)                                                     AS addressable_borrowers,
    CAST(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS INT)                AS in_the_money_borrowers,
    CAST(SUM(CASE WHEN opportunity_score >= 75 THEN 1 ELSE 0 END) AS INT)     AS high_opportunity_borrowers,
    CAST(ROUND(AVG(opportunity_score)) AS INT)                                AS avg_opportunity_score
  FROM base
  GROUP BY fips_5
),
-- Explode segment_codes per CLIP and count per (fips_5, segment_code). Rank
-- by count DESC (segment_code ASC tiebreak) and pick rank 1 per county.
exploded_segments AS (
  SELECT
    b.fips_5,
    sc AS segment_code
  FROM base AS b
  LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
  WHERE sc IS NOT NULL
),
segment_counts AS (
  SELECT
    fips_5,
    segment_code,
    COUNT(*) AS cnt,
    ROW_NUMBER() OVER (
      PARTITION BY fips_5
      ORDER BY COUNT(*) DESC, segment_code ASC
    ) AS rn
  FROM exploded_segments
  GROUP BY fips_5, segment_code
),
top_segment_per_county AS (
  SELECT fips_5, segment_code AS top_segment_code
  FROM segment_counts
  WHERE rn = 1
)
SELECT
  a.fips_5,
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
  ON ts.fips_5 = a.fips_5;
