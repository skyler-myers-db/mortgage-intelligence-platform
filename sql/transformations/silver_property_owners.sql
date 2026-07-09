-- =============================================================================
-- silver_property_owners.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE that populates `mip.silver.property_owners`
--            from `cotality_mortgage_data.corelogic.entrada_eval_property_
--            domain_v3` by exploding the owner_1..owner_4 slot column
--            families into one row per occupied owner slot (max 4 owners
--            per property record).
--
-- Grain:     One row per (clip, owner_position). Duplicate Owner Links
--            within a CLIP collapse to the lowest slot, so rows with a
--            non-null owner_link_id satisfy the contract grain of one row
--            per (clip, owner_link).
-- PK (MERGE key): (clip, owner_position).
-- Geography contract: WHERE situs_state IS NOT NULL; coverage is inherited
--            from refreshed source rows, never a fixed state list.
-- Slice:     s1-1-multi-owner.
-- Data contract: docs/data-contract-module0.md §2.6.
--
-- Classifier contract (ROADMAP-TEMPORARY — classify + caveat + suppress
-- only, pending Cotality entity resolution):
--   The CASE branch ORDER, regex patterns, and confidence literals below
--   are the shared contract with backend/services/owner_classification.py
--   (Python mirror + golden fixture) and the DLT path in
--   pipelines/lakeflow/mip_feature_pipeline.py. A change here must land in
--   all three files — tests/unit/test_property_owners.py enforces it.
--
--     1. vacant slot (no name, no trust name, no Owner Link) -> no row
--     2. owner_1_original_trust_name populated               -> trust  0.95
--     3. name RLIKE trust pattern                            -> trust  0.9
--     4. name RLIKE llc pattern, corporate indicator Y       -> llc    0.95
--     5. name RLIKE llc pattern                              -> llc    0.85
--     6. corporate indicator Y, no entity pattern            -> llc    0.6
--     7. name + Owner Link present                           -> individual 0.9
--     8. name present, Owner Link missing                    -> unresolved 0.4
--     9. Owner Link present, name blank                      -> unresolved 0.5
--
-- PII posture:
--   Raw owner_N_full_name values are consumed transiently in this statement
--   (classification + salted hash) and are never written. Only
--   `owner_name_hash` lands, using the exact expression contract from
--   silver_property_master.sql (sha2(LOWER(TRIM(name)) || ':' || salt, 256),
--   salt from secret scope `mip`/`pii-salt-v1`, no literal fallback).
--
-- Dedup guard: the share occasionally lands duplicate clip rows; the same
--   QUALIFY tiebreak as silver_property_master.sql (tax_year, then
--   last_foreclosure_date, then calculated_total_value) picks one source
--   row per CLIP before the explode so the MERGE key stays unique.
--
-- Idempotency: MERGE on (clip, owner_position).
-- =============================================================================

MERGE INTO mip.silver.property_owners AS t
USING (
  WITH src AS (
    SELECT
      clip,
      situs_state,
      owner_1_full_name,
      owner_2_full_name,
      owner_3_full_name,
      owner_4_full_name,
      CAST(owner_1_corporate_indicator AS STRING) AS owner_1_corporate_indicator,
      CAST(owner_2_corporate_indicator AS STRING) AS owner_2_corporate_indicator,
      CAST(owner_3_corporate_indicator AS STRING) AS owner_3_corporate_indicator,
      CAST(owner_4_corporate_indicator AS STRING) AS owner_4_corporate_indicator,
      CAST(owner_1_identifier AS STRING) AS owner_1_identifier,
      CAST(owner_2_identifier AS STRING) AS owner_2_identifier,
      CAST(owner_3_identifier AS STRING) AS owner_3_identifier,
      CAST(owner_4_identifier AS STRING) AS owner_4_identifier,
      CAST(owner_1_original_trust_name AS STRING) AS owner_1_original_trust_name
    FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
    WHERE clip IS NOT NULL
      AND situs_state IS NOT NULL
    -- Same dedup tiebreak as silver_property_master.sql (audit P2-10).
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY clip
      ORDER BY tax_year DESC, last_foreclosure_date DESC, calculated_total_value DESC
    ) = 1
  ),
  exploded AS (
    SELECT
      src.clip,
      src.situs_state,
      slot.owner_position,
      NULLIF(TRIM(slot.owner_full_name), '')                                   AS owner_full_name,
      (UPPER(TRIM(COALESCE(slot.corporate_indicator, ''))) = 'Y')              AS is_corporate_indicator,
      NULLIF(TRIM(slot.owner_identifier), '')                                  AS owner_link_id,
      NULLIF(TRIM(slot.trust_name), '')                                        AS trust_name
    FROM src
    LATERAL VIEW INLINE(ARRAY(
      STRUCT(1, src.owner_1_full_name, src.owner_1_corporate_indicator, src.owner_1_identifier, src.owner_1_original_trust_name),
      STRUCT(2, src.owner_2_full_name, src.owner_2_corporate_indicator, src.owner_2_identifier, CAST(NULL AS STRING)),
      STRUCT(3, src.owner_3_full_name, src.owner_3_corporate_indicator, src.owner_3_identifier, CAST(NULL AS STRING)),
      STRUCT(4, src.owner_4_full_name, src.owner_4_corporate_indicator, src.owner_4_identifier, CAST(NULL AS STRING))
    )) slot AS owner_position, owner_full_name, corporate_indicator, owner_identifier, trust_name
  ),
  classified AS (
    SELECT
      clip,
      situs_state,
      owner_position,
      owner_full_name,
      is_corporate_indicator,
      owner_link_id,
      -- Branch ORDER is the contract — see header + owner_classification.py.
      CASE
        WHEN trust_name IS NOT NULL THEN 'trust'
        WHEN owner_full_name IS NOT NULL AND UPPER(owner_full_name) RLIKE '\\b(TRUST|TRUSTEE|TRUSTEES|TRST|REVOCABLE|IRREVOCABLE)\\b|\\bTR\\s*$' THEN 'trust'
        WHEN owner_full_name IS NOT NULL AND UPPER(owner_full_name) RLIKE '\\b(LLC|L L C|LC|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|COMPANY|LP|LLP|LLLP|PARTNERSHIP|PARTNERS|HOLDINGS|VENTURES|PROPERTIES|ENTERPRISES|INVESTMENTS)\\b' AND is_corporate_indicator THEN 'llc'
        WHEN owner_full_name IS NOT NULL AND UPPER(owner_full_name) RLIKE '\\b(LLC|L L C|LC|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|COMPANY|LP|LLP|LLLP|PARTNERSHIP|PARTNERS|HOLDINGS|VENTURES|PROPERTIES|ENTERPRISES|INVESTMENTS)\\b' THEN 'llc'
        WHEN is_corporate_indicator THEN 'llc'
        WHEN owner_full_name IS NOT NULL AND owner_link_id IS NOT NULL THEN 'individual'
        WHEN owner_full_name IS NOT NULL THEN 'unresolved'
        ELSE 'unresolved'
      END AS owner_entity_type,
      CASE
        WHEN trust_name IS NOT NULL THEN 0.95
        WHEN owner_full_name IS NOT NULL AND UPPER(owner_full_name) RLIKE '\\b(TRUST|TRUSTEE|TRUSTEES|TRST|REVOCABLE|IRREVOCABLE)\\b|\\bTR\\s*$' THEN 0.9
        WHEN owner_full_name IS NOT NULL AND UPPER(owner_full_name) RLIKE '\\b(LLC|L L C|LC|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|COMPANY|LP|LLP|LLLP|PARTNERSHIP|PARTNERS|HOLDINGS|VENTURES|PROPERTIES|ENTERPRISES|INVESTMENTS)\\b' AND is_corporate_indicator THEN 0.95
        WHEN owner_full_name IS NOT NULL AND UPPER(owner_full_name) RLIKE '\\b(LLC|L L C|LC|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|COMPANY|LP|LLP|LLLP|PARTNERSHIP|PARTNERS|HOLDINGS|VENTURES|PROPERTIES|ENTERPRISES|INVESTMENTS)\\b' THEN 0.85
        WHEN is_corporate_indicator THEN 0.6
        WHEN owner_full_name IS NOT NULL AND owner_link_id IS NOT NULL THEN 0.9
        WHEN owner_full_name IS NOT NULL THEN 0.4
        ELSE 0.5
      END AS resolution_confidence
    FROM exploded
    -- Vacant slots emit no row (branch 1).
    WHERE owner_full_name IS NOT NULL
       OR owner_link_id IS NOT NULL
       OR trust_name IS NOT NULL
  ),
  deduped AS (
    -- Duplicate Owner Links inside one CLIP collapse to the lowest slot so
    -- resolved owners keep the one-row-per-(clip, owner_link) grain. Rows
    -- without an Owner Link are all kept (surrogate partition per slot).
    SELECT *
    FROM classified
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY clip, COALESCE(owner_link_id, CONCAT('pos-', CAST(owner_position AS STRING)))
      ORDER BY owner_position
    ) = 1
  )
  SELECT
    clip,
    owner_position,
    owner_link_id,
    -- PII HASH: same expression contract as silver_property_master.sql.
    -- Raw owner names are consumed here and never written.
    CASE
      WHEN owner_full_name IS NOT NULL THEN
        sha2(
          CONCAT(
            LOWER(TRIM(COALESCE(owner_full_name, ''))),
            ':',
            secret('mip', 'pii-salt-v1')
          ),
          256
        )
      ELSE NULL
    END                                                              AS owner_name_hash,
    owner_entity_type,
    resolution_confidence,
    is_corporate_indicator,
    (owner_entity_type <> 'unresolved')                              AS is_contact_eligible,
    situs_state,
    CURRENT_TIMESTAMP()                                              AS ingest_ts,
    CAST(:batch_id AS STRING)                                        AS _meta_batch_id
  FROM deduped
) AS s
  ON t.clip = s.clip AND t.owner_position = s.owner_position
WHEN MATCHED THEN UPDATE SET
  owner_link_id          = s.owner_link_id,
  owner_name_hash        = s.owner_name_hash,
  owner_entity_type      = s.owner_entity_type,
  resolution_confidence  = s.resolution_confidence,
  is_corporate_indicator = s.is_corporate_indicator,
  is_contact_eligible    = s.is_contact_eligible,
  situs_state            = s.situs_state,
  ingest_ts              = s.ingest_ts,
  _meta_batch_id         = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  clip, owner_position, owner_link_id, owner_name_hash, owner_entity_type,
  resolution_confidence, is_corporate_indicator, is_contact_eligible,
  situs_state, ingest_ts, _meta_batch_id
) VALUES (
  s.clip, s.owner_position, s.owner_link_id, s.owner_name_hash,
  s.owner_entity_type, s.resolution_confidence, s.is_corporate_indicator,
  s.is_contact_eligible, s.situs_state, s.ingest_ts, s._meta_batch_id
);
