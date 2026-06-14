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
--   state              — situs state from refreshed source coverage.
--   segment_codes      — array<SegmentCode>; preserves all borrower segment
--                        memberships without multiplying borrower rows.
--   primary_segment    — first segment code when present; otherwise 'none'.
--   segment            — deprecated display alias for primary_segment. Kept
--                        for stale Lakeview / Genie SQL while segment_codes
--                        remains the membership contract.
--   loan_purpose       — first_pos_loan_type (proxy for loan_purpose; CONV/
--                        FHA/VA etc. from share).
--   is_investor        — boolean.
--   is_current_customer — boolean.
--   is_former_customer — boolean.
--   is_competitor_lien — boolean.
--   has_permit         — filed building-permit flag; false until a true
--                        permit source lands.
--   listed_for_sale    — live Cotality MLS active/under-contract flag.
--   listing_status_category / listing_price / listing_days_on_market
--                      — display-safe listing attributes.
--   has_heloc_propensity_trigger / heloc_propensity_score
--                      — Cotality HELOC propensity model signal.
--   has_refi_propensity_trigger / refi_propensity_score
--                      — Cotality refinance propensity model signal.
--   current_lender_ref — public-safe current-servicer alias from the governed
--                        lender dictionary; never the raw lender string.
--
-- Read-time aggregations (NOT materialized measure columns — this is a plain
-- borrower-grain view; dashboards / Genie compute these over the exposed
-- columns below):
--   avg_rate_spread_bps        — AVG(rate_spread_bps).
--   avg_equity_pct             — AVG(equity_pct).
--   count_itm                  — COUNT(*) FILTER (in_the_money = TRUE).
--   sum_loan_amount            — SUM(current_lien_balance).
--   count_total                — COUNT(*) / COUNT(DISTINCT clip).
--   avg_opportunity_score      — AVG(opportunity_score).
--
-- Non-negotiables:
--   * The view never exposes owner_name_hash, trigger_timeline_json, or any
--     internal-only column. Genie + dashboards see the UI-safe surface.
--   * The view is borrower-grain. It must not explode segment_codes; segment
--     overlap questions use array_contains/array_intersect and explicit
--     COUNT(DISTINCT clip) when they need membership analysis.
-- =============================================================================

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
  'Genie + dashboard borrower-grain metric view over gold.borrower_360 (one row per clip). Exposed columns: clip, state, segment_codes, primary_segment, deprecated segment alias, loan_purpose, is_investor, is_current_customer, is_former_customer, is_competitor_lien, has_permit, listed_for_sale, listing_status_category, listing_price, listing_days_on_market, heloc_propensity_score, has_heloc_propensity_trigger, refi_propensity_score, has_refi_propensity_trigger, current_lender_ref, rate_spread_bps, equity_pct, in_the_money, current_lien_balance, opportunity_score. These are plain columns, NOT materialized measures: the intended read-time aggregations are avg_rate_spread_bps = AVG(rate_spread_bps), avg_equity_pct = AVG(equity_pct), count_itm = COUNT(*) FILTER (in_the_money), sum_loan_amount = SUM(current_lien_balance), count_total = COUNT(DISTINCT clip), and avg_opportunity_score = AVG(opportunity_score), each computed by the dashboard or Genie query at read time. See docs/data-contract-module0.md §3.2.';
