-- =============================================================================
-- certified_segment_performance_metric_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Certified Databricks metric view contract for segment strategy.
--
-- Certification: Module 0 certified semantic contract. Segment counts remain
--            de-duplicated at the rollup grain; overlap/intersection cohorts
--            use borrower-grain tools instead of summing cards.
-- =============================================================================

CREATE OR REPLACE VIEW mip.semantics.certified_segment_performance_metric_view
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: "Certification: Module 0 Segment Intelligence metrics over the reviewed segment_performance semantic view."
source: mip.semantics.segment_performance_metric_view
fields:
  - name: Segment Code
    expr: segment_code
    comment: "Reviewed Module 0 segment code."
  - name: Segment Name
    expr: name
    comment: "Borrower-facing segment label."
  - name: State
    expr: state
    comment: "Two-letter state or _ALL rollup."
measures:
  - name: Segment Borrowers
    expr: SUM(count)
    comment: "Borrower count at the selected segment/state rollup grain."
  - name: Average Opportunity Score
    expr: AVG(avg_score)
    comment: "Average opportunity score for the selected segment rollup rows."
  - name: Approval Rate
    expr: AVG(approval_rate)
    comment: "Approval rate from the latest funnel snapshot."
  - name: Outreach Rate
    expr: AVG(outreach_rate)
    comment: "Outreach rate from the latest funnel snapshot."
  - name: Count Delta
    expr: AVG(delta_vs_prior_count)
    comment: "Average week-over-week count delta for selected segment rollups."
$$;
