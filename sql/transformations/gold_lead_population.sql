-- =============================================================================
-- gold_lead_population.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.lead_population` via CTAS. Ranked
--            quality-filtered cut of gold.borrower_360
--            (opportunity_score >= 50), with both national rank and
--            within-state rank pre-materialized.
--
-- Grain:     One row per clip (subset of gold.borrower_360).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.5.
--
-- Filtering: WHERE opportunity_score >= 50. Every borrower that clears
--            the quality floor lands in lead_population; the UI paginates.
--            A real lender's queue is sized by opportunity quality, not
--            by a UI convenience number, so no arbitrary row cap.
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

CREATE OR REPLACE TABLE mip.gold.lead_population AS
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
    -- equity_pct is carried through from borrower_360 so the executive
    -- dashboard's top-borrowers widget can read percent equity without
    -- joining back. Nightly "Lakeview widgets resolve" test asserts the
    -- column exists on lead_population; adding it here is the canonical
    -- fix to CI failures on 2026-04-22 (UNRESOLVED_COLUMN equity_pct).
    b.equity_pct,
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
  FROM mip.gold.borrower_360 AS b
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
  equity_pct,
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
FROM ranked;
