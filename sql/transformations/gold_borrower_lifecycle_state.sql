-- =============================================================================
-- gold_borrower_lifecycle_state.sql  (transformation — sync target)
-- -----------------------------------------------------------------------------
-- Purpose:   Provide a UC-native surface (`mip.gold.borrower_lifecycle_state`)
--            over Lakebase approval + outreach state, keyed by borrower_id.
--            Metric views (segment_performance_metric_view,
--            lead_generation_metric_view) JOIN this to expose approval_rate
--            and outreach_rate without a runtime federated join from a view.
--
-- Grain:     Zero or one row per borrower_id with durable lifecycle activity.
--            Lakebase is authoritative; jobs/sync_lifecycle_state.py applies
--            a sparse, event-triggered MERGE.
--
-- TODO (when UC foreign-catalog federation over Lakebase Postgres lands):
--            Replace the body of this file with a CREATE OR REPLACE TABLE …
--            AS SELECT that reads directly from the UC foreign catalog that
--            maps to the mip-app-state Postgres instance. The column contract
--            on mip.gold.borrower_lifecycle_state does not change — only the
--            source. See docs/validation/metric-views.md for the rollover
--            plan.
--
-- Today's posture:
--   * The schema is declared in sql/ddl/003_gold_tables.sql (§7).
--   * Population is done by jobs/sync_lifecycle_state.py or the cheaper app
--     warehouse hook. Both read durable Lakebase state and MERGE only those
--     borrowers into this table.
--   * This file is an explicit operator cleanup for workspaces upgraded from
--     the retired full-universe seed. Routine app and repair-job syncs do not
--     execute it. The equivalent guarded job action requires
--     `--prune-legacy-defaults`.
--   * It removes only untouched synthetic default rows; real decisions,
--     outreach, offers, and timestamps are preserved.
--
-- Run-order: after `refresh_scores` lands borrower_360, before
--            `refresh_segments` / dashboards consume the metric views.
-- Data contract: metric views treat a missing borrower row as
--                approval_status='pending' / outreach_status='none'. Joins
--                LEFT JOIN + COALESCE so a borrower not yet reviewed counts
--                correctly in the denominator.
--
-- Never add this DELETE to routine deploy, app, or repair-job execution. The
-- multi-million-row cleanup requires explicit operator intent.
-- =============================================================================

DELETE FROM mip.gold.borrower_lifecycle_state
WHERE approval_status = 'pending'
  AND outreach_status = 'none'
  AND offer_code IS NULL
  AND approved_at IS NULL
  AND approval_decided_at IS NULL
  AND approval_event_id IS NULL
  AND outreach_at IS NULL
  AND outreach_created_at IS NULL
  AND outreach_event_id IS NULL;
