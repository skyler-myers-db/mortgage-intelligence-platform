-- =============================================================================
-- borrower_opportunity_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Genie-reachable and dashboard-reachable semantic view of
--            `mip.gold.borrower_360`. Exposes the borrower opportunity
--            surface with typed dimensions and measures so Genie can answer
--            questions like "how many ITM borrowers in Texas" without
--            inventing SQL against the raw gold table.
--
-- Grain:     Inherits gold.borrower_360 (one row per CLIP). Aggregations are
--            defined as MEASURES on this view.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.2. Metric views are
--            consumed by `backend/services/genie_client.py` and the
--            Executive / Segment dashboards.
--
-- Dimensions:
--   state              — situs state (6-state footprint + _ALL rollup)
--   segment            — SegmentCode Literal; exploded from segment_codes
--                        so Genie can filter by a single segment.
--   loan_purpose       — first_pos_loan_type (proxy for loan_purpose; CONV/
--                        FHA/VA etc. from share).
--   is_investor        — boolean.
--   is_current_customer — boolean.
--
-- Measures:
--   avg_rate_spread_bps        — AVG(rate_spread_bps).
--   avg_equity_pct             — AVG(equity_pct).
--   count_itm                  — COUNT(*) FILTER (in_the_money = TRUE).
--   sum_loan_amount            — SUM(current_lien_balance).
--   count_total                — COUNT(*).
--   avg_opportunity_score      — AVG(opportunity_score).
--
-- Non-negotiables:
--   * The view never exposes owner_name_hash, trigger_timeline_json, or any
--     internal-only column. Genie + dashboards see the UI-safe surface.
--   * `segment` dimension uses LATERAL VIEW EXPLODE, so one borrower can
--     appear in multiple segment rows -- callers that want unique borrower
--     counts should dimension by `state` and NOT by `segment`, or use a
--     COUNT DISTINCT on clip.
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.borrower_opportunity_metric_view AS
SELECT
  b.clip,
  b.state,
  segment                                           AS segment,
  b.first_pos_loan_type                             AS loan_purpose,
  b.is_investor,
  b.is_current_customer,
  b.rate_spread_bps,
  b.equity_pct,
  b.in_the_money,
  b.current_lien_balance,
  b.opportunity_score
FROM mip.gold.borrower_360 AS b
LATERAL VIEW EXPLODE(b.segment_codes) seg AS segment;

COMMENT ON VIEW mip.semantics.borrower_opportunity_metric_view IS
  'Genie + dashboard metric view over gold.borrower_360. Dimensions: state, segment, loan_purpose, is_investor, is_current_customer. Measures: avg_rate_spread_bps, avg_equity_pct, count_itm, sum_loan_amount, count_total, avg_opportunity_score. See docs/data-contract-module0.md §3.2.';
