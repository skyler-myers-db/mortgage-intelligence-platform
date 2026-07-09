-- =============================================================================
-- 005_semantics_views.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent CREATE OR REPLACE VIEW manifest for the three
--            Genie-facing + dashboard-facing semantic views under
--            mip.semantics. A single file so a single bundle task can
--            materialise them AFTER the gold tables they read from are
--            built. This is the file `mip_refresh_scores` executes at
--            the end of its CTAS chain to guarantee Genie's trusted
--            assets bind on the first `databricks bundle deploy -t dev`.
--
-- Sources of truth (these files stay the authored copy for code review;
-- 005_semantics_views.sql is the deploy surface and must stay a
-- byte-equivalent concatenation):
--     sql/metric_views/lead_generation_metric_view.sql
--     sql/metric_views/segment_performance_metric_view.sql
--     sql/metric_views/borrower_opportunity_metric_view.sql
--
-- Idempotency: every statement below is CREATE OR REPLACE VIEW; re-running
-- this file any number of times is safe and byte-stable. No DDL against
-- any catalog/schema other than mip.semantics.* is emitted here — the
-- mip.semantics schema itself is created by 001_catalogs_schemas.sql.
--
-- Grounding contract: Genie's trusted_assets list in
-- genie/mortgage_lead_intelligence_space.yml references each of these
-- three fully qualified names. If a view name drifts, provision_genie_
-- space.py will fail to bind tables on create/update. Keep this file
-- in lockstep with the YAML.
--
-- Slice:     slice13-accuracy (zero-click semantics provisioning).
-- Data contract: docs/data-contract-module0.md §3.5, §3.6 + per-file
--            docstring in sql/metric_views/*.sql.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. mip.semantics.lead_generation_metric_view
-- -----------------------------------------------------------------------------
-- See sql/metric_views/lead_generation_metric_view.sql for the authored
-- copy + per-column rationale.
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
    lp.has_permit,
    lp.listed_for_sale,
    lp.listing_status_category,
    lp.listing_price,
    lp.listing_days_on_market,
    lp.heloc_propensity_score,
    lp.has_heloc_propensity_trigger,
    lp.refi_propensity_score,
    lp.has_refi_propensity_trigger,
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
  l.has_permit,
  l.listed_for_sale,
  l.listing_status_category,
  l.listing_price,
  l.listing_days_on_market,
  l.heloc_propensity_score,
  l.has_heloc_propensity_trigger,
  l.refi_propensity_score,
  l.has_refi_propensity_trigger,
  l.population_version,
  l.refreshed_at,
  l.approval_status,
  l.outreach_status,
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
  'Genie + dashboard borrower-grain metric view over gold.lead_population + gold.borrower_lifecycle_state + gold.funnel_snapshot_daily. Dimensions: segment_codes, primary_segment, state, rank_bucket, listed_for_sale, has_heloc_propensity_trigger. Row measures: approval_status, outreach_status, listing_price, listing_days_on_market, heloc_propensity_score, refi_propensity_score. Aggregate measures must COUNT(DISTINCT clip); approval_rate/outreach_rate/delta_vs_prior_count are state-level. See docs/data-contract-module0.md §3.5 + docs/validation/metric-views.md.';


-- -----------------------------------------------------------------------------
-- 2. mip.semantics.segment_performance_metric_view
-- -----------------------------------------------------------------------------
-- See sql/metric_views/segment_performance_metric_view.sql for the authored
-- copy + per-column rationale.
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


-- -----------------------------------------------------------------------------
-- 3. mip.semantics.borrower_opportunity_metric_view
-- -----------------------------------------------------------------------------
-- See sql/metric_views/borrower_opportunity_metric_view.sql for the authored
-- copy + per-column rationale.
CREATE OR REPLACE VIEW mip.semantics.borrower_opportunity_metric_view AS
SELECT
  b.clip,
  b.state,
  b.segment_codes,
  CASE
    WHEN SIZE(b.segment_codes) > 0 THEN b.segment_codes[0]
    ELSE 'none'
  END                                               AS primary_segment,
  CASE
    WHEN SIZE(b.segment_codes) > 0 THEN b.segment_codes[0]
    ELSE 'none'
  END                                               AS segment,
  b.first_pos_loan_type                             AS loan_purpose,
  b.loan_product_type,
  b.origination_channel,
  b.is_investor,
  b.is_current_customer,
  b.is_former_customer,
  b.is_competitor_lien,
  b.has_permit,
  b.listed_for_sale,
  b.listing_status_category,
  b.listing_price,
  b.listing_days_on_market,
  b.heloc_propensity_score,
  b.has_heloc_propensity_trigger,
  b.refi_propensity_score,
  b.has_refi_propensity_trigger,
  b.current_lender_ref,
  b.rate_spread_bps,
  b.equity_pct,
  b.in_the_money,
  b.current_lien_balance,
  b.opportunity_score
FROM mip.gold.borrower_360 AS b;

COMMENT ON VIEW mip.semantics.borrower_opportunity_metric_view IS
  'Genie + dashboard borrower-grain view over gold.borrower_360 (one row per clip). Exposed row columns: clip, state, segment_codes, primary_segment, deprecated segment alias, loan_purpose, loan_product_type, origination_channel, is_investor, is_current_customer, is_former_customer, is_competitor_lien, has_permit, listed_for_sale, listing_status_category, listing_price, listing_days_on_market, heloc_propensity_score, has_heloc_propensity_trigger, refi_propensity_score, has_refi_propensity_trigger, current_lender_ref, rate_spread_bps, equity_pct, in_the_money, current_lien_balance, opportunity_score. These are plain columns, not materialized measure columns; dashboards and Genie compute read-time aggregations such as AVG(rate_spread_bps), AVG(equity_pct), COUNT(DISTINCT clip), and AVG(opportunity_score). See docs/data-contract-module0.md §3.2.';
