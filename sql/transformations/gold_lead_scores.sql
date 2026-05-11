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
-- Sub-score formulas intentionally mirror gold_borrower_360's formulas.
-- Do not add alternate intent, relationship, or fit terms here unless the
-- borrower_360 CTAS is changed in the same patch; drift between the two
-- app-facing scoring surfaces is a data-truth failure.
--
-- evidence sub-score: 10 * live evidence rows plus bounded second-position
-- balance tail, with BLOCKED signal types excluded.
--
-- Threshold convention matches borrower_360.sql: default thresholds are
-- baked here as literals. When admin-config thresholds land (Slice 5),
-- both transformations swap to a CROSS JOIN against mip_app.thresholds
-- simultaneously -- drift is a parity test failure by construction.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.lead_scores AS
WITH evidence_counts AS (
  SELECT clip, COUNT(*) AS evidence_event_count
  FROM mip.gold.evidence_events
  WHERE signal_type NOT IN ('permit', 'listing')
  GROUP BY clip
),
-- Historical tenant-lender relationships per owner_link_id for the
-- relationship sub-score boost (data-contract §5 branch 1).
--
-- BUG FIX (slice13-accuracy): previous implementation counted mortgage-
-- event rows per CLIP, so a CLIP with 3 tenant-lender events (e.g. purchase
-- + refi + release) reported `historical_mortgage_count_at_lender = 3`
-- and triggered the >= 2 branch on a single property. The relationship
-- score branch is meant to reward owners with MULTIPLE DISTINCT PROPERTIES
-- previously financed by the tenant lender, not repeat events on one property.
--
-- Fix: group by owner_link_id and COUNT(DISTINCT clip) among CLIPs that
-- ever had a tenant-lender event. Each borrower row then picks up the
-- owner-level count via property_master. CLIPs lacking an owner_link_id
-- fall through to 0 (LEFT JOIN in `base`), which is correct -- we cannot
-- attribute ownership of multiple properties without the link.
lender_ref AS (
  SELECT
    UPPER(TRIM(raw_key)) AS raw_key,
    is_competitor
  FROM mip.ref.lender_dictionary
),
historical_tenant AS (
  SELECT
    pm.owner_link_id,
    COUNT(DISTINCT me.clip) AS historical_distinct_clips_at_lender
  FROM mip.silver.mortgage_events AS me
  JOIN mip.silver.property_master AS pm ON pm.clip = me.clip
  JOIN lender_ref AS lr ON lr.raw_key = UPPER(TRIM(me.lender_name))
  WHERE me.lender_name IS NOT NULL
    AND COALESCE(NOT lr.is_competitor, FALSE)
    AND me.situs_state IS NOT NULL
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
    b.is_former_customer,
    b.is_competitor_lien,
    b.has_first_party_relationship,
    b.first_party_relationship_depth,
    b.first_party_recent_interactions,
    b.first_party_recent_application,
    b.first_party_synthetic_demo,
    b.is_owner_occupied,
    b.is_corporate_owner,
    b.related_property_count,
    b.first_pos_loan_type,
    b.second_pos_amount,  -- for evidence sub-score (parity with borrower_360 2026-04-22)
    pm.bedrooms,
    pm.bathrooms,
    COALESCE(ec.evidence_event_count,    0) AS evidence_event_count,
    -- owner-level DISTINCT-CLIP count at the tenant lender (post-slice13 semantics).
    COALESCE(ht.historical_distinct_clips_at_lender, 0) AS historical_tenant_distinct_clips,
    b.min_spread_bps_applied,
    b.min_equity_pct_applied
  FROM mip.gold.borrower_360 AS b
  LEFT JOIN mip.silver.property_master AS pm ON pm.clip = b.clip
  LEFT JOIN evidence_counts   AS ec ON ec.clip = b.clip
  LEFT JOIN historical_tenant AS ht ON ht.owner_link_id = b.owner_link_id
),
-- Sub-score formulas: continuous blends (fix/copilot-batch-post-merge
-- 2026-04-22). Tiered CASE statements collapsed 5.16M borrowers into a
-- handful of discrete sub-score buckets and, via fn_lead_score, into only
-- 3 unique opportunity_score values across the top 500. These formulas
-- align with sql/transformations/gold_borrower_360.sql where the same
-- source fields are available.
subscores AS (
  SELECT
    b.*,
    -- economic_incentive: continuous blend of spread + equity that
    -- saturates gently. See gold_borrower_360.sql for rationale; the
    -- formula here must stay aligned with that CTAS.
    CAST(LEAST(100, GREATEST(0,
        LEAST(55, CAST(ROUND(3 * sqrt(GREATEST(0, b.rate_spread_bps))) AS INT))
      + LEAST(50, CAST(ROUND(0.5 * LEAST(100, GREATEST(0, b.equity_pct))) AS INT))
    )) AS INT) AS economic_incentive,
    -- intent_trigger: exact mirror of gold_borrower_360. BLOCKED signals
    -- (permit, listing, avm uplift) stay 0 until the Cotality shares land.
    -- Sqrt on the rate-drift term keeps the top tail separable.
    CAST(LEAST(100, GREATEST(0,
        20 * CASE WHEN b.is_competitor_lien THEN 1 ELSE 0 END
      + LEAST(25, GREATEST(0, (COALESCE(b.related_property_count, 1) - 1) * 10))
      + LEAST(30, CAST(ROUND(2 * sqrt(GREATEST(0, b.rate_spread_bps))) AS INT))
      + LEAST(10, GREATEST(0, CAST(b.equity_pct / 10 AS INT)))
      + CASE WHEN b.is_current_customer THEN 8 ELSE 0 END
    )) AS INT) AS intent_trigger,
    -- fit: exact mirror of gold_borrower_360. Bedrooms/bathrooms come
    -- from the same silver property master row used by borrower_360.
    CAST(LEAST(100, GREATEST(0,
      CASE
        WHEN b.is_owner_occupied AND b.first_pos_loan_type IN ('CONV','FHA','VA') THEN 70
        WHEN b.is_owner_occupied                                                  THEN 60
        WHEN b.is_corporate_owner                                                 THEN 50
        ELSE 40
      END
      + LEAST(20, 4 * COALESCE(b.bedrooms, 0))
      + LEAST(10, 3 * CAST(COALESCE(b.bathrooms, 0) AS INT))
    )) AS INT) AS fit,
    -- relationship: exact mirror of gold_borrower_360. Current/former
    -- customers get an owner-level distinct tenant CLIP bonus; other
    -- borrowers get the related-property count tail.
    CAST(LEAST(100, GREATEST(0,
      CASE
        WHEN b.is_current_customer THEN 70
        WHEN b.is_former_customer  THEN 60
        WHEN b.is_competitor_lien  THEN 55
        WHEN COALESCE(b.related_property_count, 1) > 1 THEN 45
        ELSE 35
      END
      + CASE
          WHEN b.is_current_customer OR b.is_former_customer
            THEN LEAST(25, 5 * LEAST(5, b.historical_tenant_distinct_clips))
          ELSE LEAST(25, GREATEST(0, (COALESCE(b.related_property_count, 1) - 1) * 5))
        END
      + LEAST(12, 3 * COALESCE(b.first_party_relationship_depth, 0))
      + LEAST(8, 4 * COALESCE(b.first_party_recent_interactions, 0))
      + CASE WHEN b.first_party_recent_application THEN 5 ELSE 0 END
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
  s.is_former_customer,
  s.is_competitor_lien,
  s.has_first_party_relationship,
  COALESCE(s.first_party_relationship_depth, 0) AS first_party_relationship_depth,
  COALESCE(s.first_party_recent_interactions, 0) AS first_party_recent_interactions,
  COALESCE(s.first_party_recent_application, FALSE) AS first_party_recent_application,
  COALESCE(s.first_party_synthetic_demo, FALSE) AS first_party_synthetic_demo,
  s.min_spread_bps_applied,
  s.min_equity_pct_applied,
  35 AS heloc_equity_min_applied,
  25 AS cashout_equity_min_applied,
  50 AS retention_min_spread_applied,
  -- Shared refresh_at captured once per run. See audit-holes-round-3 #7.
  (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1) AS refreshed_at
FROM subscores AS s;
