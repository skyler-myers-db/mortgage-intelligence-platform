-- =============================================================================
-- silver_property_owners.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.property_owners`, the multi-owner explode of
--            entrada_eval_property_domain_v3 (S1.1). One row per occupied
--            owner slot (owner_1..owner_4 column families), max 4 owners per
--            property record. Duplicate Owner Links within one CLIP collapse
--            to the lowest slot, so resolved owners keep the
--            one-row-per-(clip, owner_link) grain.
--
-- Scope (ROADMAP-TEMPORARY): owner_entity_type is a name/indicator
--            CLASSIFIER — classify + caveat + suppress only. It is NOT
--            entity resolution; Cotality entity resolution is upstream WIP
--            and will replace this classifier when mastered entity types
--            ship. This scope is a temporary roadmap state, not a product
--            ceiling. See docs/data-contract-module0.md §2.6.
--
-- Grain:     One row per (clip, owner_position). For rows with a non-null
--            owner_link_id this is equivalently one row per (clip,
--            owner_link_id) — the transformation dedupes duplicate links.
-- Slice:     s1-1-multi-owner.
-- Data contract: docs/data-contract-module0.md §2.6.
--
-- PII posture:
--            Raw owner_N_full_name strings are consumed transiently at
--            classification/hash time and NEVER land here. Only the salted
--            owner_name_hash (same sha2 + secret-scope salt contract as
--            silver.property_master) and the opaque Owner Link persist.
--
-- Contact eligibility:
--            is_contact_eligible = owner_entity_type <> 'unresolved'.
--            gold.borrower_360 reads this table to fail marketing_eligible
--            closed (suppression_reason 'unresolved_owner') whenever a CLIP
--            carries an unresolved owner.
--
-- Classifier parity: the CASE branches, regex patterns, and confidence
--            literals in the paired transformation must match
--            backend/services/owner_classification.py and the DLT path in
--            pipelines/lakeflow/mip_feature_pipeline.py. Guarded by
--            tests/unit/test_property_owners.py.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. Transformation MERGEs on
--            (clip, owner_position).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.property_owners (
  clip                   STRING    NOT NULL COMMENT 'Cotality CLIP (property_master.clip). PK part 1.',
  owner_position         INT       NOT NULL COMMENT 'Source owner slot 1..4 from entrada_eval_property_domain_v3. PK part 2.',
  owner_link_id          STRING             COMMENT 'Cotality Owner Link (owner_N_identifier). NULL when the source slot has a name but no identifier — such owners classify as unresolved.',
  owner_name_hash        STRING             COMMENT 'sha2(LOWER(TRIM(owner_N_full_name)) || '':'' || salt, 256), same salt contract as property_master.owner_name_hash. NULL when the slot has no name string. Raw names never land.',
  owner_entity_type      STRING    NOT NULL COMMENT 'Classifier output: individual | trust | llc | unresolved. ROADMAP-TEMPORARY name/indicator classification pending Cotality entity resolution.',
  resolution_confidence  DOUBLE    NOT NULL COMMENT 'Deterministic classifier confidence 0..1 per branch (see backend/services/owner_classification.py). Pinned by tests/fixtures/owner_entity_type_golden.json.',
  is_corporate_indicator BOOLEAN   NOT NULL COMMENT 'TRUE when owner_N_corporate_indicator = ''Y'' after UPPER(TRIM(...)) coercion (same rule as property_master.owner_is_corporate).',
  is_contact_eligible    BOOLEAN   NOT NULL COMMENT 'owner_entity_type <> ''unresolved''. Unresolved owners are excluded from every contact-eligible population via gold.borrower_360.marketing_eligible.',
  situs_state            STRING    NOT NULL COMMENT 'Situs state carried from the source row; coverage is data-driven.',
  ingest_ts              TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp.',
  _meta_batch_id         STRING             COMMENT 'Lakeflow pipeline run id for observability.'
)
USING DELTA
CLUSTER BY (clip)
COMMENT 'Multi-owner explode of entrada_eval_property_domain_v3 (S1.1): one row per occupied owner slot (max 4), with owner_entity_type classification {individual,trust,llc,unresolved} + resolution_confidence. Classify + caveat + suppress only — ROADMAP-TEMPORARY pending Cotality entity resolution. See docs/data-contract-module0.md §2.6.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
