-- =============================================================================
-- gold_property_owner_bridge.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.property_owner_bridge`. One row per
--            `owner_link_id` carrying the Owner-Link rollup that gold.
--            borrower_360 stamps onto each CLIP for investor / multi-property
--            signals. Direct projection of silver.owner_property_bridge
--            narrowed to the gold-layer contract columns.
--
-- Grain:     One row per owner_link_id. Upstream silver already aggregates
--            per Owner-Link across refreshed source coverage.
-- PK:        owner_link_id.
-- Clustering: Liquid cluster on (owner_link_id). Gold.borrower_360 joins 1:1
--            on this column; clustering makes the lookup cheap.
--
-- Data contract reference: docs/data-contract-module0.md §3.1.
-- Slice:     module0-real-data-slice3 (gold layer build).
-- Source:    mip.silver.owner_property_bridge (silver rollup produced by
--            pipelines/lakeflow/mip_feature_pipeline.py in slice 2).
--
-- PII posture: owner_link_id is a Cotality-mastered opaque identifier -- NOT
--            a direct identifier. No raw names, no street addresses. No
--            governance concern at this table; all redaction already occurred
--            upstream at the silver/gold boundary.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. Transformation uses
--            CREATE OR REPLACE TABLE ... AS SELECT (CTAS) because the table
--            is a pure projection of silver with no downstream MERGE keys
--            worth preserving between refreshes.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.property_owner_bridge (
  owner_link_id             STRING    NOT NULL COMMENT 'Cotality Owner Link. PK.',
  related_property_count    INT       NOT NULL COMMENT 'Count of distinct CLIPs tied to this Owner Link across refreshed source coverage. Drives Borrower360.related_property_count and the investor branch of fn_next_best_offer.',
  corporate_property_count  INT       NOT NULL COMMENT 'Number of related properties with owner_is_corporate = TRUE.',
  absentee_property_count   INT       NOT NULL COMMENT 'Number of related properties with is_absentee = TRUE.',
  distinct_states_count     INT       NOT NULL COMMENT 'Number of distinct situs_state values. Multi-market investor signal.',
  distinct_cbsa_count       INT       NOT NULL COMMENT 'Number of distinct situs_cbsa_code values.',
  primary_clip              STRING             COMMENT 'CLIP of the owner-occupied property for this Owner Link (if any). NULL when no owner-occupant in set.',
  refreshed_at              TIMESTAMP NOT NULL COMMENT 'Refresh timestamp for audit / provenance chips.'
)
USING DELTA
CLUSTER BY (owner_link_id)
COMMENT 'Owner-Link rollup projected into gold from silver.owner_property_bridge. Feeds the investor / multi-property branch of gold.borrower_360 + fn_next_best_offer. See docs/data-contract-module0.md §3.1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
