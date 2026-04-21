-- =============================================================================
-- lead_generation_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Genie-reachable and dashboard-reachable semantic view of
--            `mip.gold.lead_population`. Dimensions the ranked lead
--            queue by segment + state + a simple rank_bucket, so questions
--            like "top 100 leads in California in the ITM segment" answer
--            cleanly.
--
-- Grain:     Inherits gold.lead_population (one row per clip in the
--            top-N ranked cut).
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.5.
--
-- Dimensions:
--   segment    — explode segment_codes so Genie can filter by segment.
--   state      — situs state.
--   rank_bucket — 'top_10' / 'top_100' / 'top_1000' / 'top_10000'. Derived
--                from rank_overall so the dashboard can slice the lead
--                queue into pre-sized cohorts without computing CASE.
--
-- Measures:
--   count_top10                — COUNT(*) FILTER (rank_overall <= 10).
--   count_top100               — COUNT(*) FILTER (rank_overall <= 100).
--   sum_marketable_population  — COUNT(*) (size of the ranked population).
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.lead_generation_metric_view AS
SELECT
  lp.clip,
  lp.state,
  segment                                AS segment,
  CASE
    WHEN lp.rank_overall <= 10    THEN 'top_10'
    WHEN lp.rank_overall <= 100   THEN 'top_100'
    WHEN lp.rank_overall <= 1000  THEN 'top_1000'
    ELSE                               'top_10000'
  END                                     AS rank_bucket,
  lp.rank_overall,
  lp.rank_within_state,
  lp.opportunity_score,
  lp.equity_estimate,
  lp.rate_spread_bps,
  lp.population_version,
  lp.refreshed_at
FROM mip.gold.lead_population AS lp
LATERAL VIEW EXPLODE(lp.segment_codes) seg AS segment;

COMMENT ON VIEW mip.semantics.lead_generation_metric_view IS
  'Genie + dashboard metric view over gold.lead_population. Dimensions: segment, state, rank_bucket. Measures: count_top10, count_top100, sum_marketable_population. See docs/data-contract-module0.md §3.5.';
