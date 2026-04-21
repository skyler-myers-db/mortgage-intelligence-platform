-- =============================================================================
-- next_best_offer_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_next_best_offer(...) produces the same product
--           codes as tests/fixtures/next_best_offer_golden.json (which is
--           also the contract for backend/services/scoring.py in the next
--           slice, and the contract backing OfferRecommendation).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_next_best_offer.sql first, then this file. The
--   `mismatch` column must be empty on every row.
--
-- Default thresholds: min_spread_bps=75, min_equity_pct=15, heloc_equity_min_pct=35,
--   cashout_equity_min=25, retention_min_spread=50. See the header of
--   fn_next_best_offer.sql and the `default_thresholds` field in
--   tests/fixtures/next_best_offer_golden.json. All five thresholds are
--   passed explicitly per-call; NONE are baked into the UDF.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (
  id,
  rate_spread_bps, equity_pct,
  has_permit, listed_for_sale, is_investor, is_current_customer, is_competitor_lien,
  min_spread_bps, min_equity_pct, heloc_equity_min_pct, cashout_equity_min, retention_min_spread,
  expected_offer
) AS (
  VALUES
    ('case_01_listed_purchase_dominates',
        200,  80, TRUE,  TRUE,  TRUE,  TRUE,  TRUE,
        75, 15, 35, 25, 50,
        'purchase'),
    ('case_02_refi_plus_heloc_clean',
        120,  45, FALSE, FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'refi_plus_heloc'),
    ('case_03_heloc_permit_driven',
         30,  45, TRUE,  FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'heloc'),
    ('case_04_refi_only_equity_below_heloc_min',
        100,  20, FALSE, FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'refi'),
    ('case_05_cash_out_no_rate_no_permit',
         20,  30, FALSE, FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'cash_out'),
    ('case_06_investor_below_equity_branches',
          0,  10, FALSE, FALSE, TRUE,  FALSE, FALSE,
        75, 15, 35, 25, 50,
        'investor'),
    ('case_07_retention_competitor_lien',
          0,  10, FALSE, FALSE, FALSE, TRUE,  TRUE,
        75, 15, 35, 25, 50,
        'retention'),
    ('case_08_nurture_customer_no_trigger',
         30,  10, FALSE, FALSE, FALSE, TRUE,  FALSE,
        75, 15, 35, 25, 50,
        'nurture'),
    ('case_09_boundary_heloc_equity_one_below_falls_to_refi',
        100,  34, FALSE, FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'refi'),
    ('case_10_boundary_heloc_equity_exact_fires_cross_sell',
        100,  35, FALSE, FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'refi_plus_heloc'),
    ('case_11_b48291_rodriguez_refi_plus_heloc',
         88,  46, FALSE, FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'refi_plus_heloc'),
    ('case_12_b48294_park_label_shift_to_refi_plus_heloc',
        188,  39, TRUE,  FALSE, FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'refi_plus_heloc'),
    ('case_13_b48295_thompson_purchase',
        162,  56, FALSE, TRUE,  FALSE, FALSE, FALSE,
        75, 15, 35, 25, 50,
        'purchase'),
    ('case_14_all_null_numerics_boolean_nulls_return_nurture',
        CAST(NULL AS INT), CAST(NULL AS INT),
        CAST(NULL AS BOOLEAN), CAST(NULL AS BOOLEAN), CAST(NULL AS BOOLEAN),
        CAST(NULL AS BOOLEAN), CAST(NULL AS BOOLEAN),
        75, 15, 35, 25, 50,
        'nurture'),
    ('case_15_custom_threshold_shifts_branch_refi_plus_heloc_to_cash_out',
        120,  45, FALSE, FALSE, FALSE, FALSE, FALSE,
       150, 15, 35, 25, 50,
        'cash_out')
)
SELECT
  id,
  expected_offer,
  mip.gold.fn_next_best_offer(
    rate_spread_bps, equity_pct,
    has_permit, listed_for_sale, is_investor, is_current_customer, is_competitor_lien,
    min_spread_bps, min_equity_pct, heloc_equity_min_pct, cashout_equity_min, retention_min_spread
  ) AS actual_offer,
  CASE
    WHEN mip.gold.fn_next_best_offer(
      rate_spread_bps, equity_pct,
      has_permit, listed_for_sale, is_investor, is_current_customer, is_competitor_lien,
      min_spread_bps, min_equity_pct, heloc_equity_min_pct, cashout_equity_min, retention_min_spread
    ) = expected_offer
      THEN ''
    ELSE 'MISMATCH'
  END AS mismatch
FROM golden
ORDER BY id;
