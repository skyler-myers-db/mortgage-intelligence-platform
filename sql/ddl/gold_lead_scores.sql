-- =============================================================================
-- gold_lead_scores.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.lead_scores` -- one row per CLIP carrying
--            the five 0..100 component sub-scores, the fn_lead_score blended
--            opportunity_score, and the fn_next_best_offer recommendation.
--            This is the scoring audit surface; gold.borrower_360 computes
--            the hot-path borrower scores directly from the same primitives.
--
-- Grain:     One row per CLIP.
-- PK:        clip.
-- Clustering: Liquid cluster on (clip). Z-order on opportunity_score DESC is
--            applied after the first refresh (out-of-band OPTIMIZE ZORDER
--            BY -- not DDL) so that the "top N" Lead Queue scan is cheap.
--
-- Data contract reference: docs/data-contract-module0.md §3.3.
-- Slice:     module0-real-data-slice3 (gold layer build).
--
-- Sub-score semantics (each 0..100, formula per data-contract §5):
--   economic_incentive : continuous sqrt/linear blend of rate_spread_bps +
--                        equity_pct.
--   intent_trigger     : competitor-lien, investor, rate-drift, equity,
--                        current-customer, live MLS listing, and Cotality
--                        HELOC/refi propensity terms. Filed permits remain
--                        0 until a true permit source lands.
--   fit                : owner-occupancy + loan_type + corporate/investor fit
--                        using fields carried on borrower_360.
--   relationship       : current/former customer, competitor, investor, owner-
--                        level Summit history, and first-party relationship
--                        depth / recent engagement.
--   evidence           : 10 pts per live evidence row plus bounded
--                        second-position balance tail.
--
-- opportunity_score = mip.gold.fn_lead_score(economic_incentive,
--                     intent_trigger, fit, relationship, evidence).
-- recommended_offer_code = mip.gold.fn_next_best_offer(...).
-- in_the_money = mip.gold.fn_in_the_money(rate_spread_bps, equity_pct,
--                                              min_spread, min_equity).
--
-- Thresholds: Five admin-tunable INTs are represented as explicit columns
-- on the scoring row. The current SQL refresh applies the documented
-- default literals; future admin-config binding must update borrower_360
-- and lead_scores together. The UDFs themselves remain threshold-
-- parameterized (frozen signature), so a future feature flag can recompute
-- per-query.
--
-- Threshold defaults (per docs/data-contract §5 + frozen UDF headers):
--   min_spread_bps       = 75
--   min_equity_pct       = 15
--   heloc_equity_min_pct = 35
--   cashout_equity_min   = 25
--   retention_min_spread = 50
--
-- Golden fixture parity: every row's (opportunity_score, confidence,
-- recommended_offer_code, in_the_money) tuple is a function of the same
-- primitive inputs used by gold.borrower_360 for the same CLIP. The
-- SQL-Python parity test in tests/integration/test_sql_python_parity.py
-- loads tests/fixtures/*_golden.json and asserts fn_lead_score /
-- fn_in_the_money / fn_rate_spread / fn_next_best_offer produce
-- byte-identical output in Python and in Databricks SQL. If this table's
-- numbers drift from borrower_360 or Python scoring, that test fails
-- loudly.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS; populated via CTAS in the
--            transformation file (CREATE OR REPLACE TABLE ... AS SELECT).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.lead_scores (
  clip                     STRING    NOT NULL COMMENT 'Cotality CLIP. PK. FK to gold.borrower_360.clip.',
  economic_incentive       INT       NOT NULL COMMENT '0..100 sub-score on rate_spread_bps + equity_pct. Weight 0.35 in fn_lead_score.',
  intent_trigger           INT       NOT NULL COMMENT '0..100 sub-score on recent mortgage events, competitor/investor signals, rate drift, equity proxy, and current-customer bump. Weight 0.30.',
  fit                      INT       NOT NULL COMMENT '0..100 sub-score on owner-occupancy + loan_type + corporate/investor fit. Weight 0.15.',
  relationship             INT       NOT NULL COMMENT '0..100 sub-score on customer / competitor / investor relationship ladder plus owner-level distinct tenant-lender CLIP history. Weight 0.10.',
  evidence                 INT       NOT NULL COMMENT '0..100: 10 pts per live evidence row plus bounded second-position balance tail. Weight 0.10.',
  opportunity_score        INT       NOT NULL COMMENT 'mip.gold.fn_lead_score(...) output. 0..100. Mirrors gold.borrower_360 for the same CLIP.',
  confidence               INT       NOT NULL COMMENT 'ROUND(mean(5 sub-scores)). Mirrors gold.borrower_360 for the same CLIP.',
  in_the_money             BOOLEAN   NOT NULL COMMENT 'mip.gold.fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied).',
  recommended_offer_code   STRING    NOT NULL COMMENT 'mip.gold.fn_next_best_offer(...) lowercase code.',
  rate_spread_bps          INT       NOT NULL COMMENT 'Input to fn_in_the_money / fn_next_best_offer. Carried here so the table is self-contained for parity testing.',
  equity_pct               INT       NOT NULL COMMENT 'Input to fn_in_the_money / fn_next_best_offer.',
  has_permit               BOOLEAN   NOT NULL COMMENT 'Filed building-permit flag. FALSE until a true Cotality Building Permits source table is present.',
  listed_for_sale          BOOLEAN   NOT NULL COMMENT 'TRUE when borrower_360 has a current active/under-contract Cotality MLS listing row.',
  heloc_propensity_score   INT                COMMENT 'Cotality HELOC propensity score carried from borrower_360. Model signal, not a permit filing.',
  has_heloc_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when heloc_propensity_score >= 700. Used as the HELOC-intent input without setting has_permit.',
  refi_propensity_score    INT                COMMENT 'Cotality refinance propensity score carried from borrower_360.',
  has_refi_propensity_trigger BOOLEAN NOT NULL COMMENT 'TRUE when refi_propensity_score >= 700. Adds intent-trigger weight.',
  is_investor              BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_current_customer      BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  is_former_customer       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360. Distinct from competitor lien; requires historical tenant relationship and no current tenant lien.',
  is_competitor_lien       BOOLEAN   NOT NULL COMMENT 'Carried from borrower_360.',
  has_first_party_relationship BOOLEAN NOT NULL COMMENT 'Carried from borrower_360. TRUE when optional first-party feeds resolve to this borrower.',
  first_party_relationship_depth INT   NOT NULL COMMENT 'Bounded count of resolved first-party feed categories.',
  first_party_recent_interactions INT  NOT NULL COMMENT 'Recent positive interaction count from the first-party engagement feed.',
  first_party_recent_application BOOLEAN NOT NULL COMMENT 'TRUE when a recent first-party LOS/application event exists.',
  first_party_synthetic_demo     BOOLEAN NOT NULL COMMENT 'TRUE only for rows touched by the Summit demo_synthetic first-party seed.',
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
