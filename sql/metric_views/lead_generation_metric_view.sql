-- =============================================================================
-- lead_generation_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Genie-reachable and dashboard-reachable semantic view of
--            `mip.gold.lead_population`. Dimensions the ranked lead
--            queue by state + a simple rank_bucket while preserving
--            borrower grain. Segment questions should filter with
--            array_contains(segment_codes, '<segment_code>') or use
--            mip.semantics.segment_performance_metric_view for segment-grain
--            KPIs.
--
-- Grain:     Inherits gold.lead_population (one row per clip in the
--            top-N ranked cut).
-- Slice:     slice13-accuracy (lifecycle + delta join).
-- Data contract: docs/data-contract-module0.md §3.5 + docs/validation/
--            metric-views.md.
--
-- Dimensions:
--   segment_codes — array of borrower segment memberships; not exploded.
--   primary_segment — first segment code for display only; do not use as a
--                     full segment-membership filter.
--   state      — situs state.
--   rank_bucket — 'top_10' / 'top_100' / 'top_1000' / 'top_10000' /
--                'outside_top_10000'. Derived from rank_overall so the
--                dashboard can slice the lead queue into pre-sized cohorts
--                without computing CASE.
--
-- Borrower-level measures (per row):
--   approval_status   — pending / approved / rejected / hold. Joined from
--                       mip.gold.borrower_lifecycle_state (hourly sync of
--                       Lakebase mip_app.approvals). Missing → 'pending'.
--   outreach_status   — queued / actioned / none.
--
-- Materialized aggregate columns (computed per row as state-partitioned
-- windows; every borrower row in a state carries that state's value):
--   approval_rate              — 100 * COUNT(approved) / COUNT(*) OVER state, 2dp.
--   outreach_rate              — 100 * COUNT(actioned) / COUNT(*) OVER state, 2dp.
--   delta_vs_prior_count       — (addressable_today - addressable_prior_week)
--                                / addressable_prior_week * 100, 2dp. Sourced
--                                from mip.gold.funnel_snapshot_daily keyed by
--                                (snapshot_date, state, segment_code).
--
-- Read-time aggregations (NOT columns — dashboards / Genie compute these over
-- the exposed columns; do not SELECT them as if they exist):
--   count_top10                — COUNT(*) FILTER (rank_overall <= 10).
--   count_top100               — COUNT(*) FILTER (rank_overall <= 100).
--   sum_marketable_population  — COUNT(DISTINCT clip) (size of the ranked population).
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
lead_rows AS (
  SELECT
    lp.clip,
    lp.state,
    lp.segment_codes                          AS segment_codes,
    CASE
      WHEN SIZE(lp.segment_codes) > 0 THEN lp.segment_codes[0]
      ELSE 'none'
    END                                     AS primary_segment,
    CASE
      WHEN lp.rank_overall <= 10    THEN 'top_10'
      WHEN lp.rank_overall <= 100   THEN 'top_100'
      WHEN lp.rank_overall <= 1000  THEN 'top_1000'
      WHEN lp.rank_overall <= 10000 THEN 'top_10000'
      ELSE                               'outside_top_10000'
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
)
SELECT
  l.clip,
  l.state,
  l.segment_codes,
  l.primary_segment,
  l.rank_bucket,
  l.rank_overall,
  l.rank_within_state,
  l.opportunity_score,
  l.equity_estimate,
  l.rate_spread_bps,
  l.population_version,
  l.refreshed_at,
  l.approval_status,
  l.outreach_status,
  -- State-level rates preserve one row per borrower; segment-level rates
  -- belong in segment_performance_metric_view.
  CAST(
    ROUND(
      100.0 * COUNT(CASE WHEN l.approval_status = 'approved' THEN 1 END)
        OVER (PARTITION BY l.state)
        / NULLIF(COUNT(*) OVER (PARTITION BY l.state), 0),
      2
    ) AS DOUBLE
  )                                           AS approval_rate,
  CAST(
    ROUND(
      100.0 * COUNT(CASE WHEN l.outreach_status = 'actioned' THEN 1 END)
        OVER (PARTITION BY l.state)
        / NULLIF(COUNT(*) OVER (PARTITION BY l.state), 0),
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
FROM lead_rows AS l
LEFT JOIN today AS t ON t.state = l.state AND t.segment_code = '_ALL'
LEFT JOIN prior AS p ON p.state = l.state AND p.segment_code = '_ALL';

COMMENT ON VIEW mip.semantics.lead_generation_metric_view IS
  'Genie + dashboard borrower-grain metric view over gold.lead_population + gold.borrower_lifecycle_state + gold.funnel_snapshot_daily (one row per clip in the ranked cut). Exposed columns: clip, state, segment_codes, primary_segment, rank_bucket, rank_overall, rank_within_state, opportunity_score, equity_estimate, rate_spread_bps, population_version, refreshed_at, approval_status, outreach_status, approval_rate, outreach_rate, delta_vs_prior_count. approval_rate / outreach_rate / delta_vs_prior_count ARE materialized columns (state-partitioned windows, so they repeat per borrower row in a state). count_top10 / count_top100 / sum_marketable_population are NOT columns — they are read-time aggregations the dashboard or Genie computes over the exposed columns (and must COUNT(DISTINCT clip)). See docs/data-contract-module0.md §3.5 + docs/validation/metric-views.md.';
