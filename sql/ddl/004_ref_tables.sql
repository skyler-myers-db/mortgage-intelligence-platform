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

-- ---------------------------------------------------------------------------
-- refresh_run_state
-- ---------------------------------------------------------------------------
-- Purpose:   Deterministic refresh-timestamp anchor. One row per
--            `mip_refresh_scores` run, captured once at the top of the
--            CTAS DAG; every downstream CTAS reads the value so all
--            `refreshed_at` / `snapshot_at` columns agree to the second
--            within one run. See sql/ddl/refresh_run_state.sql for the
--            full rationale (audit-holes-round-3 #7).
--
-- Grain:     One row per job run (append-only audit ledger).
-- Posture:   CREATE ... IF NOT EXISTS. Idempotent. Provisioned on every
--            `mip_ref_seed` deploy so the seed task in
--            `mip_refresh_scores` can INSERT into it without a manual
--            bootstrap step.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mip.ref.refresh_run_state (
  run_id       STRING             COMMENT 'Databricks job run_id (or ad-hoc marker). NULL tolerated for dev hand-runs.',
  refresh_at   TIMESTAMP NOT NULL COMMENT 'Deterministic refresh timestamp shared by every gold CTAS in the run.',
  captured_at  TIMESTAMP NOT NULL COMMENT 'When the seed row was written. Tiebreaker for MAX() when two rows share refresh_at.',
  source       STRING             COMMENT 'Provenance of the row: mip_refresh_scores | ad_hoc | backfill.'
)
USING DELTA
COMMENT 'Deterministic refresh-timestamp anchor. Consumed by every gold CTAS to replace per-task CURRENT_TIMESTAMP() drift.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed'       = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

-- ---------------------------------------------------------------------------
-- state_footprint
-- ---------------------------------------------------------------------------
-- Purpose:   State display metadata/fallback labels. The current data-bearing
--            coverage scope is discovered from mip.gold.county_rollup and
--            mip.gold.zip_rollup, not this table. These rows only enrich
--            live coverage with names/default metadata and support truthful
--            degraded UI chrome when gold coverage is unavailable.
--
-- Grain:     One row per 2-char USPS `state_code`. `is_default_state` is
--            optional single-state anchor metadata; user-facing pages default
--            to the dynamic "All N states" footprint option.
--
-- Posture:   CREATE ... IF NOT EXISTS. Idempotent; safe to run on every
--            bundle deploy. The companion seed SQL
--            (`sql/ref/state_footprint_seed.sql`) uses MERGE so re-runs
--            do not duplicate rows and refresh `last_updated` when an
--            attribute changes.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mip.ref.state_footprint (
  state_code       STRING    NOT NULL COMMENT '2-char USPS state code (uppercase). PK.',
  state_name       STRING    NOT NULL COMMENT 'Human-readable state name, e.g. "Illinois".',
  display_order    INT       NOT NULL COMMENT 'Sort order in UI lists (1 = first).',
  is_default_state BOOLEAN   NOT NULL COMMENT 'Optional single-state anchor metadata. Not a user-facing page default.',
  last_updated     TIMESTAMP          COMMENT 'CURRENT_TIMESTAMP() on seed/MERGE.',
  source           STRING             COMMENT 'Provenance: us_state_metadata_seed | analyst_contribution | tenant_override.',
  CONSTRAINT state_footprint_pk PRIMARY KEY (state_code)
)
USING DELTA
COMMENT 'US state display metadata for geography coverage. Not a data-bearing coverage whitelist.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed'      = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);

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
