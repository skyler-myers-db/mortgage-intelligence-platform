-- =============================================================================
-- segment_performance_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Genie-reachable and dashboard-reachable semantic view of
--            `mip_demo.gold.segment_population`. Exposes per-segment counts
--            and averages with the "_ALL" national rollup as first-class
--            data.
--
-- Grain:     Inherits gold.segment_population (one row per (segment_code,
--            state), including '_ALL').
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.6.
--
-- Dimensions:
--   segment_code   — itm / listed / permit / investor / equity / retention.
--   state          — 2-char state or '_ALL' national rollup.
--
-- Measures:
--   count           — member count per cell.
--   avg_score       — average opportunity_score per cell.
--   delta_vs_prior  — QoQ delta string ("+NN%" / "-NN%" / "+0%"); exposed
--                     as a dimension, not a measure, because it is
--                     pre-formatted.
-- =============================================================================

CREATE OR REPLACE VIEW mip_demo.semantics.segment_performance_metric_view AS
SELECT
  sp.segment_code,
  sp.state,
  sp.name,
  sp.count,
  sp.avg_score,
  sp.delta_vs_prior,
  sp.description,
  sp.color,
  sp.refreshed_at
FROM mip_demo.gold.segment_population AS sp;

COMMENT ON VIEW mip_demo.semantics.segment_performance_metric_view IS
  'Genie + dashboard metric view over gold.segment_population. Dimensions: segment_code, state. Measures: count, avg_score. Pre-formatted delta_vs_prior exposed for dashboard chips. See docs/data-contract-module0.md §3.6.';
