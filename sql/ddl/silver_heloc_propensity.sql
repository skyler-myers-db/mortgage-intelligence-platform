-- =============================================================================
-- silver_heloc_propensity.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.heloc_propensity`, a CLIP-keyed lift of the
--            Cotality HELOC propensity score feed. This is a model-score
--            signal, not a filed building-permit feed.
--
-- PII posture:
--            The source table includes owner and street-address fields. The
--            silver table lands only CLIP, geography, score, run date, and load
--            metadata. Do not use this table to claim a permit was filed.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.heloc_propensity (
  clip                         STRING    NOT NULL COMMENT 'Cotality CLIP. Join key to borrower_360.',
  situs_state                  STRING    NOT NULL COMMENT '2-char source state.',
  situs_zip_code               STRING             COMMENT '5-digit situs ZIP.',
  heloc_propensity_score       INT                COMMENT 'Cotality HELOC propensity score, 0..999 in the current feed.',
  heloc_propensity_run_date    DATE               COMMENT 'Cotality model run date.',
  source_updated_at            TIMESTAMP          COMMENT 'Cotality source load timestamp.',
  ingest_ts                    TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp.',
  _meta_source_file_name       STRING             COMMENT 'Cotality source file path for refresh forensics.',
  _meta_batch_id               STRING             COMMENT 'Lakeflow / SQL backfill batch id.'
)
USING DELTA
CLUSTER BY (situs_state, clip)
COMMENT 'CLIP-keyed Cotality HELOC propensity score lift. This is not a building-permit feed; raw names and addresses are excluded.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
