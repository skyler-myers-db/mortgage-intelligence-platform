-- =============================================================================
-- certified_borrower_opportunity_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Certified Databricks metric view contract for borrower-grain
--            opportunity questions and governed Genie follow-ups.
--
-- Certification: Module 0 certified semantic contract. This view exposes only
--            display-safe borrower-grain fields; raw CLIP, owner names, street
--            addresses, phone, and email remain outside the app surface.
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.certified_borrower_opportunity_metric_view
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: "Certification: Module 0 borrower opportunity metrics over the reviewed borrower_opportunity semantic view."
source: mip.semantics.borrower_opportunity_metric_view
fields:
  - name: State
    expr: state
    synonyms: ["geography", "coverage state", "borrower state", "situs state"]
    comment: "Borrower situs state from the refreshed coverage footprint."
  - name: Primary Segment
    expr: primary_segment
    synonyms: ["segment", "cohort", "opportunity type", "borrower group"]
    comment: "Display-only primary segment; segment overlap uses segment_codes."
  - name: In The Money
    expr: in_the_money
    synonyms: ["refi incentive", "rate incentive", "economic incentive", "prime refi"]
    comment: "TRUE when the rate-spread and equity thresholds are both met."
  - name: Listed For Sale
    expr: listed_for_sale
    synonyms: ["listing", "MLS", "for sale", "purchase intent"]
    comment: "TRUE only for current active/under-contract MLS listing rows."
  - name: HELOC Propensity Trigger
    expr: has_heloc_propensity_trigger
    synonyms: ["HELOC intent", "home equity intent", "equity credit demand"]
    comment: "Cotality HELOC propensity trigger; not a filed permit."
  - name: Current Lender Ref
    expr: current_lender_ref
    synonyms: ["current servicer", "lien holder", "target lender", "competitor lender"]
    comment: "Public-safe lender alias, never the raw lender string."
measures:
  - name: Borrowers
    expr: COUNT(DISTINCT clip)
    synonyms: ["borrower count", "population", "unique borrowers", "market size"]
    comment: "De-duplicated borrower count."
  - name: Average Rate Spread Bps
    expr: AVG(rate_spread_bps)
    synonyms: ["average spread", "rate delta", "spread bps", "refi economics"]
    comment: "Average current-rate minus market-rate spread, in basis points."
  - name: Average Equity Percent
    expr: AVG(equity_pct)
    synonyms: ["average equity", "modeled equity", "home equity", "equity pct"]
    comment: "Average modeled equity percentage."
  - name: In The Money Borrowers
    expr: COUNT(CASE WHEN in_the_money THEN clip END)
    synonyms: ["refi candidates", "in the money count", "economic incentive count"]
    comment: "Borrowers meeting the reviewed in-the-money predicate."
  - name: Average Opportunity Score
    expr: AVG(opportunity_score)
    synonyms: ["avg score", "mean score", "borrower score", "opportunity score"]
    comment: "Average Module 0 opportunity score."
$$;
