-- =============================================================================
-- gold_zip_rollup.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.zip_rollup` via CTAS. One row per
--            (state, county_fips_5, zip, snapshot_date) -- aggregates
--            the addressable population per ZIP/county/state grain
--            and emits a stable-ranked sample_borrower_id so the map's ZIP
--            tiles can deep-link to a real Borrower 360 dossier.
--
-- Grain:     (state, county_fips_5, zip, snapshot_date).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent single-day
--            rebuild; matches the sibling gold CTAS posture.
-- Slice:     slice13-accuracy-validation.
--
-- Source:    mip.gold.borrower_360 (zip + county_fips_5 + segment_codes).
--            Counts the FULL addressable population per ZIP — the same
--            definition used by the home-page Marketable Population KPI.
--
-- 2026-05-04 (FIX α, round 3 — supersedes round 2 FIX F): the round-2
-- attempt aligned ZIP/county/state rollups with the lead_population
-- score >= 50 filter, intending to make the map count match the Lead
-- Queue rows. That created a worse mismatch: the home-page KPI
-- "Marketable population" reads borrower_360 unfiltered (5.16M) while
-- the map state tooltip dropped to 254K. The KPI and map were no longer
-- in the same ballpark, defeating the original alignment intent.
--
-- The right semantic is: "marketable" means the addressable population
-- (every borrower in the eval share). The Lead Queue is a separate
-- "ranked top" view; when narrowed to a state+zip, it should query
-- borrower_360 directly (no score floor) so the queue matches the
-- rollup count for that geo. That alignment is implemented in the
-- LeadRepository (FIX β, same-day commit), not at the rollup layer.
--
-- sample_borrower_id: ROW_NUMBER() OVER (PARTITION BY state, county_fips_5, zip
--            ORDER BY opportunity_score DESC, borrower_id ASC) LIMIT 1 per ZIP
--            grain. Stable
--            across refreshes because the ordering is fully deterministic
--            (opportunity_score is an INT, borrower_id is unique).
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.zip_rollup AS
WITH base AS (
  SELECT
    b.zip,
    b.state,
    b.county_fips_5,
    b.borrower_id,
    b.opportunity_score,
    b.segment_codes
  FROM mip.gold.borrower_360 AS b
  WHERE b.zip IS NOT NULL
    AND LENGTH(b.zip) = 5
),
aggregates AS (
  SELECT
    state,
    county_fips_5,
    zip,
    CAST(COUNT(*) AS INT)                        AS addressable_borrowers,
    CAST(ROUND(AVG(opportunity_score)) AS INT)   AS avg_opportunity_score
  FROM base
  GROUP BY state, county_fips_5, zip
),
exploded_segments AS (
  SELECT
    b.state,
    b.county_fips_5,
    b.zip,
    sc AS segment_code
  FROM base AS b
  LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
  WHERE sc IS NOT NULL
),
segment_counts AS (
  SELECT
    state,
    county_fips_5,
    zip,
    segment_code,
    COUNT(*) AS cnt,
    ROW_NUMBER() OVER (
      PARTITION BY state, county_fips_5, zip
      ORDER BY COUNT(*) DESC, segment_code ASC
    ) AS rn
  FROM exploded_segments
  GROUP BY state, county_fips_5, zip, segment_code
),
top_segment_per_zip AS (
  SELECT state, county_fips_5, zip, segment_code AS top_segment_code
  FROM segment_counts
  WHERE rn = 1
),
-- Stable top-borrower per ZIP. Ordering by opportunity_score DESC surfaces
-- the most interesting dossier; borrower_id ASC tiebreak keeps the sample
-- stable across refreshes when multiple borrowers share the same score.
ranked_borrowers AS (
  SELECT
    state,
    county_fips_5,
    zip,
    borrower_id,
    ROW_NUMBER() OVER (
      PARTITION BY state, county_fips_5, zip
      ORDER BY opportunity_score DESC, borrower_id ASC
    ) AS rn
  FROM base
),
sample_borrower_per_zip AS (
  SELECT state, county_fips_5, zip, borrower_id AS sample_borrower_id
  FROM ranked_borrowers
  WHERE rn = 1
)
SELECT
  a.state,
  a.county_fips_5,
  a.zip,
  a.addressable_borrowers,
  a.avg_opportunity_score,
  ts.top_segment_code,
  sb.sample_borrower_id,
  -- Shared snapshot_at captured once per run. snapshot_date also derived
  -- from the seed so across-midnight refreshes agree with the county
  -- rollup. See audit-holes-round-3 #7.
  CAST((SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1) AS DATE) AS snapshot_date,
  (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1)              AS snapshot_at
FROM aggregates AS a
LEFT JOIN top_segment_per_zip AS ts
  ON ts.state = a.state
 AND COALESCE(ts.county_fips_5, '') = COALESCE(a.county_fips_5, '')
 AND ts.zip = a.zip
LEFT JOIN sample_borrower_per_zip AS sb
  ON sb.state = a.state
 AND COALESCE(sb.county_fips_5, '') = COALESCE(a.county_fips_5, '')
 AND sb.zip = a.zip;
