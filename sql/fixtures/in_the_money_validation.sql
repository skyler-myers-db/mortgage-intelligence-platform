-- =============================================================================
-- in_the_money_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_in_the_money(...) produces the same booleans as
--           tests/fixtures/in_the_money_golden.json (which is also the
--           contract for backend/services/scoring.py in the next slice).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_in_the_money.sql first, then this file. The
--   `mismatch` column must be empty on every row.
--
-- Default thresholds: min_spread_bps=75, min_equity_pct=15. See the header of
--   fn_in_the_money.sql and the `default_thresholds` field in
--   tests/fixtures/in_the_money_golden.json. Thresholds are passed
--   explicitly per-call; they are not baked into the UDF.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (id, rate_spread_bps, equity_pct, min_spread_bps, min_equity_pct, expected_itm) AS (
  VALUES
    ('case_01_spread_above_equity_above_true',     150,  40,  75, 15, TRUE),
    ('case_02_spread_above_equity_below_false',    150,  10,  75, 15, FALSE),
    ('case_03_spread_below_equity_above_false',     50,  40,  75, 15, FALSE),
    ('case_04_boundary_spread_exact_equity_above_true', 75, 40, 75, 15, TRUE),
    ('case_05_boundary_equity_exact_spread_above_true', 150, 15, 75, 15, TRUE),
    ('case_06_null_spread_false',                  CAST(NULL AS INT), 40,  75, 15, FALSE),
    ('case_07_null_equity_false',                  150, CAST(NULL AS INT), 75, 15, FALSE),
    ('case_08_stricter_threshold_rejects_high_spread', 88, 46, 150, 15, FALSE),
    ('case_09_b48291_rodriguez_itm',           88,  46,  75, 15, TRUE),
    ('case_10_b48294_park_itm',               188,  39,  75, 15, TRUE),
    ('case_11_b48295_thompson_itm',           162,  56,  75, 15, TRUE)
)
SELECT
  id,
  expected_itm,
  mip.gold.fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps, min_equity_pct) AS actual_itm,
  CASE
    WHEN mip.gold.fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps, min_equity_pct) <=> expected_itm
      THEN ''
    ELSE 'MISMATCH'
  END AS mismatch
FROM golden
ORDER BY id;
