"""Migration SQL for the runtime Lakebase bootstrap.

Single responsibility: hold the small, retry-safe DDL statements the app
can apply between deploys, the read-only preflight queries that prove a
migration is already present, and the advisory-lock key each migration
owns. Nothing here executes -- these are constants, so the DDL text stays
reviewable in one place and diffable against ``lakebase/schema.sql``.

``backend.services.lakebase_bootstrap`` re-exports the DDL tuples, so
existing import sites keep working unchanged.
"""
from __future__ import annotations

# R5-01 DDL -- matches sql/ddl/lakebase_add_request_id.sql. Keep the two
# in sync; the SE-facing standalone file exists so operators can apply
# the migration without re-running the whole schema.sql.
_APPROVAL_REQUEST_ID_DDL: tuple[str, ...] = (
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS request_id TEXT",
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS decision_intent TEXT",
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS decision_payload_hash TEXT",
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS decision_response JSONB",
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS audit_event_id UUID",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_request_id "
        "ON mip_app.approvals (request_id) WHERE request_id IS NOT NULL"
    ),
)
_APPROVAL_REQUEST_ID_PREFLIGHT_SQL = """
SELECT
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'approvals'
      AND column_name = 'request_id'
  ) AS has_request_id_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'approvals'
      AND column_name = 'decision_intent'
  ) AS has_decision_intent_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'approvals'
      AND column_name = 'decision_payload_hash'
  ) AS has_decision_payload_hash_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'approvals'
      AND column_name = 'decision_response'
  ) AS has_decision_response_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'approvals'
      AND column_name = 'audit_event_id'
  ) AS has_audit_event_id_column,
  EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'mip_app'
      AND tablename = 'approvals'
      AND indexname = 'idx_approvals_request_id'
  ) AS has_request_id_index
"""

# R6-04 advisory-lock key -- deterministic 64-bit integer derived from a
# migration-specific string so we don't collide with other advisory locks
# the customer might run. ``pg_advisory_lock`` takes a ``bigint`` key; we
# let Postgres hash the stable migration name via ``hashtext(...)``.
# Document additional bootstraps here as they're added so the lock-key
# namespace stays auditable.
_APPROVAL_REQUEST_ID_KEY: str = "mip_bootstrap_approvals_request_id"
_SALES_WORKFLOW_REQUEST_ID_DDL: tuple[str, ...] = (
    "ALTER TABLE mip_app.lead_assignments ADD COLUMN IF NOT EXISTS request_id TEXT",
    "ALTER TABLE mip_app.lead_assignments ADD COLUMN IF NOT EXISTS assignment_scope TEXT NOT NULL DEFAULT 'single'",
    """
    UPDATE mip_app.lead_assignments
    SET assignment_scope = 'distribution'
    WHERE request_id IS NOT NULL
      AND request_id IN (
          SELECT request_id
          FROM mip_app.lead_assignments
          WHERE request_id IS NOT NULL
          GROUP BY request_id
          HAVING COUNT(*) > 1
      )
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_lead_assignments_assignment_scope'
              AND conrelid = 'mip_app.lead_assignments'::regclass
        ) THEN
            ALTER TABLE mip_app.lead_assignments
                ADD CONSTRAINT ck_lead_assignments_assignment_scope
                CHECK (assignment_scope IN ('single','distribution'));
        END IF;
    END $$
    """,
    "DROP INDEX IF EXISTS mip_app.idx_lead_assignments_request_id",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_assignments_request_borrower "
        "ON mip_app.lead_assignments (request_id, borrower_id) WHERE request_id IS NOT NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_assignments_single_request_id "
        "ON mip_app.lead_assignments (request_id) "
        "WHERE request_id IS NOT NULL AND assignment_scope = 'single'"
    ),
    "ALTER TABLE mip_app.call_dispositions ADD COLUMN IF NOT EXISTS request_id TEXT",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_call_dispositions_request_id "
        "ON mip_app.call_dispositions (request_id) WHERE request_id IS NOT NULL"
    ),
)
_SALES_WORKFLOW_REQUEST_ID_PREFLIGHT_SQL = """
SELECT
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'lead_assignments'
      AND column_name = 'request_id'
  ) AS has_assignment_request_id_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'lead_assignments'
      AND column_name = 'assignment_scope'
  ) AS has_assignment_scope_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'call_dispositions'
      AND column_name = 'request_id'
  ) AS has_disposition_request_id_column,
  EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'mip_app'
      AND tablename = 'lead_assignments'
      AND indexname = 'idx_lead_assignments_request_borrower'
  ) AS has_assignment_request_id_index,
  EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'mip_app'
      AND tablename = 'lead_assignments'
      AND indexname = 'idx_lead_assignments_single_request_id'
  ) AS has_assignment_single_request_id_index,
  EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'mip_app'
      AND tablename = 'call_dispositions'
      AND indexname = 'idx_call_dispositions_request_id'
  ) AS has_disposition_request_id_index
"""
_SALES_WORKFLOW_REQUEST_ID_KEY: str = "mip_bootstrap_sales_workflow_request_id"

# S2 loan-officer entity + assignment lifecycle. The deploy-time
# mip_lakebase_migrate job owns this DDL via lakebase/schema.sql; the
# runtime bootstrap is the between-deploys backstop so the first
# /loan-officers request on a not-yet-migrated instance doesn't 500.
# Keep in sync with lakebase/schema.sql.
_LOAN_OFFICER_LIFECYCLE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS mip_app.loan_officers (
        loan_officer_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email             TEXT NOT NULL UNIQUE REFERENCES mip_app.sales_team(email),
        display_name      TEXT NOT NULL,
        coverage_states   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
        coverage_counties TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
        active            BOOLEAN NOT NULL DEFAULT true,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    (
        "CREATE INDEX IF NOT EXISTS idx_loan_officers_active "
        "ON mip_app.loan_officers (active, display_name)"
    ),
    (
        "ALTER TABLE mip_app.lead_assignments ADD COLUMN IF NOT EXISTS "
        "loan_officer_id UUID REFERENCES mip_app.loan_officers(loan_officer_id)"
    ),
    (
        "ALTER TABLE mip_app.lead_assignments ADD COLUMN IF NOT EXISTS "
        "status TEXT NOT NULL DEFAULT 'assigned'"
    ),
    (
        "ALTER TABLE mip_app.lead_assignments ADD COLUMN IF NOT EXISTS "
        "status_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ),
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_lead_assignments_status'
              AND conrelid = 'mip_app.lead_assignments'::regclass
        ) THEN
            ALTER TABLE mip_app.lead_assignments
                ADD CONSTRAINT ck_lead_assignments_status
                CHECK (status IN ('assigned','contact_drafted','approved','actioned','outcome_recorded'));
        END IF;
    END $$
    """,
    (
        "CREATE INDEX IF NOT EXISTS idx_lead_assignments_lo_status "
        "ON mip_app.lead_assignments (loan_officer_id, status) "
        "WHERE released_at IS NULL"
    ),
)
_LOAN_OFFICER_LIFECYCLE_PREFLIGHT_SQL = """
SELECT
  EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'mip_app'
      AND table_name = 'loan_officers'
  ) AS has_loan_officers_table,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'lead_assignments'
      AND column_name = 'loan_officer_id'
  ) AS has_assignment_loan_officer_id_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'lead_assignments'
      AND column_name = 'status'
  ) AS has_assignment_status_column,
  EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'mip_app'
      AND tablename = 'lead_assignments'
      AND indexname = 'idx_lead_assignments_lo_status'
  ) AS has_assignment_lo_status_index
"""
_LOAN_OFFICER_LIFECYCLE_KEY: str = "mip_bootstrap_loan_officer_lifecycle"

# Feature C DDL -- loan-officer assignment + follow-up reminder columns on
# the approval decision row. Existing demo workspaces already have
# ``mip_app.approvals`` from an earlier deploy, so the table-level
# ``CREATE IF NOT EXISTS`` in ``lakebase/schema.sql`` will NOT add these
# columns to those tables; the runtime write path bootstraps them once per
# process before the approve INSERT references them. Reminder delivery is
# out of scope -- these columns only persist the assignment + computed
# follow_up_at timestamp. Keep in sync with ``lakebase/schema.sql``.
_APPROVAL_FOLLOWUP_DDL: tuple[str, ...] = (
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS assigned_to_email TEXT",
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS follow_up_at TIMESTAMPTZ",
)
_APPROVAL_FOLLOWUP_PREFLIGHT_SQL = """
SELECT
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'approvals'
      AND column_name = 'assigned_to_email'
  ) AS has_assigned_to_email_column,
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'approvals'
      AND column_name = 'follow_up_at'
  ) AS has_follow_up_at_column
"""
_APPROVAL_FOLLOWUP_KEY: str = "mip_bootstrap_approvals_followup"


# S6 DDL -- assignment-outcome columns on the existing feedback table.
# Outcomes reuse the feedback row shape (event_type carries the outcome
# vocabulary); these additive columns join an outcome back to its
# lifecycle assignment, its idempotency key, and its in-transaction
# LEAD_OUTCOME_RECORDED audit row. Keep in sync with the S6 appendix in
# ``lakebase/schema.sql``.
_ASSIGNMENT_OUTCOME_DDL: tuple[str, ...] = (
    "ALTER TABLE mip_app.feedback ADD COLUMN IF NOT EXISTS assignment_id UUID",
    "ALTER TABLE mip_app.feedback ADD COLUMN IF NOT EXISTS request_id TEXT",
    "ALTER TABLE mip_app.feedback ADD COLUMN IF NOT EXISTS audit_event_id UUID",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_request_id "
        "ON mip_app.feedback (request_id) WHERE request_id IS NOT NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_assignment_outcome "
        "ON mip_app.feedback (assignment_id) "
        "WHERE assignment_id IS NOT NULL "
        "AND event_type LIKE 'assignment_outcome_%'"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_feedback_assignment "
        "ON mip_app.feedback (assignment_id, recorded_at DESC) "
        "WHERE assignment_id IS NOT NULL"
    ),
)
_ASSIGNMENT_OUTCOME_PREFLIGHT_SQL = """
SELECT
  EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'mip_app'
      AND table_name = 'feedback'
      AND column_name = 'assignment_id'
  ) AS has_assignment_id_column,
  EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'mip_app'
      AND tablename = 'feedback'
      AND indexname = 'idx_feedback_request_id'
  ) AS has_request_id_index,
  EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'mip_app'
      AND tablename = 'feedback'
      AND indexname = 'idx_feedback_assignment_outcome'
  ) AS has_assignment_outcome_index
"""
_ASSIGNMENT_OUTCOME_KEY: str = "mip_bootstrap_s6_assignment_outcome"
