-- R5-01 idempotency follow-up for mip_app.approvals.
--
-- Context: the outreach approve/reject routers return 503 on Lakebase
-- failure, and the frontend's ``_fetchWithRetry`` retries on 503. If the
-- INSERT succeeded server-side but the HTTP response was lost, the
-- retry wrote a second decision row (silent duplicate). This migration
-- adds a nullable ``request_id`` column, canonical decision binding,
-- exact first response, and a partial unique index so a
-- re-POSTed approve/reject with the same caller-supplied request_id
-- collides on the index and the router returns the existing decision
-- instead of inserting a duplicate.
--
-- The filter ``WHERE request_id IS NOT NULL`` keeps pre-R5-01 rows
-- (which don't carry a request_id) valid; only rows the upgraded
-- backend wrote are subject to the uniqueness constraint.
--
-- Idempotent -- safe to run on an already-migrated database. Exactly
-- the same shape the backend bootstrap path
-- (``backend/services/lakebase_bootstrap.py``) executes on first
-- approve/reject after deploy, so the mip_lakebase_migrate job and the
-- runtime bootstrap agree byte-for-byte.
--
-- Runs automatically as part of ``lakebase/schema.sql`` under the
-- mip_lakebase_migrate job; this standalone file exists so an SE can
-- apply the migration without a full schema reapply in a pinch.

ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS request_id TEXT;

ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS decision_intent TEXT;

ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS decision_payload_hash TEXT;

ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS decision_response JSONB;

ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS audit_event_id UUID;

CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_request_id
    ON mip_app.approvals (request_id) WHERE request_id IS NOT NULL;
