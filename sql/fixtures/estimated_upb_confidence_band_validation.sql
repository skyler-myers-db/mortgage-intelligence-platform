-- =============================================================================
-- estimated_upb_confidence_band_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_estimated_upb_confidence_band(...) produces the same
--           lower/point/upper dollar balances as
--           tests/fixtures/estimated_upb_confidence_band_golden.json.
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_bounded_mortgage_rate.sql,
--   sql/uc_functions/fn_estimated_upb.sql,
--   sql/uc_functions/fn_estimated_upb_confidence_band.sql, then this file.
--   The three diff columns must be 0 on every row and `mismatch` must be empty.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (
  id,
  original_upb,
  estimated_rate,
  months_elapsed,
  expected_lower_upb,
  expected_estimate_upb,
  expected_upper_upb
) AS (
  VALUES
    ('case_01_standard_fixed_rate_band',        360000L, 0.06D,  12, 349658L, 355579L, 359331L),
    ('case_02_missing_rate_includes_linear_point', 360000L, CAST(NULL AS DOUBLE), 120, 240000L, 240000L, 345689L),
    ('case_03_source_high_rate_clamps_to_ceiling', 300000L, 0.8456D, 60, 256033L, 296162L, 296162L),
    ('case_04_source_low_rate_is_missing',      240000L, 0.005D, 180, 120000L, 120000L, 216826L),
    ('case_05_past_term_collapses_to_zero',     200000L, 0.055D, 420, 0L, 0L, 0L),
    ('case_06_null_original_upb_collapses_to_zero', CAST(NULL AS BIGINT), 0.06D, 12, 0L, 0L, 0L)
),
actual AS (
  SELECT
    id,
    expected_lower_upb,
    expected_estimate_upb,
    expected_upper_upb,
    mip.gold.fn_estimated_upb_confidence_band(
      original_upb,
      estimated_rate,
      months_elapsed
    ) AS band
  FROM golden
)
SELECT
  id,
  expected_lower_upb,
  band.lower_upb AS actual_lower_upb,
  band.lower_upb - expected_lower_upb AS lower_diff,
  expected_estimate_upb,
  band.estimate_upb AS actual_estimate_upb,
  band.estimate_upb - expected_estimate_upb AS estimate_diff,
  expected_upper_upb,
  band.upper_upb AS actual_upper_upb,
  band.upper_upb - expected_upper_upb AS upper_diff,
  CASE
    WHEN band.lower_upb = expected_lower_upb
      AND band.estimate_upb = expected_estimate_upb
      AND band.upper_upb = expected_upper_upb
      THEN ''
    ELSE 'MISMATCH'
  END AS mismatch
FROM actual
ORDER BY id;
