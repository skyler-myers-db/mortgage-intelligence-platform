-- =============================================================================
-- certified_lead_generation_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Certified Databricks metric view contract for Lead Queue KPIs.
--            This leaves the existing dashboard/Genie SQL view intact and
--            publishes a metric-view wrapper with reviewed fields/measures.
--
-- Certification: Module 0 certified semantic contract. Do not claim this
--            capability live unless the bundle task has deployed this object
--            and the capability probe sees warehouse configuration.
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.certified_lead_generation_metric_view
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: "Certification: Module 0 Lead Queue metrics over the reviewed lead_generation semantic view."
source: mip.semantics.lead_generation_metric_view
fields:
  - name: State
    expr: state
    comment: "Borrower situs state from the refreshed coverage footprint."
  - name: Primary Segment
    expr: primary_segment
    comment: "Display-only primary segment; segment overlap uses segment_codes."
  - name: Rank Bucket
    expr: rank_bucket
    comment: "Lead Queue rank bucket derived from rank_overall."
  - name: Approval Status
    expr: approval_status
    comment: "Lakebase lifecycle approval status mirrored into gold."
  - name: Outreach Status
    expr: outreach_status
    comment: "Lakebase lifecycle outreach status mirrored into gold."
  - name: Listed For Sale
    expr: listed_for_sale
    comment: "TRUE only for active/under-contract MLS listing rows surfaced in gold."
measures:
  - name: Ranked Leads
    expr: COUNT(DISTINCT clip)
    comment: "De-duplicated Lead Queue population."
  - name: Average Opportunity Score
    expr: AVG(opportunity_score)
    comment: "Average Module 0 opportunity score over the selected Lead Queue rows."
  - name: Average Rate Spread Bps
    expr: AVG(rate_spread_bps)
    comment: "Average current-rate minus market-rate spread, in basis points."
  - name: Approved Lead Rate
    expr: AVG(approval_rate)
    comment: "State-window approval rate exposed by the reviewed semantic view."
  - name: Outreach Lead Rate
    expr: AVG(outreach_rate)
    comment: "State-window outreach rate exposed by the reviewed semantic view."
$$;
