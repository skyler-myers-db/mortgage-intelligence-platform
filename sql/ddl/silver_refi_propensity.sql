-- =============================================================================
-- silver_refi_propensity.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.refi_propensity`, a CLIP-keyed lift of the
--            Cotality refinance propensity score feed. It supplements the
--            deterministic in-the-money math without replacing it.
--
-- PII posture:
--            The source table includes owner and street-address fields. The
--            silver table lands only CLIP, geography, score, run date, and load
--            metadata.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.refi_propensity (
  clip                         STRING    NOT NULL COMMENT 'Cotality CLIP. Join key to borrower_360.',
  situs_state                  STRING    NOT NULL COMMENT '2-char source state.',
  situs_zip_code               STRING             COMMENT '5-digit situs ZIP.',
  refi_propensity_score        INT                COMMENT 'Cotality refinance propensity score, 0..999 in the current feed.',
  refi_propensity_run_date     DATE               COMMENT 'Cotality model run date.',
  source_updated_at            TIMESTAMP          COMMENT 'Cotality source load timestamp.',
  ingest_ts                    TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp.',
  _meta_source_file_name       STRING             COMMENT 'Cotality source file path for refresh forensics.',
  _meta_batch_id               STRING             COMMENT 'Lakeflow / SQL backfill batch id.'
)
USING DELTA
CLUSTER BY (situs_state, clip)
COMMENT 'CLIP-keyed Cotality refinance propensity score lift. Deterministic rate/equity rules remain the canonical ITM and NBO primitives.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
