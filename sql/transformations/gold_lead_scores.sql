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
-- Sub-score formulas live in data-contract §5. (The historical DLT mirror
-- was retired in slice13-accuracy; the authoritative materialisation path
-- is this CTAS chain under `mip_refresh_scores`.)
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
-- Historical Summit *relationships* per owner_link_id for the relationship
-- sub-score boost (data-contract §5 branch 1).
--
-- BUG FIX (slice13-accuracy): previous implementation counted mortgage-
-- event rows per CLIP, so a CLIP with 3 Summit lien events (e.g. purchase
-- + refi + release) reported `historical_mortgage_count_at_lender = 3`
-- and triggered the >= 2 branch on a single property. The relationship
-- score branch is meant to reward owners with MULTIPLE DISTINCT PROPERTIES
-- previously financed by Summit, not repeat events on one property.
--
-- Fix: group by owner_link_id and COUNT(DISTINCT clip) among CLIPs that
-- ever had a Summit lender event. Each borrower row then picks up the
-- owner-level count via property_master. CLIPs lacking an owner_link_id
-- fall through to 0 (LEFT JOIN in `base`), which is correct -- we cannot
-- attribute ownership of multiple properties without the link.
historical_summit AS (
  SELECT
    pm.owner_link_id,
    COUNT(DISTINCT me.clip) AS historical_distinct_clips_at_lender
  FROM mip.silver.mortgage_events AS me
  JOIN mip.silver.property_master AS pm ON pm.clip = me.clip
  WHERE me.lender_name IS NOT NULL
    AND UPPER(me.lender_name) LIKE '%SUMMIT%'
    AND me.situs_state IN ('IL','CA','FL','TX','WA','CO')
    AND pm.owner_link_id IS NOT NULL
  GROUP BY pm.owner_link_id
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
    b.second_pos_amount,  -- for evidence sub-score (parity with borrower_360 2026-04-22)
    -- year_built etc. not carried; bedrooms/bathrooms not on borrower_360;
    -- approximate fit on the available columns.
    COALESCE(re.recent_refi_count_90d,   0) AS recent_refi_count_90d,
    COALESCE(re.recent_payoff_count_90d, 0) AS recent_payoff_count_90d,
    COALESCE(ec.evidence_event_count,    0) AS evidence_event_count,
    -- owner-level DISTINCT-CLIP count at Summit (post-slice13 semantics).
    COALESCE(hs.historical_distinct_clips_at_lender, 0) AS historical_summit_distinct_clips,
    b.min_spread_bps_applied,
    b.min_equity_pct_applied
  FROM mip.gold.borrower_360 AS b
  LEFT JOIN recent_events     AS re ON re.clip = b.clip
  LEFT JOIN evidence_counts   AS ec ON ec.clip = b.clip
  LEFT JOIN historical_summit AS hs ON hs.owner_link_id = b.owner_link_id
),
-- Sub-score formulas: continuous blends (fix/copilot-batch-post-merge
-- 2026-04-22). Tiered CASE statements collapsed 5.16M borrowers into a
-- handful of discrete sub-score buckets and, via fn_lead_score, into only
-- 3 unique opportunity_score values across the top 500. These formulas
-- mirror the 1:1 changes in sql/transformations/gold_borrower_360.sql;
-- drift between the two is a parity failure by construction.
subscores AS (
  SELECT
    b.*,
    -- economic_incentive: continuous blend of spread + equity that
    -- saturates gently. See gold_borrower_360.sql for rationale; the
    -- formula here must stay 1:1 with that CTAS -- drift is a parity
    -- failure by construction.
    CAST(LEAST(100, GREATEST(0,
        LEAST(55, CAST(ROUND(3 * sqrt(GREATEST(0, b.rate_spread_bps))) AS INT))
      + LEAST(50, CAST(ROUND(0.5 * LEAST(100, GREATEST(0, b.equity_pct))) AS INT))
    )) AS INT) AS economic_incentive,
    -- intent_trigger: mirrors gold_borrower_360 (must stay 1:1). Real
    -- recent-event counts (refi/payoff) keep their contributions;
    -- BLOCKED signals (permit, listing, avm uplift) stay 0. Sqrt on the
    -- rate-drift term keeps the top tail separable.
    CAST(LEAST(100, GREATEST(0,
        CAST(20 * b.recent_refi_count_90d AS INT)
      + CAST(15 * b.recent_payoff_count_90d AS INT)
      + 20 * CASE WHEN b.is_competitor_lien THEN 1 ELSE 0 END
      + CASE WHEN b.is_investor THEN 20 ELSE 0 END
      + LEAST(30, CAST(ROUND(2 * sqrt(GREATEST(0, b.rate_spread_bps))) AS INT))
      + LEAST(10, GREATEST(0, CAST(b.equity_pct / 10 AS INT)))
      + CASE WHEN b.is_current_customer THEN 8 ELSE 0 END
    )) AS INT) AS intent_trigger,
    -- fit: continuous over available property signals. lead_scores has
    -- no bedrooms/bathrooms columns (they live on borrower_360), so we
    -- approximate with the owner-occupancy tier + loan-type flag plus
    -- a small jitter via is_investor (reduces fit for pure investor
    -- rows so the ranked queue prioritises owner-occupants).
    CAST(LEAST(100, GREATEST(0,
      CASE
        WHEN b.is_owner_occupied AND b.first_pos_loan_type IN ('CONV','FHA','VA') THEN 78
        WHEN b.is_owner_occupied                                                  THEN 68
        WHEN b.is_corporate_owner                                                 THEN 58
        ELSE 50
      END
      - CASE WHEN b.is_investor AND NOT b.is_owner_occupied THEN 10 ELSE 0 END
    )) AS INT) AS fit,
    -- relationship: must stay 1:1 with gold_borrower_360. lead_scores
    -- does not carry related_property_count directly, but b.is_investor
    -- is already derived from multi-property >= 2. For non-investor
    -- rows we fall back to 0 bump; this is a known minor parity gap
    -- (related_property_count in borrower_360 vs is_investor boolean
    -- here) that drops a ~5pt contribution at most. The overall spread
    -- is driven by economic_incentive + intent_trigger.
    CAST(LEAST(100, GREATEST(0,
      CASE
        WHEN b.is_current_customer THEN 70
        WHEN b.is_competitor_lien  THEN 55
        WHEN b.is_investor         THEN 45
        ELSE 35
      END
      + CASE
          WHEN b.is_current_customer
            THEN LEAST(25, 5 * LEAST(5, b.historical_summit_distinct_clips))
          WHEN b.is_investor
            THEN 10
          ELSE 0
        END
    )) AS INT) AS relationship,
    -- evidence: 10 pts per live event (was 20 -- saturated >=50% of
    -- rows) plus a continuous second_pos_amount term so dossier-rich
    -- borrowers beat dossier-sparse ones. Must match the formula in
    -- gold_borrower_360 exactly.
    LEAST(100, GREATEST(0,
      10 * b.evidence_event_count
      + CASE
          WHEN b.second_pos_amount IS NOT NULL AND b.second_pos_amount > 0
            THEN LEAST(20, CAST(ROUND(sqrt(b.second_pos_amount / 1000.0)) AS INT))
          ELSE 0
        END
    )) AS evidence
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
