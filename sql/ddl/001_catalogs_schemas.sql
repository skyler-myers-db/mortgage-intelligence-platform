-- =============================================================================
-- 001_catalogs_schemas.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent Unity Catalog bootstrap for Module 0. Creates the
--            `mip_demo` catalog and the six schemas that the Module 0 data
--            contract (docs/data-contract-module0.md) names:
--
--                mip_demo.raw        -- untyped lift from the Cotality share
--                mip_demo.silver     -- 1:1 typed + state-filtered from raw
--                mip_demo.gold       -- precomputed demo surfaces
--                mip_demo.semantics  -- Genie-facing metric views
--                mip_demo.app        -- app-runtime scratch / lookups
--                mip_demo.audit      -- audit tables (mirrors Lakebase audit)
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
--            in Slice 4. Per the mock/live seam in
--            docs/module0-real-data-plan.md, no app path reads from these
--            schemas until Slice 4 flips MIP_MOCK_MODE=false, so a delayed
--            RBAC pass does not leak data.
--
-- Isolation: The catalog is created with default (OPEN) isolation mode.
--            A production deploy will flip to ISOLATED so metastore-level
--            access does not leak into this demo workspace; we do not do
--            that at booth-deploy time because (a) the demo workspace is
--            single-tenant, and (b) ISOLATED mode requires workspace
--            binding calls that are not part of the bundle contract.
--
-- Idempotency: Every statement is CREATE ... IF NOT EXISTS. Re-running
--            this script has zero side effects on existing data.
-- =============================================================================

CREATE CATALOG IF NOT EXISTS mip_demo
COMMENT 'Mortgage Intelligence Platform - Module 0 demo catalog. See docs/data-contract-module0.md.';

CREATE SCHEMA IF NOT EXISTS mip_demo.raw
COMMENT 'Untyped lift from the Cotality Delta Share (read-only pass-through).';

CREATE SCHEMA IF NOT EXISTS mip_demo.silver
COMMENT '1:1 typed + state-filtered tables. Includes market_rates_weekly from FRED.';

CREATE SCHEMA IF NOT EXISTS mip_demo.gold
COMMENT 'Precomputed demo surfaces: borrower_360, lead_scores, evidence_events, lead_population.';

CREATE SCHEMA IF NOT EXISTS mip_demo.semantics
COMMENT 'Genie-facing metric views (lead_generation, segment_performance, borrower_opportunity).';

CREATE SCHEMA IF NOT EXISTS mip_demo.app
COMMENT 'App-runtime scratch + lookup tables that do not belong in Lakebase.';

CREATE SCHEMA IF NOT EXISTS mip_demo.audit
COMMENT 'Warehouse-side audit sink (mirrors the Lakebase audit_events tables for long-retention analysis).';
