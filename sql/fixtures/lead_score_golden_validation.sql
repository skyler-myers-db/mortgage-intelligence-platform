-- =============================================================================
-- lead_score_golden_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip_demo.gold.fn_lead_score(...) produces the same integers as
--           tests/fixtures/lead_score_golden.json (which is also the contract
--           for backend/services/scoring.py).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_lead_score.sql first, then this file. The `diff`
--   column must be 0 on every row and `mismatch` must be empty.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (id, economic_incentive, intent_trigger, fit, relationship, evidence, expected_score) AS (
  VALUES
    ('case_01_all_zeros',                           0,   0,   0,   0,   0,   0),
    ('case_02_all_hundreds',                      100, 100, 100, 100, 100, 100),
    ('case_03_demo_b48291_itm_heavy',              98,  95,  90,  85,  92,  94),
    ('case_04_demo_b48294_heloc_heavy',            85,  92,  85,  80,  85,  87),
    ('case_05_demo_b48295_listed_rounding',        70,  95,  80,  85,  80,  82),
    ('case_06_banker_round_half_to_even_down',     49,  50,  50,  50,  50,  50),
    ('case_07_banker_round_half_to_even_pure_half',50,  50,  60,  50,  50,  52),
    ('case_08_clipping_guard_high',               100, 100, 100, 100, 100, 100),
    ('case_09_null_component_coerced_to_zero',    100, 100, CAST(NULL AS INT), 100, 100,  85),
    ('case_10_all_nulls_return_zero_not_null',    CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS INT), CAST(NULL AS INT), 0),
    ('case_11_mid_itm_realistic',                  78,  62,  70,  55,  65,  68),
    ('case_12_mid_heloc_realistic',                55,  88,  72,  60,  70,  69)
)
SELECT
  id,
  expected_score,
  mip_demo.gold.fn_lead_score(economic_incentive, intent_trigger, fit, relationship, evidence) AS actual_score,
  mip_demo.gold.fn_lead_score(economic_incentive, intent_trigger, fit, relationship, evidence) - expected_score AS diff,
  CASE
    WHEN mip_demo.gold.fn_lead_score(economic_incentive, intent_trigger, fit, relationship, evidence) = expected_score
      THEN ''
    ELSE 'MISMATCH'
  END AS mismatch
FROM golden
ORDER BY id;
