-- =============================================================================
-- gold_lead_scores.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.lead_scores` -- one row per CLIP carrying
--            the five 0..100 component sub-scores, the fn_lead_score blended
--            opportunity_score, and the fn_next_best_offer recommendation.
--            This is where scoring happens; gold.borrower_360 JOINs against
--            this table for its score columns.
--
-- Grain:     One row per CLIP.
-- PK:        clip.
-- Clustering: Liquid cluster on (clip). Z-order on opportunity_score DESC is
--            applied after first demo refresh (out-of-band OPTIMIZE ZORDER BY
--            -- not DDL) so that the "top N" Lead Queue scan is cheap.
--
-- Data contract reference: docs/data-contract-module0.md §3.3.
-- Slice:     module0-real-data-slice3 (gold layer build).
--
-- Sub-score semantics (each 0..100, formula per data-contract §5):
--   economic_incentive : piecewise on rate_spread_bps + equity_pct.
--   intent_trigger     : LEAST(100, 20*recent_refi_90d + 15*recent_payoff_90d
--                        + 25*listed_for_sale + 20*has_permit
--                        + 15*is_competitor_lien + 10*recent_avm_uplift>=10).
--                        listed_for_sale and has_permit are BLOCKED -> 0,
--                        leaving intent driven purely by real events.
--   fit                : owner-occupancy + loan_type + size fit (0..100).
--   relationship       : is_current_customer + historical count at demo
--                        lender.
--   evidence           : LEAST(100, 20 * count_of_evidence_rows_for_clip).
--
-- opportunity_score = mip.gold.fn_lead_score(economic_incentive,
--                     intent_trigger, fit, relationship, evidence).
-- recommended_offer_code = mip.gold.fn_next_best_offer(...).
-- in_the_money = mip.gold.fn_in_the_money(rate_spread_bps, equity_pct,
--                                              min_spread, min_equity).
--
-- Thresholds: Five admin-tunable INTs come from mip_app.thresholds (Lakebase)
-- at refresh time. The demo posture is: thresholds are baked into the refresh
-- and the columns `min_*_applied` record what was used so WhyPanel can
-- reproduce the decision. The UDFs themselves remain threshold-parameterized
-- (frozen signature), so a future feature flag can recompute per-query.
--
-- Threshold defaults (per docs/data-contract §5 + frozen UDF headers):
--   min_spread_bps       = 75
--   min_equity_pct       = 15
--   heloc_equity_min_pct = 35
--   cashout_equity_min   = 25
--   retention_min_spread = 50
--
-- Golden fixture parity: every row's (opportunity_score,
-- recommended_offer_code, in_the_money) tuple is a function of the inputs
-- already defined in gold.borrower_360 for the same CLIP. The SQL-Python
-- parity test in tests/integration/test_sql_python_parity.py loads
-- tests/fixtures/*_golden.json and asserts fn_lead_score / fn_in_the_money /
-- fn_rate_spread / fn_next_best_offer produce byte-identical output in
-- Python and in Databricks SQL. If this table's numbers drift from Python
-- scoring, that test fails loudly.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS; populated via CTAS in the
--            transformation file (CREATE OR REPLACE TABLE ... AS SELECT).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.lead_scores (
  clip                     STRING    NOT NULL COMMENT 'Cotality CLIP. PK. FK to gold.borrower_360.clip.',
  economic_incentive       INT       NOT NULL COMMENT '0..100 sub-score on rate_spread_bps + equity_pct. Weight 0.35 in fn_lead_score.',
  intent_trigger           INT       NOT NULL COMMENT '0..100 sub-score on recent mortgage events + listed/permit (BLOCKED->0) + competitor_lien + recent_avm_uplift. Weight 0.30.',
  fit                      INT       NOT NULL COMMENT '0..100 sub-score on owner-occupancy + loan_type + property size. Weight 0.15.',
  relationship             INT       NOT NULL COMMENT '0..100 sub-score on customer + historical mortgage count at demo lender. Weight 0.10.',
  evidence                 INT       NOT NULL COMMENT '0..100: LEAST(100, 20 * count_of_evidence_rows_for_clip). Weight 0.10.',
  opportunity_score        INT       NOT NULL COMMENT 'mip.gold.fn_lead_score(...) output. 0..100. Frozen UDF signature; parity test locks it to Python.',
  confidence               INT       NOT NULL COMMENT 'ROUND(mean(5 sub-scores)). Mirrors mock_data._build_borrower for screen parity.',
  in_the_money             BOOLEAN   NOT NULL COMMENT 'mip.gold.fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied).',
  recommended_offer_code   STRING    NOT NULL COMMENT 'mip.gold.fn_next_best_offer(...) lowercase code.',
  rate_spread_bps          INT       NOT NULL COMMENT 'Input to fn_in_the_money / fn_next_best_offer. Carried here so the table is self-contained for parity testing.',
  equity_pct               INT       NOT NULL COMMENT 'Input to fn_in_the_money / fn_next_best_offer.',
  has_permit               BOOLEAN   NOT NULL COMMENT 'BLOCKED -> FALSE; carried for parity test transparency.',
  listed_for_sale          BOOLEAN   NOT NULL COMMENT 'BLOCKED -> FALSE; carried for parity test transparency.',
  is_investor              BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_current_customer      BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_competitor_lien       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  min_spread_bps_applied   INT       NOT NULL COMMENT 'Threshold applied this refresh.',
  min_equity_pct_applied   INT       NOT NULL COMMENT 'Threshold applied this refresh.',
  heloc_equity_min_applied INT       NOT NULL COMMENT 'HELOC equity threshold applied this refresh (fn_next_best_offer branch 2/3).',
  cashout_equity_min_applied INT     NOT NULL COMMENT 'Cash-out equity threshold applied this refresh (fn_next_best_offer branch 5).',
  retention_min_spread_applied INT   NOT NULL COMMENT 'Retention spread threshold applied this refresh (fn_next_best_offer branch 7).',
  refreshed_at             TIMESTAMP NOT NULL COMMENT 'Refresh timestamp for audit / provenance.'
)
USING DELTA
CLUSTER BY (clip)
COMMENT 'CLIP-grain scoring surface: 5 component sub-scores, fn_lead_score blend, fn_in_the_money flag, fn_next_best_offer code, and the exact thresholds applied at refresh. SQL-Python parity is locked by tests/integration/test_sql_python_parity.py. See docs/data-contract-module0.md §3.3 + §5.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
