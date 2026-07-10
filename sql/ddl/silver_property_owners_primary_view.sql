-- =============================================================================
-- silver_property_owners_primary_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   `mip.silver.property_owners_primary` — single-owner
--            compatibility projection of `mip.silver.property_owners`
--            (S1.1 multi-owner). One row per CLIP: the owner_position = 1
--            slot, i.e. the same owner that silver.property_master exposes
--            through its owner_link_id / owner_name_hash /
--            owner_is_corporate columns. Existing single-owner consumers
--            keep working unchanged against property_master; new consumers
--            that want "the primary owner plus its entity classification"
--            read this view instead of re-deriving slot-1 semantics.
--
-- Column vocabulary matches the legacy single-owner surface
-- (`owner_is_corporate`, not `is_corporate_indicator`) so a consumer can
-- swap `silver.property_master` owner columns for this view without
-- renames.
--
-- Executed by: the `init_property_owners_primary_view` sql_task in the
-- `mip_refresh_silver` job (databricks.yml) after the Lakeflow pipeline
-- materializes silver.property_owners. CREATE OR REPLACE VIEW is
-- idempotent and rebinds cheaply on every refresh.
--
-- Slice: s1-1-multi-owner. Data contract: docs/data-contract-module0.md §2.6.
-- =============================================================================

CREATE OR REPLACE VIEW mip.silver.property_owners_primary (
  clip                  COMMENT 'Cotality CLIP. One row per CLIP (primary owner slot).',
  owner_link_id         COMMENT 'Cotality Owner Link of the primary (slot 1) owner. Matches property_master.owner_link_id.',
  owner_name_hash       COMMENT 'Salted hash of the primary owner name. Matches property_master.owner_name_hash for the same refresh.',
  owner_entity_type     COMMENT 'Classifier output for the primary owner: individual | trust | llc | unresolved. ROADMAP-TEMPORARY classification pending Cotality entity resolution.',
  resolution_confidence COMMENT 'Deterministic classifier confidence 0..1 for the primary owner.',
  owner_is_corporate    COMMENT 'Legacy vocabulary alias of is_corporate_indicator (Y/N coercion contract).',
  is_contact_eligible   COMMENT 'FALSE when the primary owner is unresolved; unresolved owners are excluded from contact-eligible populations.',
  situs_state           COMMENT 'Situs state carried from the source row.'
)
COMMENT 'Single-owner compatibility projection of mip.silver.property_owners (owner_position = 1). Legacy column vocabulary; see docs/data-contract-module0.md §2.6.'
AS
SELECT
  clip,
  owner_link_id,
  owner_name_hash,
  owner_entity_type,
  resolution_confidence,
  is_corporate_indicator AS owner_is_corporate,
  is_contact_eligible,
  situs_state
FROM mip.silver.property_owners
WHERE owner_position = 1;
