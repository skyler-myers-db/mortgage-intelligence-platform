-- =============================================================================
-- gold_property_owner_bridge.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.property_owner_bridge` as a CTAS
--            projection of silver.owner_property_bridge. Gold carries a
--            narrower schema than silver (drops total_avm_value /
--            total_open_lien_balance / total_estimated_equity /
--            ingest_ts / _meta_batch_id -- those live in silver for
--            Genie questions, not on the gold hot path).
--
-- Grain:     One row per owner_link_id.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild is the
--            default refresh posture (gold is precomputed).
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.1.
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. The clustering,
-- column COMMENTs, and TBLPROPERTIES here mirror sql/ddl/gold_property_owner_
-- bridge.sql; the refresh job never re-runs that DDL.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.property_owner_bridge
CLUSTER BY (owner_link_id)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
SELECT
  s.owner_link_id,
  s.related_property_count,
  s.corporate_property_count,
  s.absentee_property_count,
  s.distinct_states_count,
  s.distinct_cbsa_count,
  s.primary_clip,
  -- Shared refresh_at captured once per run. See audit-holes-round-3 #7.
  (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1) AS refreshed_at
FROM mip.silver.owner_property_bridge AS s
WHERE s.owner_link_id IS NOT NULL;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.property_owner_bridge.owner_link_id IS 'Cotality Owner Link. PK.';
COMMENT ON COLUMN mip.gold.property_owner_bridge.related_property_count IS 'Count of distinct CLIPs tied to this Owner Link across refreshed source coverage. Drives Borrower360.related_property_count and the investor branch of fn_next_best_offer.';
COMMENT ON COLUMN mip.gold.property_owner_bridge.corporate_property_count IS 'Number of related properties with owner_is_corporate = TRUE.';
COMMENT ON COLUMN mip.gold.property_owner_bridge.absentee_property_count IS 'Number of related properties with is_absentee = TRUE.';
COMMENT ON COLUMN mip.gold.property_owner_bridge.distinct_states_count IS 'Number of distinct situs_state values. Multi-market investor signal.';
COMMENT ON COLUMN mip.gold.property_owner_bridge.distinct_cbsa_count IS 'Number of distinct situs_cbsa_code values.';
COMMENT ON COLUMN mip.gold.property_owner_bridge.primary_clip IS 'CLIP of the owner-occupied property for this Owner Link (if any). NULL when no owner-occupant in set.';
COMMENT ON COLUMN mip.gold.property_owner_bridge.refreshed_at IS 'Refresh timestamp for audit / provenance chips.';
