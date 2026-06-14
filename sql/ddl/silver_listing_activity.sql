-- =============================================================================
-- silver_listing_activity.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.listing_activity`, a CLIP-keyed lift of
--            Cotality MLS/Listings. This is the live source for
--            borrower_360.listed_for_sale, the listed segment, purchase NBO
--            branch, and listing evidence rows.
--
-- PII posture:
--            The Cotality MLS table contains street-address and remarks
--            fields. They are deliberately not landed here. Silver keeps only
--            CLIP, geography, standardized status, price, days-on-market,
--            source ids, and load timestamps needed for Module 0 evidence.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.listing_activity (
  clip                         STRING    NOT NULL COMMENT 'Cotality CLIP. Join key to silver.lien_current / gold.borrower_360.',
  situs_state                  STRING    NOT NULL COMMENT '2-char source state.',
  situs_zip_code               STRING             COMMENT '5-digit listing/property ZIP.',
  listing_record_id            STRING             COMMENT 'Composite listing id from Cotality. Kept for evidence trace, not exposed as PII.',
  listing_status_code          STRING             COMMENT 'Cotality standardized listing status code.',
  listing_status_category      STRING             COMMENT 'Cotality standardized status category. A=active, U=under contract/pending, S=sold, X=off-market.',
  listing_status_description   STRING             COMMENT 'Display-safe standardized status description.',
  listing_type                 STRING             COMMENT 'Listing type when supplied.',
  listing_date                 DATE               COMMENT 'Listing date.',
  listing_status_date          DATE               COMMENT 'Most recent status/change date used for latest-row selection.',
  listing_price                BIGINT             COMMENT 'Current listing price, USD.',
  original_listing_price       BIGINT             COMMENT 'Original listing price, USD.',
  days_on_market               INT                COMMENT 'Days on market from the standardized feed.',
  listing_service              STRING             COMMENT 'MLS/listing service abbreviation or name. No agent or consumer contact data.',
  is_current_listing           BOOLEAN   NOT NULL COMMENT 'TRUE when Cotality marks this as the most recent listing row for the CLIP.',
  is_active_listing            BOOLEAN   NOT NULL COMMENT 'TRUE for current active/under-contract rows (status category A/U).',
  source_updated_at            TIMESTAMP          COMMENT 'Cotality source updated_at timestamp.',
  ingest_ts                    TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp.',
  _meta_source_file_name       STRING             COMMENT 'Cotality source file path for refresh forensics.',
  _meta_batch_id               STRING             COMMENT 'Lakeflow / SQL backfill batch id.'
)
USING DELTA
CLUSTER BY (situs_state, clip)
COMMENT 'CLIP-keyed Cotality MLS/Listings lift. Raw street address, public remarks, agent/contact, and lockbox fields are intentionally excluded.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
