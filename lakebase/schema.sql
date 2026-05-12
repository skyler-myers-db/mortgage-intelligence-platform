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
    suppression_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    message_variants JSONB NOT NULL DEFAULT '[]'::jsonb,
    channel_cascade JSONB NOT NULL DEFAULT '[]'::jsonb,
    send_window JSONB NOT NULL DEFAULT '{}'::jsonb,
    holdout JSONB,
    roi_assumptions JSONB,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS suppression_policy JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS message_variants JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS channel_cascade JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS send_window JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS holdout JSONB;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS roi_assumptions JSONB;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_campaigns_owner
    ON mip_app.campaigns (owner_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_campaigns_status
    ON mip_app.campaigns (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS mip_app.campaign_message_variants (
    campaign_id  UUID NOT NULL REFERENCES mip_app.campaigns(campaign_id) ON DELETE CASCADE,
    variant_name TEXT NOT NULL,
    channel      TEXT NOT NULL CHECK (channel IN ('email','sms','direct_mail')),
    subject      TEXT,
    body         TEXT NOT NULL CHECK (length(body) <= 5000),
    weight_pct   NUMERIC,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, variant_name, channel)
);

CREATE TABLE IF NOT EXISTS mip_app.tenant_disclosures (
    tenant_id           TEXT NOT NULL DEFAULT 'summit',
    state               TEXT NOT NULL,
    channel             TEXT NOT NULL CHECK (channel IN ('email','sms','direct_mail')),
    disclosure_version  TEXT NOT NULL,
    body                TEXT NOT NULL CHECK (length(body) BETWEEN 20 AND 2000),
    active              BOOLEAN NOT NULL DEFAULT true,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, state, channel, disclosure_version)
);
CREATE INDEX IF NOT EXISTS idx_tenant_disclosures_active
    ON mip_app.tenant_disclosures (tenant_id, state, channel, active, updated_at DESC);

-- Sales operations ----------------------------------------------------
-- Thin Module 0 work-management layer for the named Sales Manager
-- persona. These tables store internal staff identity and synthetic
-- borrower ids only. Borrower names, emails, phone numbers, street
-- addresses, and raw Cotality identifiers do not belong here.
CREATE TABLE IF NOT EXISTS mip_app.sales_team (
    email            TEXT PRIMARY KEY,
    display_label    TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'loan_officer'
                     CHECK (role IN ('loan_officer','sales_manager','admin')),
    manager_email    TEXT,
    region           TEXT,
    capacity_per_day INTEGER NOT NULL DEFAULT 35
                     CHECK (capacity_per_day BETWEEN 0 AND 250),
    active           BOOLEAN NOT NULL DEFAULT true,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sales_team_manager
    ON mip_app.sales_team (manager_email, active, display_label);

CREATE TABLE IF NOT EXISTS mip_app.lead_assignments (
    assignment_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id       TEXT NOT NULL,
    assigned_to_email TEXT NOT NULL REFERENCES mip_app.sales_team(email),
    assigned_by       TEXT NOT NULL,
    assigned_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ,
    released_at       TIMESTAMPTZ,
    strategy          TEXT NOT NULL DEFAULT 'manual'
                      CHECK (strategy IN ('manual','round_robin','score_balanced')),
    request_id        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_assignments_active_borrower
    ON mip_app.lead_assignments (borrower_id)
    WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_lead_assignments_assignee
    ON mip_app.lead_assignments (assigned_to_email, assigned_at DESC)
    WHERE released_at IS NULL;
DROP INDEX IF EXISTS mip_app.idx_lead_assignments_request_id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_assignments_request_borrower
    ON mip_app.lead_assignments (request_id, borrower_id)
    WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS mip_app.call_dispositions (
    disposition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id    TEXT NOT NULL,
    lo_email       TEXT NOT NULL REFERENCES mip_app.sales_team(email),
    outcome        TEXT NOT NULL CHECK (
        outcome IN (
            'called_no_answer','called_left_voicemail','connected',
            'callback_scheduled','application_started','not_interested',
            'not_now','dead'
        )
    ),
    attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    callback_at    TIMESTAMPTZ,
    notes          TEXT CHECK (notes IS NULL OR length(notes) <= 500),
    audit_event_id UUID,
    request_id     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_call_dispositions_borrower
    ON mip_app.call_dispositions (borrower_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_dispositions_lo
    ON mip_app.call_dispositions (lo_email, occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_dispositions_request_id
    ON mip_app.call_dispositions (request_id)
    WHERE request_id IS NOT NULL;

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

-- Saved workspace ----------------------------------------------------
-- Actor-scoped saved leads and draft outreach copy. These tables back
-- the in-app inbox/workspace; they intentionally store synthetic
-- borrower ids plus coarse location / offer metadata only. Raw names,
-- street addresses, emails, phone numbers, and raw Cotality CLIPs do
-- not belong here.
CREATE TABLE IF NOT EXISTS mip_app.saved_leads (
    actor_email       TEXT NOT NULL,
    borrower_id       TEXT NOT NULL,
    city              TEXT,
    state             TEXT,
    zip               TEXT,
    recommended_offer TEXT,
    opportunity_score INTEGER CHECK (opportunity_score BETWEEN 0 AND 100),
    confidence        INTEGER CHECK (confidence BETWEEN 0 AND 100),
    saved_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ,
    PRIMARY KEY (actor_email, borrower_id)
);
CREATE INDEX IF NOT EXISTS idx_saved_leads_actor_updated
    ON mip_app.saved_leads (actor_email, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS mip_app.outreach_drafts (
    actor_email  TEXT NOT NULL,
    borrower_id  TEXT NOT NULL,
    offer_code   TEXT,
    channel      TEXT NOT NULL DEFAULT 'email' CHECK (channel IN ('email','sms','direct_mail')),
    body         TEXT NOT NULL CHECK (length(body) <= 5000),
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','released')),
    saved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    PRIMARY KEY (actor_email, borrower_id, channel)
);
ALTER TABLE mip_app.outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_channel_check;
ALTER TABLE mip_app.outreach_drafts
    ADD CONSTRAINT outreach_drafts_channel_check CHECK (channel IN ('email','sms','direct_mail'));
CREATE INDEX IF NOT EXISTS idx_outreach_drafts_actor_updated
    ON mip_app.outreach_drafts (actor_email, updated_at DESC)
    WHERE deleted_at IS NULL;

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
-- Genie action idempotency: the server issues request ids inside the
-- HMAC confirmation token, and Lakebase enforces one audited mutation per
-- actor/request/event. The partial predicate keeps legacy non-Genie audit
-- rows append-only while giving governed Genie actions retry safety.
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_audit_genie_request_actor_event
    ON mip_app.action_audit (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND event_type LIKE 'GENIE_ACTION_%';
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_audit_genie_request_actor_event_v2
    ON mip_app.action_audit (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND left(event_type, 13) = 'GENIE_ACTION_';

-- Genie sessions ------------------------------------------------------
-- Durable state for Databricks Genie conversations. These tables store
-- conversation/message identifiers and proof metadata only; they do NOT
-- store raw user prompts or answer text, because those fields can contain
-- free-form PII-like content. The app can recover the latest conversation
-- after reload while the append-only action_audit ledger remains the
-- governed proof of each read/action.
CREATE TABLE IF NOT EXISTS mip_app.genie_sessions (
    actor_email        TEXT NOT NULL,
    conversation_id    TEXT NOT NULL,
    last_message_id    TEXT,
    last_question_hash TEXT,
    source             TEXT,
    trusted_assets     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_email, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_sessions_actor_updated
    ON mip_app.genie_sessions (actor_email, updated_at DESC);

CREATE TABLE IF NOT EXISTS mip_app.genie_messages (
    conversation_id    TEXT NOT NULL,
    message_id         TEXT NOT NULL,
    actor_email        TEXT NOT NULL,
    question_hash      TEXT NOT NULL,
    source             TEXT NOT NULL,
    row_count          INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    visualization_kind TEXT,
    trusted_assets     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    request_id         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_messages_actor_created
    ON mip_app.genie_messages (actor_email, created_at DESC);

-- Genie-governed cohorts ---------------------------------------------
-- One row per confirmed "open this cohort" action. The cohort stores
-- only reviewed filters and optional synthetic borrower ids from the
-- Genie result, never owner names, raw CLIPs, addresses, emails, or
-- phone numbers. Lead Queue replays this Lakebase row by cohort_id so
-- a governed Genie action cannot degrade into the generic lead queue.
CREATE TABLE IF NOT EXISTS mip_app.genie_cohorts (
    cohort_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email     TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    conversation_id TEXT,
    message_id      TEXT,
    question_hash   TEXT,
    route_filters   JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_assets   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    sql_hash        TEXT,
    row_count       INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (actor_email, request_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_cohorts_actor_created
    ON mip_app.genie_cohorts (actor_email, created_at DESC);

CREATE TABLE IF NOT EXISTS mip_app.genie_cohort_members (
    cohort_id   UUID NOT NULL REFERENCES mip_app.genie_cohorts(cohort_id) ON DELETE CASCADE,
    borrower_id TEXT NOT NULL,
    rank_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cohort_id, borrower_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_cohort_members_borrower
    ON mip_app.genie_cohort_members (borrower_id);

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
