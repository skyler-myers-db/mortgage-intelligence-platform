-- =============================================================================
-- estimated_upb_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_estimated_upb(...) produces the same dollar balances as
--           tests/fixtures/estimated_upb_golden.json (also the contract for
--           backend/services/scoring.py).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_estimated_upb.sql first, then this file. The `diff`
--   column must be 0 on every row and `mismatch` must be empty.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (id, original_upb, estimated_rate, months_elapsed, expected_upb) AS (
  VALUES
    ('case_01_no_elapsed',                     300000L, 0.06D,  0, 300000L),
    ('case_02_one_year_fixed_rate',            360000L, 0.06D, 12, 355579L),
    ('case_03_five_years_fixed_rate',          300000L, 0.06D, 60, 279163L),
    ('case_04_unknown_rate_linear_fallback',   360000L, CAST(NULL AS DOUBLE), 120, 240000L),
    ('case_05_zero_rate_linear_fallback',      180000L, 0.0D, 180, 90000L),
    ('case_06_near_payoff',                    240000L, 0.04D, 359, 1142L),
    ('case_07_past_term_clamps_to_zero',       200000L, 0.055D, 420, 0L),
    ('case_08_null_original_upb',              CAST(NULL AS BIGINT), 0.06D, 12, 0L),
    ('case_09_null_months_treats_as_new_loan', 250000L, 0.05D, CAST(NULL AS INT), 250000L),
    ('case_10_implausibly_high_rate_linear_fallback', 360000L, 1000000000.0D, 120, 240000L)
)
SELECT
  id,
  expected_upb,
  mip.gold.fn_estimated_upb(original_upb, estimated_rate, months_elapsed) AS actual_upb,
  mip.gold.fn_estimated_upb(original_upb, estimated_rate, months_elapsed) - expected_upb AS diff,
  CASE
    WHEN mip.gold.fn_estimated_upb(original_upb, estimated_rate, months_elapsed) = expected_upb
      THEN ''
    ELSE 'MISMATCH'
  END AS mismatch
FROM golden
ORDER BY id;
