-- =============================================================================
-- 004_ref_tables.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Create the `mip.ref` reference-data schema and its lender
--            dictionary table. `mip.ref.lender_dictionary` is the authoritative
--            mapping from raw share lender strings (uppercase) to the
--            customer-facing display labels used by the PII-redaction layer
--            (`backend/services/pii_redaction.py`).
--
--            This promotes the inline ``_LENDER_REF_MAP`` Python dict (11
--            entries) to a governed UC table so product can grow the lender
--            vocabulary without a Python deploy + analysts can contribute
--            via SQL MERGE. The Python side keeps a copy of the same vocab
--            as a fallback when UC is unavailable (see resolver docstring).
--
-- Grain:     One row per distinct raw_key. raw_key is the uppercase share
--            lender string the resolver looks up; each raw_key MUST map to
--            exactly one display_name.
--
-- Posture:   CREATE ... IF NOT EXISTS. Idempotent; safe to run on every
--            `databricks bundle deploy`. The companion seed SQL
--            (`sql/ref/lender_dictionary_seed.sql`) uses MERGE so re-runs
--            do not duplicate rows.
--
-- Integration: Run as part of the `mip_ref_seed` bundle job (resources/
--            jobs.yml); scheduled to execute after catalog/schema init
--            (`001_catalogs_schemas.sql`) and BEFORE silver/gold pipelines
--            so downstream transformations that eventually join against
--            ref.lender_dictionary see a populated table.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS mip.ref
COMMENT 'Reference data / controlled vocabularies. Governed by product + analysts; consumed by backend PII redaction and future gold joins.';

CREATE TABLE IF NOT EXISTS mip.ref.lender_dictionary (
  raw_key      STRING    NOT NULL COMMENT 'Uppercase raw share lender string (from voluntary_lien or mortgage_events). PK.',
  display_name STRING    NOT NULL COMMENT 'Customer-facing polished label surfaced at the API boundary.',
  lender_type  STRING             COMMENT 'One of: bank, credit_union, non_bank_fintech, wholesale, other.',
  -- is_competitor: NOT NULL BOOLEAN with no Delta column DEFAULT -- that
  -- would require `delta.feature.allowColumnDefaults = supported`, which
  -- isn't on by default in every workspace and would add a per-client
  -- runtime knob. The seed MERGE supplies TRUE explicitly for every row
  -- (see sql/ref/lender_dictionary_seed.sql), so the default is redundant.
  is_competitor BOOLEAN             COMMENT 'FALSE iff this lender IS the tenant (e.g. SUMMIT MTG for Summit Mortgage); TRUE for every third-party servicer. Seeded via MERGE.',
  last_updated TIMESTAMP             COMMENT 'Last seed/MERGE timestamp. CURRENT_TIMESTAMP() at write.',
  source       STRING                COMMENT 'Provenance of the row: manual_seed | cotality_probe | analyst_contribution | backfill.',
  CONSTRAINT lender_dictionary_pk PRIMARY KEY (raw_key)
)
USING DELTA
COMMENT 'Authoritative raw->display mapping for share lender strings. See backend/services/pii_redaction.py::LenderRefResolver.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed'      = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
