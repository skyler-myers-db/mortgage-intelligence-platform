-- =============================================================================
-- refi_propensity_heuristic_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_refi_propensity_heuristic(...) produces the same
--           scores as tests/fixtures/refi_propensity_heuristic_golden.json
--           (which is also the contract for
--           backend/services/scoring.py::refi_propensity_heuristic).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_refi_propensity_heuristic.sql first, then this file.
--   The `mismatch` column must be empty on every row.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (id, rate_spread_bps, loan_age_months, equity_pct, estimated_upb, listed_for_sale, expected_score) AS (
  VALUES
    ('case_01_all_null',                 CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS BIGINT), CAST(NULL AS BOOLEAN), 10),
    ('case_02_max_score',                150,  48, 40, CAST(320000 AS BIGINT), FALSE, 100),
    ('case_03_spread_100_boundary',      100,   0,  0, CAST(0 AS BIGINT),      TRUE,  40),
    ('case_04_spread_99_falls_to_75_band', 99,  0,  0, CAST(0 AS BIGINT),      TRUE,  32),
    ('case_05_spread_75_boundary',        75,   0,  0, CAST(0 AS BIGINT),      TRUE,  32),
    ('case_06_spread_50_boundary',        50,   0,  0, CAST(0 AS BIGINT),      TRUE,  22),
    ('case_07_spread_25_boundary',        25,   0,  0, CAST(0 AS BIGINT),      TRUE,  10),
    ('case_08_spread_24_scores_zero',     24,   0,  0, CAST(0 AS BIGINT),      TRUE,   0),
    ('case_09_seasoning_prime_band_edges', 0,  24,  0, CAST(0 AS BIGINT),      TRUE,  20),
    ('case_10_seasoning_84_upper_edge',    0,  84,  0, CAST(0 AS BIGINT),      TRUE,  20),
    ('case_11_seasoning_shoulder_bands',   0,  12,  0, CAST(0 AS BIGINT),      TRUE,  10),
    ('case_12_seasoning_85_to_120_shoulder', 0, 120, 0, CAST(0 AS BIGINT),     TRUE,  10),
    ('case_13_seasoning_121_scores_zero',  0, 121,  0, CAST(0 AS BIGINT),      TRUE,   0),
    ('case_14_seasoning_11_scores_zero',   0,  11,  0, CAST(0 AS BIGINT),      TRUE,   0),
    ('case_15_equity_bands',               0,   0, 20, CAST(0 AS BIGINT),      TRUE,  20),
    ('case_16_equity_10_band',             0,   0, 10, CAST(0 AS BIGINT),      TRUE,  10),
    ('case_17_upb_bands',                  0,   0,  0, CAST(150000 AS BIGINT), TRUE,  10),
    ('case_18_upb_shoulder',               0,   0,  0, CAST(149999 AS BIGINT), TRUE,   5),
    ('case_19_upb_below_75k_scores_zero',  0,   0,  0, CAST(74999 AS BIGINT),  TRUE,   0),
    ('case_20_segment_threshold_edge',    75,  30,  0, CAST(0 AS BIGINT),      FALSE, 62),
    ('case_21_listing_blocks_threshold',  75,  30,  0, CAST(0 AS BIGINT),      TRUE,  52)
)
SELECT
  id,
  expected_score,
  mip.gold.fn_refi_propensity_heuristic(rate_spread_bps, loan_age_months, equity_pct, estimated_upb, listed_for_sale) AS actual_score,
  CASE
    WHEN mip.gold.fn_refi_propensity_heuristic(rate_spread_bps, loan_age_months, equity_pct, estimated_upb, listed_for_sale) <=> expected_score
      THEN ''
    ELSE 'MISMATCH'
  END AS mismatch
FROM golden
ORDER BY id;
