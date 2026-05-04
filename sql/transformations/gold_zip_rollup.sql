-- =============================================================================
-- gold_zip_rollup.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.zip_rollup` via CTAS. One row per (zip,
--            snapshot_date) -- aggregates the marketable population per ZIP
--            and emits a stable-ranked sample_borrower_id so the map's ZIP
--            tiles can deep-link to a real Borrower 360 dossier.
--
-- Grain:     (zip, snapshot_date).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent single-day
--            rebuild; matches the sibling gold CTAS posture.
-- Slice:     slice13-accuracy-validation.
--
-- Source:    mip.gold.borrower_360 (zip + county_fips_5 + segment_codes),
--            FILTERED to opportunity_score >= 50 to align with
--            mip.gold.lead_population (the Lead Queue's source of truth).
--
-- 2026-05-04 fix (FIX F): the prior `base` CTE included every row in
-- borrower_360 regardless of score. The map tooltip therefore reported
-- `addressable_borrowers = 1` for ZIPs whose only borrower had
-- opportunity_score < 50, while the Lead Queue (which reads from
-- lead_population, filtered to >= 50) showed 0 rows for that ZIP. User
-- spotted the inconsistency on a ZIP=80123 drill-down. The fix here:
-- apply the same `opportunity_score >= 50` filter the lead_population
-- transformation uses, so both surfaces share one definition of
-- "marketable borrower in this ZIP". sample_borrower_id is recomputed
-- against the same filtered base so it stays in lead_population (no
-- dossier deep-links to a borrower the queue hides).
--
-- sample_borrower_id: ROW_NUMBER() OVER (PARTITION BY zip ORDER BY
--            opportunity_score DESC, borrower_id ASC) LIMIT 1 per ZIP. Stable
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
    -- Align with mip.gold.lead_population's quality floor so the map
    -- tooltip and the Lead Queue agree on "marketable in this ZIP".
    -- See header for the original mismatch this fixes.
    AND b.opportunity_score >= 50
),
aggregates AS (
  SELECT
    zip,
    ANY_VALUE(state)                             AS state,
    -- Every row in a ZIP should share the same county FIPS in practice, but
    -- use MAX() so mismatched geocode rows (a few silver edge cases) collapse
    -- deterministically to a single non-null value per ZIP.
    MAX(county_fips_5)                           AS county_fips_5,
    CAST(COUNT(*) AS INT)                        AS addressable_borrowers,
    CAST(ROUND(AVG(opportunity_score)) AS INT)   AS avg_opportunity_score
  FROM base
  GROUP BY zip
),
exploded_segments AS (
  SELECT
    b.zip,
    sc AS segment_code
  FROM base AS b
  LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
  WHERE sc IS NOT NULL
),
segment_counts AS (
  SELECT
    zip,
    segment_code,
    COUNT(*) AS cnt,
    ROW_NUMBER() OVER (
      PARTITION BY zip
      ORDER BY COUNT(*) DESC, segment_code ASC
    ) AS rn
  FROM exploded_segments
  GROUP BY zip, segment_code
),
top_segment_per_zip AS (
  SELECT zip, segment_code AS top_segment_code
  FROM segment_counts
  WHERE rn = 1
),
-- Stable top-borrower per ZIP. Ordering by opportunity_score DESC surfaces
-- the most interesting dossier; borrower_id ASC tiebreak keeps the sample
-- stable across refreshes when multiple borrowers share the same score.
ranked_borrowers AS (
  SELECT
    zip,
    borrower_id,
    ROW_NUMBER() OVER (
      PARTITION BY zip
      ORDER BY opportunity_score DESC, borrower_id ASC
    ) AS rn
  FROM base
),
sample_borrower_per_zip AS (
  SELECT zip, borrower_id AS sample_borrower_id
  FROM ranked_borrowers
  WHERE rn = 1
)
SELECT
  a.zip,
  a.state,
  a.county_fips_5,
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
LEFT JOIN top_segment_per_zip   AS ts ON ts.zip = a.zip
LEFT JOIN sample_borrower_per_zip AS sb ON sb.zip = a.zip;
