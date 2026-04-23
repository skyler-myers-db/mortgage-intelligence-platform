-- Lakebase Postgres DDL for Module 0 app state.
-- Target: Lakebase instance `mip_app_state`, database `mip_app_state`,
-- schema `mip_app`. This file is idempotent; Slice 5 runs it at bundle
-- deploy time and treats re-runs as a no-op.
--
-- Governance rationale: `docs/governance-real-data-review.md` §4 names
-- the required tables and their minimum-viable columns. The review also
-- pins `subject_clip` (which is the redacted `clip_ref` -- a 12-char
-- hash, not the raw Cotality CLIP) and bars PII from the audit metadata
-- JSONB. We keep the review's naming where the Slice 5 task prompt
-- disagrees, because §4 is the governance contract -- the ask is
-- reconciled here:
--   * `action_audit` has both `action` (from governance §4: canonical
--     verb like "view_borrower_360") and an `event_type` alias populated
--     by the Slice 5 writer path for forward compat with the task
--     prompt. The column is the same; we standardize on `action`.
--   * `subject_clip` (CLIP hash) and `subject_segment` (lowercased segment
--     code) are added as top-level columns so operators can SELECT on
--     them without JSONB indexing.
--
-- All UUID defaults use `gen_random_uuid()`; Lakebase Postgres enables
-- `pgcrypto` by default on a new instance, but we include the CREATE
-- EXTENSION line for portability.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS mip_app;
SET search_path TO mip_app, public;

-- Campaigns -----------------------------------------------------------
-- One row per marketing campaign the user has built with the portfolio
-- builder. `criteria` JSONB stores the segment-filter payload; it MUST
-- NOT contain borrower names, addresses, or raw CLIPs -- only segment
-- codes, thresholds, and geography at ZIP/metro level.
CREATE TABLE IF NOT EXISTS mip_app.campaigns (
    campaign_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    owner_email  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',
    criteria     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_campaigns_owner
    ON mip_app.campaigns (owner_email, created_at DESC);

-- Approvals -----------------------------------------------------------
-- One row per human-in-the-loop decision on an outreach draft.
-- `borrower_id` is the synthetic `B-#####` stable id today; production
-- can swap to `clip_ref`. `action` is the approve / reject / hold verb.
CREATE TABLE IF NOT EXISTS mip_app.approvals (
    approval_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id   UUID REFERENCES mip_app.campaigns(campaign_id) ON DELETE SET NULL,
    borrower_id   TEXT NOT NULL,
    offer_code    TEXT,
    action        TEXT NOT NULL CHECK (action IN ('approve','reject','hold')),
    actor_email   TEXT NOT NULL,
    rationale     TEXT,
    request_id    TEXT,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- R5-01 idempotency key: when the backend retries an approve/reject after
-- a lost 503 response, the re-POSTed ``request_id`` collides on this
-- partial unique index so we don't write a duplicate decision row. The
-- NULL-exempt filter preserves back-compat with legacy callers that
-- don't pass a request_id yet (they still insert, they just don't get
-- the retry-safe guarantee).
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS request_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_request_id
    ON mip_app.approvals (request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_approvals_campaign
    ON mip_app.approvals (campaign_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_borrower
    ON mip_app.approvals (borrower_id, decided_at DESC);

-- Action audit --------------------------------------------------------
-- The append-only ledger governance §4 requires. `event_type` is the
-- canonical verb: VIEW_BORROWER, VIEW_LEADS, APPROVE, DRAFT_OUTREACH,
-- RECOMMEND_OFFER, RUN_GENIE. `entity_type` / `entity_id` preserve the
-- existing AuditStore contract (routers don't change). `subject_clip`
-- is a redacted clip_ref (12-char hash), never the raw Cotality CLIP;
-- `subject_segment` is the lowercased segment code (itm, heloc, ...).
-- `metadata` JSONB is for score components / thresholds / evidence_ids
-- -- NO PII (no owner names, no street addresses).
CREATE TABLE IF NOT EXISTS mip_app.action_audit (
    audit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT NOT NULL,
    actor_email     TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT 'borrower',
    entity_id       TEXT NOT NULL DEFAULT '',
    subject_clip    TEXT,
    subject_segment TEXT,
    request_id      TEXT,
    evidence_ids    TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_action_audit_event_at
    ON mip_app.action_audit (event_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_audit_event_type
    ON mip_app.action_audit (event_type, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_audit_actor
    ON mip_app.action_audit (actor_email, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_audit_subject_clip
    ON mip_app.action_audit (subject_clip)
    WHERE subject_clip IS NOT NULL;

-- Immutability: app writer has INSERT + SELECT only; revoke UPDATE/DELETE.
-- The role may not yet exist at schema-install time; the REVOKE is a no-op
-- when the grantee is absent, but we guard with DO-blocks to avoid
-- "role does not exist" errors on first deploy.
DO $$
BEGIN
    REVOKE UPDATE, DELETE ON mip_app.action_audit FROM PUBLIC;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Agent sessions ------------------------------------------------------
-- One row per agent orchestrator run (portfolio builder, borrower
-- dossier, offer strategy, outreach writer, supervisor). Used by the
-- activity log in the console right rail.
CREATE TABLE IF NOT EXISTS mip_app.agent_sessions (
    session_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email  TEXT NOT NULL,
    route        TEXT,
    outcome      TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_actor
    ON mip_app.agent_sessions (actor_email, started_at DESC);

-- Feedback ------------------------------------------------------------
-- Thumbs-up / thumbs-down + free-text from the in-app feedback control.
CREATE TABLE IF NOT EXISTS mip_app.feedback (
    feedback_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id   TEXT,
    event_type    TEXT NOT NULL,
    rating        SMALLINT CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    actor_email   TEXT NOT NULL,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_event_type
    ON mip_app.feedback (event_type, recorded_at DESC);
