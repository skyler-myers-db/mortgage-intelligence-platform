-- =============================================================================
-- segment_performance_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Genie-reachable and dashboard-reachable semantic view of
--            `mip.gold.segment_population`. Exposes per-segment counts
--            and averages with the "_ALL" national rollup as first-class
--            data.
--
-- Grain:     Inherits gold.segment_population (one row per (segment_code,
--            state), including '_ALL').
-- Slice:     slice13-accuracy (lifecycle + delta join).
-- Data contract: docs/data-contract-module0.md §3.6 + docs/validation/
--            metric-views.md.
--
-- Dimensions:
--   segment_code   — itm / listed / permit / investor / equity / retention.
--   state          — 2-char state or '_ALL' national rollup.
--
-- Measures:
--   count                 — member count per cell.
--   avg_score             — average opportunity_score per cell.
--   delta_vs_prior        — QoQ delta string ("+NN%" / "-NN%" / "+0%").
--
--   approval_rate         — approved / count * 100 (ROUND 2dp). Derived
--                           from mip.gold.funnel_snapshot_daily for
--                           snapshot_date = CURRENT_DATE(); falls back to
--                           the most recent snapshot when today's refresh
--                           has not yet landed.
--   outreach_rate         — actioned / count * 100 (ROUND 2dp). Same
--                           source + fallback as approval_rate.
--   delta_vs_prior_count        — (count_today - count_prior_week) /
--                                 NULLIF(count_prior_week, 0) * 100, 2dp.
--   delta_vs_prior_approved     — same for approved_borrowers.
--   delta_vs_prior_in_the_money — same for in_the_money_borrowers.
--
-- Data source for rate / delta measures:
--   mip.gold.funnel_snapshot_daily (written by gold_funnel_snapshot_daily.sql
--   on every scoring refresh). Approval + outreach state inside the
--   snapshot come from gold.borrower_lifecycle_state (hourly mirror of
--   Lakebase — see jobs/sync_lifecycle_state.py).
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.segment_performance_metric_view AS
WITH latest_snapshot AS (
  SELECT MAX(snapshot_date) AS snapshot_date
  FROM mip.gold.funnel_snapshot_daily
),
prior_snapshot AS (
  SELECT MAX(snapshot_date) AS snapshot_date
  FROM mip.gold.funnel_snapshot_daily
  WHERE snapshot_date <= (SELECT snapshot_date FROM latest_snapshot) - INTERVAL 7 DAYS
),
today AS (
  SELECT
    f.state,
    f.segment_code,
    f.addressable_borrowers,
    f.in_the_money_borrowers,
    f.approved_borrowers,
    f.actioned_borrowers
  FROM mip.gold.funnel_snapshot_daily f
  WHERE f.snapshot_date = (SELECT snapshot_date FROM latest_snapshot)
),
prior AS (
  SELECT
    f.state,
    f.segment_code,
    f.addressable_borrowers        AS addressable_borrowers_prior,
    f.in_the_money_borrowers       AS in_the_money_borrowers_prior,
    f.approved_borrowers           AS approved_borrowers_prior
  FROM mip.gold.funnel_snapshot_daily f
  WHERE f.snapshot_date = (SELECT snapshot_date FROM prior_snapshot)
)
SELECT
  sp.segment_code,
  sp.state,
  sp.name,
  sp.count,
  sp.avg_score,
  sp.delta_vs_prior,
  sp.description,
  sp.color,
  sp.refreshed_at,
  CAST(
    ROUND(
      100.0 * COALESCE(t.approved_borrowers, 0) / NULLIF(sp.count, 0),
      2
    ) AS DOUBLE
  )                                                                            AS approval_rate,
  CAST(
    ROUND(
      100.0 * COALESCE(t.actioned_borrowers, 0) / NULLIF(sp.count, 0),
      2
    ) AS DOUBLE
  )                                                                            AS outreach_rate,
  CAST(
    ROUND(
      100.0 * (sp.count - COALESCE(p.addressable_borrowers_prior, 0))
        / NULLIF(p.addressable_borrowers_prior, 0),
      2
    ) AS DOUBLE
  )                                                                            AS delta_vs_prior_count,
  CAST(
    ROUND(
      100.0 * (COALESCE(t.approved_borrowers, 0) - COALESCE(p.approved_borrowers_prior, 0))
        / NULLIF(p.approved_borrowers_prior, 0),
      2
    ) AS DOUBLE
  )                                                                            AS delta_vs_prior_approved,
  CAST(
    ROUND(
      100.0 * (COALESCE(t.in_the_money_borrowers, 0) - COALESCE(p.in_the_money_borrowers_prior, 0))
        / NULLIF(p.in_the_money_borrowers_prior, 0),
      2
    ) AS DOUBLE
  )                                                                            AS delta_vs_prior_in_the_money
FROM mip.gold.segment_population AS sp
LEFT JOIN today  AS t ON t.state = sp.state AND t.segment_code = sp.segment_code
LEFT JOIN prior  AS p ON p.state = sp.state AND p.segment_code = sp.segment_code;

COMMENT ON VIEW mip.semantics.segment_performance_metric_view IS
  'Genie + dashboard metric view over gold.segment_population + gold.funnel_snapshot_daily. Dimensions: segment_code, state. Measures: count, avg_score, approval_rate, outreach_rate, delta_vs_prior, delta_vs_prior_count, delta_vs_prior_approved, delta_vs_prior_in_the_money. See docs/data-contract-module0.md §3.6 + docs/validation/metric-views.md.';
