-- =============================================================================
-- gold_lead_population.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip_demo.gold.lead_population` via CTAS. Ranked top-N
--            cut of gold.borrower_360 (opportunity_score >= 50), with both
--            national rank and within-state rank pre-materialized.
--
-- Grain:     One row per clip (subset of gold.borrower_360).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.5.
--
-- Filtering: WHERE opportunity_score >= 50 AND rank_overall <= 10000.
--            10K is the default population cap (booth-scale: the ranked
--            scroll in the UI never exceeds a few hundred rendered at a
--            time; 10K buys margin for state filtering). Raising the cap
--            here is safe -- the UI is pagination-aware.
--
-- Ranking:
--   rank_overall      = DENSE_RANK() OVER (ORDER BY opportunity_score DESC, clip)
--   rank_within_state = DENSE_RANK() OVER (PARTITION BY state
--                                          ORDER BY opportunity_score DESC, clip)
--   The secondary `, clip` in ORDER BY is a deterministic tiebreaker --
--   otherwise ties within a state would shuffle between refreshes.
--
-- population_version: CONCAT(DATE_FORMAT(refreshed_at, 'yyyyMMdd'), '-v1').
--   When the gold schema bumps, bump '-v1' to '-v2' etc. in one place here.
-- =============================================================================

CREATE OR REPLACE TABLE mip_demo.gold.lead_population AS
WITH ranked AS (
  SELECT
    b.clip,
    b.borrower_id,
    b.display_name,
    b.city,
    b.state,
    b.zip,
    b.segment_codes,
    b.equity_estimate,
    b.rate_spread_bps,
    b.opportunity_score,
    b.confidence,
    b.recommended_offer,
    b.why_now,
    b.evidence_ids,
    b.approval_status,
    DENSE_RANK() OVER (ORDER BY b.opportunity_score DESC, b.clip) AS rank_overall,
    DENSE_RANK() OVER (PARTITION BY b.state
                       ORDER BY b.opportunity_score DESC, b.clip) AS rank_within_state,
    b.refreshed_at
  FROM mip_demo.gold.borrower_360 AS b
  WHERE b.opportunity_score >= 50
)
SELECT
  clip,
  borrower_id,
  display_name,
  city,
  state,
  zip,
  segment_codes,
  equity_estimate,
  rate_spread_bps,
  opportunity_score,
  confidence,
  recommended_offer,
  why_now,
  evidence_ids,
  approval_status,
  rank_overall,
  rank_within_state,
  CONCAT(DATE_FORMAT(refreshed_at, 'yyyyMMdd'), '-v1') AS population_version,
  refreshed_at
FROM ranked
WHERE rank_overall <= 10000;
