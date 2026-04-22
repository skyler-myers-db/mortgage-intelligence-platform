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
-- Slice:     slice13-accuracy (lifecycle + delta join).
-- Data contract: docs/data-contract-module0.md §3.5 + docs/validation/
--            metric-views.md.
--
-- Dimensions:
--   segment    — explode segment_codes so Genie can filter by segment.
--   state      — situs state.
--   rank_bucket — 'top_10' / 'top_100' / 'top_1000' / 'top_10000'. Derived
--                from rank_overall so the dashboard can slice the lead
--                queue into pre-sized cohorts without computing CASE.
--
-- Borrower-level measures (per row):
--   approval_status   — pending / approved / rejected / hold. Joined from
--                       mip.gold.borrower_lifecycle_state (hourly sync of
--                       Lakebase mip_app.approvals). Missing → 'pending'.
--   outreach_status   — queued / actioned / none.
--
-- Cell-level measures (read in dashboard/Genie aggregations):
--   count_top10                — COUNT(*) FILTER (rank_overall <= 10).
--   count_top100               — COUNT(*) FILTER (rank_overall <= 100).
--   sum_marketable_population  — COUNT(*) (size of the ranked population).
--   approval_rate              — COUNT(approved) / COUNT(*) * 100, 2dp.
--   outreach_rate              — COUNT(actioned) / COUNT(*) * 100, 2dp.
--   delta_vs_prior_count       — (addressable_today - addressable_prior_week)
--                                / addressable_prior_week * 100, 2dp. Sourced
--                                from mip.gold.funnel_snapshot_daily keyed by
--                                (snapshot_date, state, segment_code).
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.lead_generation_metric_view AS
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
  SELECT state, segment_code, addressable_borrowers
  FROM mip.gold.funnel_snapshot_daily
  WHERE snapshot_date = (SELECT snapshot_date FROM latest_snapshot)
),
prior AS (
  SELECT state, segment_code,
         addressable_borrowers AS addressable_borrowers_prior
  FROM mip.gold.funnel_snapshot_daily
  WHERE snapshot_date = (SELECT snapshot_date FROM prior_snapshot)
),
exploded AS (
  SELECT
    lp.clip,
    lp.state,
    segment                                 AS segment,
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
    lp.refreshed_at,
    COALESCE(ls.approval_status, 'pending') AS approval_status,
    COALESCE(ls.outreach_status, 'none')    AS outreach_status,
    lp.borrower_id
  FROM mip.gold.lead_population AS lp
  LEFT JOIN mip.gold.borrower_lifecycle_state AS ls
    ON ls.borrower_id = lp.borrower_id
  LATERAL VIEW EXPLODE(lp.segment_codes) seg AS segment
)
SELECT
  e.clip,
  e.state,
  e.segment,
  e.rank_bucket,
  e.rank_overall,
  e.rank_within_state,
  e.opportunity_score,
  e.equity_estimate,
  e.rate_spread_bps,
  e.population_version,
  e.refreshed_at,
  e.approval_status,
  e.outreach_status,
  -- Per-(state, segment) rates computed against the snapshot so widgets that
  -- want a cell-level KPI don't need to re-aggregate across the full view.
  CAST(
    ROUND(
      100.0 * COUNT(CASE WHEN e.approval_status = 'approved' THEN 1 END)
        OVER (PARTITION BY e.state, e.segment)
        / NULLIF(COUNT(*) OVER (PARTITION BY e.state, e.segment), 0),
      2
    ) AS DOUBLE
  )                                           AS approval_rate,
  CAST(
    ROUND(
      100.0 * COUNT(CASE WHEN e.outreach_status = 'actioned' THEN 1 END)
        OVER (PARTITION BY e.state, e.segment)
        / NULLIF(COUNT(*) OVER (PARTITION BY e.state, e.segment), 0),
      2
    ) AS DOUBLE
  )                                           AS outreach_rate,
  CAST(
    ROUND(
      100.0 * (COALESCE(t.addressable_borrowers, 0) - COALESCE(p.addressable_borrowers_prior, 0))
        / NULLIF(p.addressable_borrowers_prior, 0),
      2
    ) AS DOUBLE
  )                                           AS delta_vs_prior_count
FROM exploded AS e
LEFT JOIN today AS t ON t.state = e.state AND t.segment_code = e.segment
LEFT JOIN prior AS p ON p.state = e.state AND p.segment_code = e.segment;

COMMENT ON VIEW mip.semantics.lead_generation_metric_view IS
  'Genie + dashboard metric view over gold.lead_population + gold.borrower_lifecycle_state + gold.funnel_snapshot_daily. Dimensions: segment, state, rank_bucket. Row measures: approval_status, outreach_status. Aggregate measures: count_top10, count_top100, sum_marketable_population, approval_rate, outreach_rate, delta_vs_prior_count. See docs/data-contract-module0.md §3.5 + docs/validation/metric-views.md.';
