-- =============================================================================
-- audit_asset_lineage_view.sql
-- -----------------------------------------------------------------------------
-- Purpose:   The App's only read path to observed Unity Catalog lineage. The
--            asset-detail lineage section (backend/services/asset_metadata.py,
--            _load_lineage) reads mip.audit.asset_lineage and never the
--            system table: system.access.table_lineage holds lineage for every
--            table on the metastore, other teams' tables included, and App SQL
--            reads gold, ref and audit only.
--
-- Projection: the four columns the App reads. The acting identity
--            (created_by), job and statement ids, workspace and metastore ids
--            and storage paths stay in the system table.
--
-- Filter:    edges whose source or target is in this install catalog, the
--            only catalog the product's pipelines write. The Cotality share
--            catalog is not named: a Delta Share is read-only here, so no edge
--            can target it. An edge from the share into this catalog is kept
--            by its target; an edge from the share into another catalog is
--            another consumer's lineage. Time is not filtered here: the App's
--            query keeps its 90-day event_date window, in one place.
--
-- Security:  Unity Catalog evaluates a view with its OWNER's privileges on
--            what it reads. The owner (the identity that runs this file) needs
--            USE CATALOG on system, USE SCHEMA on system.access and SELECT on
--            system.access.table_lineage. The App needs SELECT on this view
--            only, beside its USE CATALOG and USE SCHEMA on the audit schema.
--
-- Deploy:    tools/databricks/ensure_asset_lineage_view.py renders this file
--            for the deploy catalog with tools/render_sql.py (the three-part
--            view name and the two catalog-name literals), probes the system
--            table, publishes the view and verifies its four columns;
--            scripts/deploy.sh then grants the App SELECT on it. A deployer
--            that cannot read the system table skips both with a warning and
--            the App keeps its explicit "Observed lineage unavailable" state.
--
-- Shape:     one CREATE OR REPLACE VIEW statement (idempotent), terminated by
--            the file's only semicolon outside these comment lines.
-- =============================================================================

CREATE OR REPLACE VIEW mip.audit.asset_lineage (
  source_table_full_name COMMENT 'Upstream table of one observed lineage event, as a three-part name.',
  target_table_full_name COMMENT 'Downstream table of one observed lineage event, as a three-part name.',
  event_time COMMENT 'When Unity Catalog observed the event.',
  event_date COMMENT 'Date of event_time. The App reads a 90-day window on it.'
)
COMMENT 'Observed lineage edges with a source or target in this install catalog, projected from system.access.table_lineage. Read by the App asset-detail lineage section and evaluated with the view owner privileges.'
AS
SELECT
  source_table_full_name,
  target_table_full_name,
  event_time,
  event_date
FROM system.access.table_lineage
WHERE source_table_catalog = 'mip'
   OR target_table_catalog = 'mip';
