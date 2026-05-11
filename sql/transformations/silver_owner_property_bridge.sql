-- =============================================================================
-- silver_owner_property_bridge.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE that populates
--            `mip.silver.owner_property_bridge` from
--            silver.property_master ⟕ silver.lien_current. One row per
--            distinct Owner-Link id.
--
-- Grain:     One row per owner_link_id.
-- PK (MERGE key): owner_link_id.
-- Geography contract: inherited from upstream silver tables. Reasserted here
--            via NOT NULL state / Owner-Link checks so malformed rows cannot
--            leak into the bridge.
-- Slice:     module0-real-data-slice2.
-- Data contract: docs/data-contract-module0.md §3.1 is the canonical gold
--            projection; this silver rollup pre-aggregates so gold becomes
--            a cheap JOIN.
--
-- Idempotency: MERGE on owner_link_id.
-- =============================================================================

MERGE INTO mip.silver.owner_property_bridge AS t
USING (
  WITH props AS (
    SELECT
      pm.owner_link_id,
      pm.clip,
      pm.situs_state,
      pm.situs_cbsa_code,
      pm.owner_is_corporate,
      pm.is_absentee,
      pm.owner_occupancy_code,
      lc.avm_value,
      lc.total_open_lien_balance,
      lc.estimated_equity
    FROM mip.silver.property_master     AS pm
    LEFT JOIN mip.silver.lien_current   AS lc
      ON lc.clip = pm.clip
    WHERE pm.owner_link_id IS NOT NULL
      AND pm.situs_state IS NOT NULL
  )
  SELECT
    owner_link_id,
    CAST(COUNT(DISTINCT clip) AS INT)                                AS related_property_count,
    CAST(SUM(CASE WHEN owner_is_corporate THEN 1 ELSE 0 END) AS INT) AS corporate_property_count,
    CAST(SUM(CASE WHEN is_absentee        THEN 1 ELSE 0 END) AS INT) AS absentee_property_count,
    CAST(COUNT(DISTINCT situs_state)     AS INT)                     AS distinct_states_count,
    CAST(COUNT(DISTINCT situs_cbsa_code) AS INT)                     AS distinct_cbsa_count,
    CAST(SUM(COALESCE(avm_value,               0)) AS BIGINT)        AS total_avm_value,
    CAST(SUM(COALESCE(total_open_lien_balance, 0)) AS BIGINT)        AS total_open_lien_balance,
    CAST(SUM(COALESCE(estimated_equity,        0)) AS BIGINT)        AS total_estimated_equity,
    MAX(CASE WHEN owner_occupancy_code = 'O' THEN clip END)          AS primary_clip,
    CURRENT_TIMESTAMP()                                              AS ingest_ts,
    CAST(:batch_id AS STRING)                                        AS _meta_batch_id
  FROM props
  GROUP BY owner_link_id
) AS s
  ON t.owner_link_id = s.owner_link_id
WHEN MATCHED THEN UPDATE SET
  related_property_count   = s.related_property_count,
  corporate_property_count = s.corporate_property_count,
  absentee_property_count  = s.absentee_property_count,
  distinct_states_count    = s.distinct_states_count,
  distinct_cbsa_count      = s.distinct_cbsa_count,
  total_avm_value          = s.total_avm_value,
  total_open_lien_balance  = s.total_open_lien_balance,
  total_estimated_equity   = s.total_estimated_equity,
  primary_clip             = s.primary_clip,
  ingest_ts                = s.ingest_ts,
  _meta_batch_id           = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  owner_link_id, related_property_count, corporate_property_count,
  absentee_property_count, distinct_states_count, distinct_cbsa_count,
  total_avm_value, total_open_lien_balance, total_estimated_equity,
  primary_clip, ingest_ts, _meta_batch_id
) VALUES (
  s.owner_link_id, s.related_property_count, s.corporate_property_count,
  s.absentee_property_count, s.distinct_states_count, s.distinct_cbsa_count,
  s.total_avm_value, s.total_open_lien_balance, s.total_estimated_equity,
  s.primary_clip, s.ingest_ts, s._meta_batch_id
);
