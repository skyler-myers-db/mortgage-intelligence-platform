-- =============================================================================
-- gold_lead_scores.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.lead_scores` via CTAS. One row per CLIP
--            carrying the five 0..100 component sub-scores, fn_lead_score
--            opportunity_score, fn_in_the_money flag, fn_next_best_offer
--            code, and the exact thresholds applied at this refresh.
--
-- Grain:     One row per clip.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild is the
--            default refresh posture; upstream gold.borrower_360 +
--            gold.evidence_events are both already materialized.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.3 + §5.
--
-- Sub-score formulas live in data-contract §5 and in the SQL header of each
-- @dlt.table equivalent in mip_gold_pipeline.py.
--
-- intent_trigger formula on the real-data path (BLOCKED terms = 0):
--   LEAST(100,
--       20 * recent_refi_count_90d
--     + 15 * recent_payoff_count_90d
--     +  0 * listed_for_sale                          -- BLOCKED
--     +  0 * has_permit                               -- BLOCKED
--     + 15 * is_competitor_lien
--     + 10 * (recent_avm_uplift_flag)                 -- approximated FALSE on real
--                                                     -- data (no AVM history yet).
--   )
-- 90-day windows are deliberately tight: longer windows pick up more
-- events but dilute "recency." We use 90d to match fixture behavior;
-- tenants wanting a wider net can widen to 180d here.
--
-- economic_incentive, fit, relationship formulas: identical to those in
-- gold_borrower_360's CTAS subscores CTE. They are recomputed here to keep
-- gold.lead_scores self-contained (so the parity test can verify them
-- independently of borrower_360).
--
-- evidence sub-score: LEAST(100, 20 * evidence_row_count_for_clip) with
-- BLOCKED signal types excluded.
--
-- Threshold convention matches borrower_360.sql: default thresholds are
-- baked here as literals. When admin-config thresholds land (Slice 5),
-- both transformations swap to a CROSS JOIN against mip_app.thresholds
-- simultaneously -- drift is a parity test failure by construction.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.lead_scores AS
WITH market AS (
  SELECT rate_fraction AS market_rate_fraction
  FROM mip.silver.market_rates_weekly
  WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE
  LIMIT 1
),
-- Recent 90d event counts per CLIP for intent_trigger. Real data:
-- listed_for_sale and has_permit are BLOCKED, so their terms drop out.
recent_events AS (
  SELECT
    clip,
    SUM(CASE WHEN is_refinance AND event_date >= DATE_SUB(CURRENT_DATE(), 90) THEN 1 ELSE 0 END) AS recent_refi_count_90d,
    SUM(CASE WHEN release_date IS NOT NULL AND release_date >= DATE_SUB(CURRENT_DATE(), 90) THEN 1 ELSE 0 END) AS recent_payoff_count_90d
  FROM mip.silver.mortgage_events
  WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
  GROUP BY clip
),
evidence_counts AS (
  SELECT clip, COUNT(*) AS evidence_event_count
  FROM mip.gold.evidence_events
  WHERE signal_type NOT IN ('permit', 'listing')
  GROUP BY clip
),
-- Historical mortgage count at Summit for the relationship sub-score boost
-- (data-contract §5 branch 1).
historical_summit AS (
  SELECT
    clip,
    COUNT(*) AS historical_mortgage_count_at_lender
  FROM mip.silver.mortgage_events
  WHERE lender_name IS NOT NULL
    AND UPPER(lender_name) LIKE '%SUMMIT%'
    AND situs_state IN ('IL','CA','FL','TX','WA','CO')
  GROUP BY clip
),
base AS (
  SELECT
    b.clip,
    b.rate_spread_bps,
    b.equity_pct,
    b.has_permit,
    b.listed_for_sale,
    b.is_investor,
    b.is_current_customer,
    b.is_competitor_lien,
    b.is_owner_occupied,
    b.is_corporate_owner,
    b.first_pos_loan_type,
    -- year_built etc. not carried; bedrooms/bathrooms not on borrower_360;
    -- approximate fit on the available columns.
    COALESCE(re.recent_refi_count_90d,   0) AS recent_refi_count_90d,
    COALESCE(re.recent_payoff_count_90d, 0) AS recent_payoff_count_90d,
    COALESCE(ec.evidence_event_count,    0) AS evidence_event_count,
    COALESCE(hs.historical_mortgage_count_at_lender, 0) AS historical_summit_count,
    b.min_spread_bps_applied,
    b.min_equity_pct_applied
  FROM mip.gold.borrower_360 AS b
  LEFT JOIN recent_events     AS re ON re.clip = b.clip
  LEFT JOIN evidence_counts   AS ec ON ec.clip = b.clip
  LEFT JOIN historical_summit AS hs ON hs.clip = b.clip
),
subscores AS (
  SELECT
    b.*,
    -- economic_incentive (data-contract §5):
    CASE
      WHEN b.rate_spread_bps >= 200 AND b.equity_pct >= 35 THEN 98
      WHEN b.rate_spread_bps >= 150 AND b.equity_pct >= 35 THEN 92
      WHEN b.rate_spread_bps >= 100 AND b.equity_pct >= 25 THEN 85
      WHEN b.rate_spread_bps >= 75  AND b.equity_pct >= 15 THEN 75
      WHEN b.rate_spread_bps >= 0   AND b.equity_pct >= 25 THEN 55
      WHEN b.equity_pct >= 25                              THEN 48
      ELSE 30
    END AS economic_incentive,
    -- intent_trigger (BLOCKED terms = 0 on real data):
    LEAST(100,
      CAST(20 * b.recent_refi_count_90d
         + 15 * b.recent_payoff_count_90d
         + 15 * (CASE WHEN b.is_competitor_lien THEN 1 ELSE 0 END)
         AS INT)
    ) AS intent_trigger,
    -- fit (data-contract §5, approximated without bedrooms/bathrooms):
    CASE
      WHEN b.is_owner_occupied
        AND b.first_pos_loan_type IN ('CONV','FHA','VA') THEN 82
      WHEN b.is_owner_occupied                           THEN 75
      WHEN b.is_corporate_owner                          THEN 65
      ELSE 58
    END AS fit,
    -- relationship (data-contract §5):
    CASE
      WHEN b.is_current_customer
        AND b.historical_summit_count >= 2              THEN 95
      WHEN b.is_current_customer                        THEN 88
      WHEN b.is_competitor_lien                         THEN 60
      ELSE 45
    END AS relationship,
    -- evidence (data-contract §5):
    LEAST(100, 20 * b.evidence_event_count) AS evidence
  FROM base AS b
)
SELECT
  s.clip,
  s.economic_incentive,
  s.intent_trigger,
  s.fit,
  s.relationship,
  s.evidence,
  mip.gold.fn_lead_score(
    s.economic_incentive, s.intent_trigger, s.fit, s.relationship, s.evidence
  ) AS opportunity_score,
  CAST(ROUND(
    (s.economic_incentive + s.intent_trigger + s.fit + s.relationship + s.evidence) / 5.0
  ) AS INT) AS confidence,
  mip.gold.fn_in_the_money(
    s.rate_spread_bps, s.equity_pct, s.min_spread_bps_applied, s.min_equity_pct_applied
  ) AS in_the_money,
  mip.gold.fn_next_best_offer(
    s.rate_spread_bps,
    s.equity_pct,
    s.has_permit,
    s.listed_for_sale,
    s.is_investor,
    s.is_current_customer,
    s.is_competitor_lien,
    s.min_spread_bps_applied,
    s.min_equity_pct_applied,
    35, 25, 50
  ) AS recommended_offer_code,
  s.rate_spread_bps,
  s.equity_pct,
  s.has_permit,
  s.listed_for_sale,
  s.is_investor,
  s.is_current_customer,
  s.is_competitor_lien,
  s.min_spread_bps_applied,
  s.min_equity_pct_applied,
  35 AS heloc_equity_min_applied,
  25 AS cashout_equity_min_applied,
  50 AS retention_min_spread_applied,
  CURRENT_TIMESTAMP() AS refreshed_at
FROM subscores AS s;
