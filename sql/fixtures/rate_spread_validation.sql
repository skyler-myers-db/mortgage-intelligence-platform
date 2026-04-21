-- =============================================================================
-- rate_spread_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_rate_spread(...) produces the same integers as
--           tests/fixtures/rate_spread_golden.json (which is also the
--           contract for backend/services/scoring.py in the next slice).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_rate_spread.sql first, then this file. The `diff`
--   column must be 0 on every row and `mismatch` must be empty.
--
-- Market baseline: the demo's fictional par is 4.875% (0.04875). See the
--   header of fn_rate_spread.sql and the `market_rate_constant` field in
--   tests/fixtures/rate_spread_golden.json for the rationale.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (id, current_rate, market_rate, expected_bps) AS (
  VALUES
    ('case_01_zero_spread',                        0.04875, 0.04875,    0),
    ('case_02_positive_100bps',                    0.05,    0.04,     100),
    ('case_03_negative_100bps_below_market',       0.04,    0.05,    -100),
    ('case_04_large_positive_500bps',              0.10,    0.05,     500),
    ('case_05_banker_round_half_to_even_down',     0.0650,  0.04875,  162),
    ('case_06_null_current_rate',                  CAST(NULL AS DOUBLE), 0.04875, 0),
    ('case_07_null_market_rate',                   0.0575,  CAST(NULL AS DOUBLE), 0),
    ('case_08_null_both',                          CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), 0),
    ('case_09_demo_b48291_rodriguez',              0.0575,  0.04875,   88),
    ('case_10_demo_b48294_park',                   0.0675,  0.04875,  188),
    ('case_11_exact_threshold_75bps',              0.05625, 0.04875,   75)
)
SELECT
  id,
  expected_bps,
  mip.gold.fn_rate_spread(current_rate, market_rate) AS actual_bps,
  mip.gold.fn_rate_spread(current_rate, market_rate) - expected_bps AS diff,
  CASE
    WHEN mip.gold.fn_rate_spread(current_rate, market_rate) = expected_bps
      THEN ''
    ELSE 'MISMATCH'
  END AS mismatch
FROM golden
ORDER BY id;
