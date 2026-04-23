-- =============================================================================
-- capture_refresh_timestamp.sql  (mip_refresh_scores seed task)
-- -----------------------------------------------------------------------------
-- Purpose:   Capture ONE deterministic `refresh_at` value at the top of the
--            `mip_refresh_scores` DAG, then let every downstream CTAS read
--            it. Eliminates per-task CURRENT_TIMESTAMP() drift across the
--            9 gold CTAS outputs.
--
-- Audit-holes-round-3 #7 fix: before this task, `gold_borrower_360.sql`,
-- `gold_lead_scores.sql`, `gold_lead_population.sql`,
-- `gold_segment_population.sql`, `gold_lockin_cohort.sql`,
-- `gold_borrower_dossier.sql`, `gold_county_rollup.sql`,
-- `gold_zip_rollup.sql`, `gold_state_top_segment.sql`, and both
-- `gold_funnel_snapshot_daily.sql` projections each invoked
-- CURRENT_TIMESTAMP() independently. Within a single run, two "Refreshed
-- ..." chips rendered from different tables could disagree by several
-- seconds -- enough to make parity tests flaky.
--
-- Contract: The seed INSERT below is the ONE place CURRENT_TIMESTAMP() is
-- allowed to live on the refresh path. Every other gold CTAS reads
--
--     (SELECT refresh_at FROM mip.ref.refresh_run_state
--         ORDER BY captured_at DESC LIMIT 1)
--
-- Pattern: INSERT one new row per run rather than UPDATE/MERGE because the
-- table is append-only audit. `max_concurrent_runs: 1` on the job means
-- the most recent row (by captured_at) is always the authoritative value
-- for the CTAS chain currently executing.
--
-- Scope: Runs before `ctas_borrower_360` and every sibling CTAS.
-- Downstream sentinel (`assert_borrower_360_fresh.sql`) verifies the
-- timestamp is fresh before scoring the population.
-- =============================================================================

-- Self-contained: ensure the schema + table exist even when `mip_ref_seed`
-- has not run yet (fresh workspace bootstrap, or a dev triggering
-- mip_refresh_scores directly). Idempotent; a no-op once 004_ref_tables.sql
-- has been applied.
CREATE SCHEMA IF NOT EXISTS mip.ref;

CREATE TABLE IF NOT EXISTS mip.ref.refresh_run_state (
  run_id       STRING             COMMENT 'Databricks job run_id (or ad-hoc marker). NULL tolerated for dev hand-runs.',
  refresh_at   TIMESTAMP NOT NULL COMMENT 'Deterministic refresh timestamp shared by every gold CTAS in the run.',
  captured_at  TIMESTAMP NOT NULL COMMENT 'When the seed row was written. Tiebreaker for MAX() when two rows share refresh_at.',
  source       STRING             COMMENT 'Provenance of the row: mip_refresh_scores | ad_hoc | backfill.'
)
USING DELTA;

INSERT INTO mip.ref.refresh_run_state (run_id, refresh_at, captured_at, source)
VALUES (
  NULL,                  -- run_id binding deferred; SQL-task context has no cheap way to read it.
                         -- `source` + captured_at ORDER BY give enough provenance for the current contract.
  CURRENT_TIMESTAMP(),
  CURRENT_TIMESTAMP(),
  'mip_refresh_scores'
);
