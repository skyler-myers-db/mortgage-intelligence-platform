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
--            default refresh posture (gold is precomputed). Clustering is
--            declared in the DDL file; the CTAS carries it through.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.1.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.property_owner_bridge AS
SELECT
  s.owner_link_id,
  s.related_property_count,
  s.corporate_property_count,
  s.absentee_property_count,
  s.distinct_states_count,
  s.distinct_cbsa_count,
  s.primary_clip,
  CURRENT_TIMESTAMP()      AS refreshed_at
FROM mip.silver.owner_property_bridge AS s
WHERE s.owner_link_id IS NOT NULL;
