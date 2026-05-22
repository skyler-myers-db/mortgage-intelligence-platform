-- =============================================================================
-- gold_source_readiness.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.source_readiness` -- an app-readable summary
--            of the upstream data sources backing Module 0.
--
-- Grain:     One row per Admin data-source tile.
--
-- Governance posture:
--   The Databricks App service principal must not need SELECT on
--   `mip.silver.*` just to render the Admin readiness panel. The ETL refresh
--   job reads silver and writes this non-PII summary into gold; the app reads
--   only `mip.gold.source_readiness`.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.source_readiness (
  source_name   STRING    NOT NULL COMMENT 'Admin panel display name.',
  status        STRING    NOT NULL COMMENT 'live / demo_synthetic / configured_empty / not_configured / roadmap / error.',
  row_count     BIGINT             COMMENT 'Source row count when live.',
  last_updated  TIMESTAMP          COMMENT 'Latest source ingest timestamp when live.',
  note          STRING    NOT NULL COMMENT 'Human-readable source note.',
  source_table  STRING             COMMENT 'UC source table used by ETL; null for roadmap sources.',
  synthetic_demo BOOLEAN  NOT NULL COMMENT 'TRUE when rows come from the explicit Summit demo_synthetic first-party seed.',
  sort_order    INT       NOT NULL COMMENT 'Stable Admin panel order.',
  checked_at    TIMESTAMP NOT NULL COMMENT 'Gold refresh anchor used for this readiness snapshot.'
)
USING DELTA
CLUSTER BY (sort_order)
COMMENT 'Non-PII source-readiness summary for Admin. Populated by gold_source_readiness.sql so the running app does not need direct silver grants.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
