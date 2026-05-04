-- =============================================================================
-- gold_source_readiness.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.source_readiness` via CTAS. This keeps the
--            Admin source-readiness panel green without granting the running
--            Databricks App principal direct access to `mip.silver.*`.
--
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.source_readiness AS
WITH refresh_anchor AS (
  SELECT refresh_at AS checked_at
  FROM mip.ref.refresh_run_state
  ORDER BY captured_at DESC
  LIMIT 1
),
source_rows AS (
  SELECT
    1 AS sort_order,
    'Cotality Public Records' AS source_name,
    'live' AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    'Delta Share · nightly' AS note,
    'mip.silver.property_master' AS source_table
  FROM mip.silver.property_master

  UNION ALL

  SELECT
    2 AS sort_order,
    'Voluntary Lien' AS source_name,
    'live' AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    'Delta Share · nightly' AS note,
    'mip.silver.lien_current' AS source_table
  FROM mip.silver.lien_current

  UNION ALL

  SELECT
    3 AS sort_order,
    'MMA Mortgage Analytics' AS source_name,
    'live' AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    'Delta Share · nightly' AS note,
    'mip.silver.mortgage_events' AS source_table
  FROM mip.silver.mortgage_events

  UNION ALL

  SELECT
    4 AS sort_order,
    'CLIP' AS source_name,
    'live' AS status,
    COUNT(DISTINCT clip) AS row_count,
    MAX(ingest_ts) AS last_updated,
    'Mastered property id' AS note,
    'mip.silver.property_master' AS source_table
  FROM mip.silver.property_master

  UNION ALL

  SELECT
    5 AS sort_order,
    'Owner Link' AS source_name,
    'live' AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    'Mastered owner graph' AS note,
    'mip.silver.owner_property_bridge' AS source_table
  FROM mip.silver.owner_property_bridge

  UNION ALL

  SELECT
    6 AS sort_order,
    'AVM' AS source_name,
    'live' AS status,
    COUNT_IF(avm_value IS NOT NULL) AS row_count,
    CAST(MAX(avm_as_of_date) AS TIMESTAMP) AS last_updated,
    'Delta Share · weekly' AS note,
    'mip.silver.lien_current' AS source_table
  FROM mip.silver.lien_current

  UNION ALL

  SELECT
    7 AS sort_order,
    'MLS' AS source_name,
    'roadmap' AS status,
    CAST(NULL AS BIGINT) AS row_count,
    CAST(NULL AS TIMESTAMP) AS last_updated,
    'Contracted · pending Cotality share' AS note,
    CAST(NULL AS STRING) AS source_table

  UNION ALL

  SELECT
    8 AS sort_order,
    'Building Permits' AS source_name,
    'roadmap' AS status,
    CAST(NULL AS BIGINT) AS row_count,
    CAST(NULL AS TIMESTAMP) AS last_updated,
    'Contracted · pending Cotality share' AS note,
    CAST(NULL AS STRING) AS source_table
)
SELECT
  source_name,
  status,
  row_count,
  last_updated,
  note,
  source_table,
  sort_order,
  (SELECT checked_at FROM refresh_anchor) AS checked_at
FROM source_rows;
