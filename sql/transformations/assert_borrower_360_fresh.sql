-- =============================================================================
-- assert_borrower_360_fresh.sql  (mip_refresh_scores integrity gate)
-- -----------------------------------------------------------------------------
-- Purpose:   Halt the CTAS chain if `mip.gold.borrower_360` is either
--            empty or stale relative to the current run's `refresh_at`
--            seed. Downstream scoring, ranking, rollups, and dossier
--            builds MUST NOT fan out against yesterday's population.
--
-- Audit-holes-round-3 #8 fix: `ctas_lead_scores` + `ctas_lead_population`
-- + every geography rollup previously read
--
--     FROM mip.gold.borrower_360
--
-- unconditionally. If `ctas_borrower_360` failed partway (silver flake,
-- warehouse scaling hiccup, retry storm), the downstream CTAS would
-- happily score the STALE population from yesterday's last successful
-- run. No test caught the drift because every table was byte-valid on
-- its own. This task is the sentinel that fails the chain when the
-- upstream is older than the seed timestamp by more than the freshness
-- window.
--
-- Threshold: 30 minutes. Chosen conservatively to tolerate a long silver
-- refresh + a retry burst (each CTAS has max_retries, and the silver
-- refresh itself can be 10-15 minutes on a cold warehouse). If
-- borrower_360.refreshed_at lags the run's refresh_at by more than 30
-- minutes, it is from a prior run, not the one currently executing.
--
-- Behavior: raises a structured error via RAISE_ERROR with the computed
-- staleness (in minutes). The SQL task fails the assertion, Databricks
-- marks the task failed, and every downstream task that depends on this
-- sentinel (via `assert_borrower_360_fresh` instead of directly on
-- `ctas_borrower_360`) is skipped with UPSTREAM_FAILED.
--
-- Contract: every task that previously had
--
--     depends_on:
--       - task_key: ctas_borrower_360
--
-- now has
--
--     depends_on:
--       - task_key: assert_borrower_360_fresh
--
-- Keep this invariant in both `databricks.yml` and `resources/jobs.yml`.
-- `tests/unit/test_gold_ddl_contract.py::test_sentinel_wired_between_*`
-- pins the wiring so a future edit that bypasses the sentinel fails CI.
-- =============================================================================

WITH run_ref AS (
  SELECT refresh_at
    FROM mip.ref.refresh_run_state
   ORDER BY captured_at DESC
   LIMIT 1
),
target AS (
  SELECT MAX(refreshed_at) AS max_refreshed_at, COUNT(*) AS row_count
    FROM mip.gold.borrower_360
)
SELECT
  CASE
    WHEN t.row_count = 0 OR t.max_refreshed_at IS NULL
      THEN RAISE_ERROR('assert_borrower_360_fresh: mip.gold.borrower_360 is empty. Aborting downstream CTAS.')
    WHEN r.refresh_at IS NULL
      THEN RAISE_ERROR('assert_borrower_360_fresh: mip.ref.refresh_run_state has no rows. Seed task (capture_refresh_timestamp) must run first.')
    WHEN (UNIX_TIMESTAMP(r.refresh_at) - UNIX_TIMESTAMP(t.max_refreshed_at)) > 1800
      THEN RAISE_ERROR(
        CONCAT(
          'assert_borrower_360_fresh: mip.gold.borrower_360 is stale by ',
          CAST(((UNIX_TIMESTAMP(r.refresh_at) - UNIX_TIMESTAMP(t.max_refreshed_at)) / 60) AS INT),
          ' minutes vs run refresh_at. Aborting downstream CTAS.'
        )
      )
    ELSE 1
  END AS ok
FROM target AS t CROSS JOIN run_ref AS r;
