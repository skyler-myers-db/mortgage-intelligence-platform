-- =============================================================================
-- refresh_run_state.sql  (gold-refresh deterministic timestamp anchor)
-- -----------------------------------------------------------------------------
-- Purpose:   Provide a single, deterministic `refresh_at` value that every
--            `mip_refresh_scores` CTAS task can read, so all `refreshed_at`
--            / `snapshot_at` columns across the 9 gold CTAS outputs agree
--            to the second within one job run.
--
-- Problem solved (audit-holes-round-3 #7):
--   Before this table, nine CTAS files each called `CURRENT_TIMESTAMP()`
--   independently. Two "Refreshed ..." chips rendered from different gold
--   tables could disagree by several seconds within the same refresh --
--   enough to make the parity test suite flaky and the Cotality Fresh chip
--   look inconsistent.
--
--   The fix is a one-row-per-run ledger. The seed task
--   (`capture_refresh_timestamp`) inserts a single `(run_id, refresh_at)`
--   row at the top of the DAG; every downstream CTAS reads
--   `MAX(refresh_at)` via a `latest_refresh_at()` SQL function. One
--   CURRENT_TIMESTAMP() call, captured once, referenced N times.
--
-- Grain:     One row per job run. Rows are append-only; the most recent
--            row by `captured_at` is the authoritative value for the run
--            currently executing, since `max_concurrent_runs: 1` on the
--            job guarantees no overlap.
--
-- Posture:   CREATE TABLE IF NOT EXISTS (idempotent). The companion seed
--            statement (see sql/transformations/capture_refresh_timestamp.sql)
--            INSERTs one row per invocation.
--
-- Integration: Provisioned by `mip_ref_seed` on first deploy, then
--            referenced by the seed task in `mip_refresh_scores`. Reader
--            pattern:
--
--                (SELECT refresh_at
--                   FROM mip.ref.refresh_run_state
--                  ORDER BY captured_at DESC
--                  LIMIT 1)
--
--            That subquery replaces every `CURRENT_TIMESTAMP()` across
--            gold CTAS outputs.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS mip.ref
COMMENT 'Reference data / controlled vocabularies. Governed by product + analysts; consumed by backend PII redaction and the deterministic refresh-timestamp anchor.';

CREATE TABLE IF NOT EXISTS mip.ref.refresh_run_state (
  run_id       STRING             COMMENT 'Databricks job run_id (or ad-hoc marker) that produced the row. NULL tolerated for dev hand-runs.',
  refresh_at   TIMESTAMP NOT NULL COMMENT 'Deterministic refresh timestamp shared by every gold CTAS in the run. Captured once at the top of the DAG.',
  captured_at  TIMESTAMP NOT NULL COMMENT 'When the seed row was written (CURRENT_TIMESTAMP at INSERT). Used as the tiebreaker for MAX() when two rows share refresh_at.',
  source       STRING             COMMENT 'Provenance of the row: mip_refresh_scores | ad_hoc | backfill.'
)
USING DELTA
COMMENT 'Deterministic refresh-timestamp anchor. See capture_refresh_timestamp.sql for the seed task.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed'       = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
