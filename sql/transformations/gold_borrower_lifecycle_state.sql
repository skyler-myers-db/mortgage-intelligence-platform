-- =============================================================================
-- gold_borrower_lifecycle_state.sql  (transformation — sync target)
-- -----------------------------------------------------------------------------
-- Purpose:   Provide a UC-native surface (`mip.gold.borrower_lifecycle_state`)
--            over Lakebase approval + outreach state, keyed by borrower_id.
--            Metric views (segment_performance_metric_view,
--            lead_generation_metric_view) JOIN this to expose approval_rate
--            and outreach_rate without a runtime federated join from a view.
--
-- Grain:     One row per borrower_id. Lakebase is authoritative; this table
--            is a scheduled mirror refreshed by jobs/sync_lifecycle_state.py.
--
-- TODO (when UC foreign-catalog federation over Lakebase Postgres lands):
--            Replace the body of this file with a CREATE OR REPLACE TABLE …
--            AS SELECT that reads directly from the UC foreign catalog that
--            maps to the mip-app-state Postgres instance. The column contract
--            on mip.gold.borrower_lifecycle_state does not change — only the
--            source. See docs/validation/metric-views.md for the rollover
--            plan.
--
-- Today's posture (slice13-accuracy):
--   * The schema is declared in sql/ddl/003_gold_tables.sql (§7).
--   * Population is done by jobs/sync_lifecycle_state.py, which opens a
--     psycopg3 connection to Lakebase (same auth path as lakebase_migrate.py),
--     materializes the per-borrower latest state, and writes the gold table
--     via the Databricks SQL warehouse (spark.sql / dbutils / dbsql-
--     statements). Idempotent: the job does a full CREATE OR REPLACE rewrite
--     of the gold table (~10k rows at Module 0 scale — a rewrite is cheaper
--     than a MERGE).
--   * This .sql file is the *manual fallback* for operators who want to wipe
--     the table back to a known-empty state (all borrowers pending / no
--     outreach) without running the Lakebase sync. That is the behavior we
--     want on a FIRST deploy before any approvals have been made — the
--     dashboards then render approval_rate = 0 / outreach_rate = 0 instead
--     of erroring on a missing table.
--
-- Run-order: after `refresh_scores` lands borrower_360, before
--            `refresh_segments` / dashboards consume the metric views.
-- Data contract: metric views treat a missing borrower row as
--                approval_status='pending' / outreach_status='none'. Joins
--                LEFT JOIN + COALESCE so a borrower not yet reviewed counts
--                correctly in the denominator.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.borrower_lifecycle_state AS
SELECT
  borrower_id,
  CAST('pending' AS STRING)   AS approval_status,
  CAST('none'    AS STRING)   AS outreach_status,
  CAST(NULL AS STRING)        AS offer_code,
  CAST(NULL AS TIMESTAMP)     AS approved_at,
  CAST(NULL AS TIMESTAMP)     AS outreach_at,
  CURRENT_TIMESTAMP()         AS synced_at
FROM mip.gold.borrower_360;
