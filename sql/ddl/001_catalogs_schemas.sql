-- =============================================================================
-- 001_catalogs_schemas.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent Unity Catalog bootstrap for Module 0. Creates the
--            `mip` catalog and the seven schemas that the Module 0 data
--            contract (docs/data-contract-module0.md) names:
--
--                mip.raw        -- untyped lift from the Cotality share
--                mip.silver     -- 1:1 typed lift from raw/source shares
--                mip.first_party -- optional customer-owned lender feeds
--                mip.gold       -- precomputed product surfaces
--                mip.semantics  -- Genie-facing metric views
--                mip.app        -- app-runtime scratch / lookups
--                mip.audit      -- audit tables (mirrors Lakebase audit)
--
-- Posture:   CREATE ... IF NOT EXISTS everywhere. Safe to re-run on every
--            job invocation and every `databricks bundle deploy`. This
--            script MUST come before any silver/gold DDL in the Lakeflow
--            job graph so downstream tables land in valid namespaces.
--
-- Owner & permissions:
--            Default Databricks UC ownership rules apply (the principal
--            that runs this SQL becomes the catalog/schema owner). No
--            explicit GRANT / REVOKE statements are issued here --
--            governance-security-reviewer lands the RBAC matrix
--            (public-read on semantics, app-role-write on audit, etc.)
--            in the implementation guide. Runtime app paths read live
--            Unity Catalog/Lakebase data or fail visibly; no mock-runtime
--            switch is created by this bootstrap.
--
-- Isolation: The catalog is created with default (OPEN) isolation mode.
--            A production tenant deploy will flip to ISOLATED so
--            metastore-level access does not leak into the workspace; we
--            do not do that in the default bundle because (a) the default
--            workspace is single-tenant, and (b) ISOLATED mode requires
--            workspace binding calls that are not part of the bundle
--            contract.
--
-- Idempotency: Every statement is CREATE ... IF NOT EXISTS. Re-running
--            this script has zero side effects on existing data.
-- =============================================================================

CREATE CATALOG IF NOT EXISTS mip
COMMENT 'Mortgage Intelligence Platform - Module 0 catalog. See docs/data-contract-module0.md.';

CREATE SCHEMA IF NOT EXISTS mip.raw
COMMENT 'Untyped lift from the Cotality Delta Share (read-only pass-through).';

CREATE SCHEMA IF NOT EXISTS mip.silver
COMMENT '1:1 typed source-lift tables. Includes market_rates_weekly from FRED.';

CREATE SCHEMA IF NOT EXISTS mip.first_party
COMMENT 'Optional customer-owned LOS, servicing, CRM, interaction, and product-balance feeds. Empty until customer ingestion is configured.';

CREATE SCHEMA IF NOT EXISTS mip.gold
COMMENT 'Precomputed product surfaces: borrower_360, lead_scores, evidence_events, lead_population.';

CREATE SCHEMA IF NOT EXISTS mip.semantics
COMMENT 'Genie-facing metric views (lead_generation, segment_performance, borrower_opportunity).';

CREATE SCHEMA IF NOT EXISTS mip.app
COMMENT 'App-runtime scratch + lookup tables that do not belong in Lakebase.';

CREATE SCHEMA IF NOT EXISTS mip.audit
COMMENT 'Warehouse-side audit sink (mirrors the Lakebase audit_events tables for long-retention analysis).';

-- Optional first-party source contracts ---------------------------------------
-- These tables are intentionally PII-light. They provide a governed landing
-- contract for the customer's own systems when a lender connects LOS,
-- servicing, CRM/campaign, call-center/digital, and banking-product feeds.
-- In the evaluation workspace they may be populated by the explicit
-- `demo_synthetic` seed task. That path is a real UC feed used to exercise
-- the lender-ingestion experience, but it must remain labeled as synthetic
-- demo data in source readiness and public demos.

CREATE TABLE IF NOT EXISTS mip.first_party.loan_applications (
  application_id_hash STRING NOT NULL COMMENT 'Customer application id hash. No raw application id.',
  customer_key_hash   STRING          COMMENT 'Customer or household hash supplied by the lender.',
  borrower_id         STRING          COMMENT 'Synthetic MIP borrower id after governed resolution, when available.',
  clip_ref            STRING          COMMENT 'Masked CLIP ref or null until Cotality resolution.',
  state               STRING,
  zip                 STRING,
  application_status  STRING,
  application_channel STRING,
  product_intent      STRING,
  application_at      TIMESTAMP,
  source_system       STRING          COMMENT 'Customer source system name, e.g. LOS vendor or demo seed.',
  feed_mode           STRING          COMMENT 'customer_connected or demo_synthetic.',
  synthetic_demo      BOOLEAN         COMMENT 'TRUE only for the Summit Mortgage public demo seed.',
  refreshed_at        TIMESTAMP
)
USING DELTA
COMMENT 'Optional lender LOS/application feed. No names, emails, phones, SSNs, or street addresses.';

CREATE TABLE IF NOT EXISTS mip.first_party.servicing_portfolio (
  servicing_loan_id_hash STRING NOT NULL COMMENT 'Customer loan id hash. No raw account number.',
  customer_key_hash      STRING,
  borrower_id            STRING,
  clip_ref               STRING,
  state                  STRING,
  zip                    STRING,
  product_type           STRING,
  current_upb            DOUBLE,
  note_rate_pct          DOUBLE,
  delinquency_bucket     STRING,
  servicing_status       STRING,
  source_system          STRING COMMENT 'Customer source system name, e.g. servicing platform or demo seed.',
  feed_mode              STRING COMMENT 'customer_connected or demo_synthetic.',
  synthetic_demo         BOOLEAN COMMENT 'TRUE only for the Summit Mortgage public demo seed.',
  refreshed_at           TIMESTAMP
)
USING DELTA
COMMENT 'Optional lender servicing-book feed. Used for current-customer, retention, and recapture context when connected.';

CREATE TABLE IF NOT EXISTS mip.first_party.crm_campaign_membership (
  campaign_member_id_hash STRING NOT NULL,
  customer_key_hash       STRING,
  borrower_id             STRING,
  campaign_key_hash       STRING,
  channel                 STRING,
  last_touch_at           TIMESTAMP,
  suppression_reason      STRING,
  consent_status          STRING,
  source_system           STRING COMMENT 'Customer source system name, e.g. CRM or demo seed.',
  feed_mode               STRING COMMENT 'customer_connected or demo_synthetic.',
  synthetic_demo          BOOLEAN COMMENT 'TRUE only for the Summit Mortgage public demo seed.',
  refreshed_at            TIMESTAMP
)
USING DELTA
COMMENT 'Optional CRM/campaign feed for suppression, recency, and outreach-history controls. PII-free.';

CREATE TABLE IF NOT EXISTS mip.first_party.customer_interactions (
  interaction_id_hash STRING NOT NULL,
  customer_key_hash   STRING,
  borrower_id         STRING,
  interaction_channel STRING,
  interaction_type    STRING,
  outcome_code        STRING,
  interaction_at      TIMESTAMP,
  source_system       STRING COMMENT 'Customer source system name, e.g. contact-center or demo seed.',
  feed_mode           STRING COMMENT 'customer_connected or demo_synthetic.',
  synthetic_demo      BOOLEAN COMMENT 'TRUE only for the Summit Mortgage public demo seed.',
  refreshed_at        TIMESTAMP
)
USING DELTA
COMMENT 'Optional call-center/digital interaction feed. PII-free interaction metadata only.';

CREATE TABLE IF NOT EXISTS mip.first_party.product_balances (
  product_balance_id_hash STRING NOT NULL,
  customer_key_hash       STRING,
  borrower_id             STRING,
  product_family          STRING,
  balance_band            STRING,
  relationship_tenure_months INT,
  source_system           STRING COMMENT 'Customer source system name, e.g. core banking or demo seed.',
  feed_mode               STRING COMMENT 'customer_connected or demo_synthetic.',
  synthetic_demo          BOOLEAN COMMENT 'TRUE only for the Summit Mortgage public demo seed.',
  refreshed_at            TIMESTAMP
)
USING DELTA
COMMENT 'Optional banking-product balance feed. Uses bands and hashes, not account numbers or balances requiring PII treatment.';
