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

-- ---------------------------------------------------------------------------
-- offer_rules_config
-- ---------------------------------------------------------------------------
-- Purpose:   Governed threshold vocabulary consumed by the Offer Orchestrator
--            decision tree (`fn_next_best_offer`), the In-the-Money flag
--            (`fn_in_the_money`), and the Admin surface (/api/admin/rules).
--            One row per tunable knob. Values mirror the defaults baked into
--            the UC functions' headers -- changing a row here does NOT retune
--            UC compute (the thresholds are passed as explicit args by the
--            application layer); the row is the single canonical source the
--            admin UI reads + the product of record for "what is the active
--            ruleset" on a given day.
--
-- Grain:     One row per knob `key`. `key` is the stable identifier the
--            backend uses (e.g. `mip_min_spread_bps`); labels / descriptions
--            are copy the admin UI renders directly.
--
-- Posture:   CREATE ... IF NOT EXISTS. Idempotent; safe to run on every
--            bundle deploy. The companion seed SQL
--            (`sql/ref/offer_rules_config_seed.sql`) MERGEs so re-runs do
--            not duplicate rows and do refresh `last_updated` when a value
--            changes.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mip.ref.offer_rules_config (
  key          STRING    NOT NULL COMMENT 'Stable knob id used by the backend, e.g. ''mip_min_spread_bps''. PK.',
  value        DOUBLE    NOT NULL COMMENT 'Numeric threshold value. bps knobs store an integer in DOUBLE; percentage knobs store the percent (15.0 = 15%); rates store the fractional form (0.04875 = 4.875%).',
  unit         STRING             COMMENT 'One of: bps | pct | rate_fraction. Informs display formatting.',
  label        STRING             COMMENT 'Human-readable label rendered in the admin UI threshold table.',
  description  STRING             COMMENT 'One-line description of what the knob controls.',
  sort_order   INT                COMMENT 'Display order in the admin UI (1 = first row).',
  last_updated TIMESTAMP          COMMENT 'CURRENT_TIMESTAMP() on seed/MERGE; changes when the row value changes.',
  CONSTRAINT offer_rules_config_pk PRIMARY KEY (key)
)
USING DELTA
COMMENT 'Governed threshold vocabulary for offer decisioning. Single source of truth for /api/admin/rules.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed'      = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
