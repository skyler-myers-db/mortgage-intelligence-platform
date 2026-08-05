-- =============================================================================
-- portfolio_headline_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Named semantic home for EVERY demoed headline aggregate (S1).
--            Borrower-grain view over `mip.gold.borrower_360` whose indicator
--            columns define the home-page KPIs. The app's portfolio preview
--            (backend/services/repositories/databricks_portfolio.py) MUST
--            aggregate over this view — never re-derive a headline number
--            with an ad-hoc predicate in a router or service.
--
-- Grain:     One row per borrower (mip.gold.borrower_360 grain), so the
--            portfolio-builder criteria push-down (WHERE on the dimension
--            columns below) composes with every headline measure.
--
-- Headline measures (aggregate over the indicator columns):
--   marketable_population    — COUNT(*).
--   refi_economics_screen    — SUM(in_the_money). "Refi economics screen" KPI.
--   high_opportunity         — SUM(is_high_opportunity). "Opportunity score
--                              75+" KPI; predicate is mip.gold.
--                              fn_high_opportunity (canonical threshold —
--                              no literal here by design).
--   offers_available         — SUM(offer_available). Count of borrowers with
--                              a non-null fn_next_best_offer decision
--                              (recommended_offer_code IS NOT NULL).
--   offers_recommended       — SUM(offer_recommended). "Primary offer paths"
--                              KPI: non-null AND not 'nurture' (an actionable
--                              lane, not just a decision).
--   avg_opportunity_score    — AVG(opportunity_score).
--
-- Score band:
--   score_band               — mip.gold.fn_score_band(opportunity_score);
--                              'high' / 'med' / 'low' display bands
--                              (canonical edges live in the function only).
--
-- Dimensions: every column backend/services/repositories/databricks_portfolio
--            .py::build_preview_predicates can reference, passed through
--            unchanged from borrower_360 (state, occupancy, lien shape,
--            Owner Link, intent, relationship, product, channel, equity,
--            contactability). Keep this list a superset of the predicate
--            vocabulary or filtered previews will fail at the warehouse.
--
-- Slice:     S1 (canonical opportunity score + headline metric views).
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.portfolio_headline_metric_view AS
SELECT
  -- Grain + geography
  b.borrower_id,
  b.state,
  -- Headline measure indicators
  b.opportunity_score,
  b.in_the_money,
  mip.gold.fn_high_opportunity(b.opportunity_score)                    AS is_high_opportunity,
  mip.gold.fn_score_band(b.opportunity_score)                          AS score_band,
  (b.recommended_offer_code IS NOT NULL)                               AS offer_available,
  (b.recommended_offer_code IS NOT NULL
    AND b.recommended_offer_code <> 'nurture')                         AS offer_recommended,
  -- Criteria push-down dimensions (build_preview_predicates vocabulary)
  b.recommended_offer_code,
  b.is_owner_occupied,
  b.current_lien_balance,
  b.rate_spread_bps,
  b.second_pos_amount,
  b.related_property_count,
  b.listed_for_sale,
  b.has_heloc_propensity_trigger,
  b.is_current_customer,
  b.is_former_customer,
  b.is_competitor_lien,
  b.current_lender_ref,
  b.loan_product_type,
  b.origination_channel,
  b.equity_pct,
  -- Contactability dimensions (EligibilityService predicate vocabulary)
  b.marketing_eligible,
  b.consent_status,
  b.suppression_reason,
  b.dnc,
  b.eligible_recontact_at,
  b.last_touch_at,
  b.has_unresolved_owner,
  b.refreshed_at
FROM mip.gold.borrower_360 AS b;

COMMENT ON VIEW mip.semantics.portfolio_headline_metric_view IS 'Borrower-grain semantic view defining every demoed headline KPI: marketable population (COUNT(*)), refi economics screen (SUM(in_the_money)), high opportunity (SUM(is_high_opportunity), canonical fn_high_opportunity threshold), offers available (SUM(offer_available), non-null fn_next_best_offer decision), primary offer paths (SUM(offer_recommended)), and average opportunity score. Home-page KPIs aggregate over this view with portfolio-builder criteria pushed down as WHERE predicates on the dimension columns.';
