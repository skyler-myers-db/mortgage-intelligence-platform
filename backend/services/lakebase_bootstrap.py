"""One-shot runtime bootstrap for Lakebase schema drift.

The mip_lakebase_migrate Databricks Job is the canonical owner of
``lakebase/schema.sql`` -- it runs on bundle deploy and applies every
``CREATE ... IF NOT EXISTS`` / ``ALTER ... IF NOT EXISTS`` idempotently.
But we ship small, targeted DDLs (e.g. R5-01 idempotency key) between
deploys too, and the first HTTP path that depends on the new column
would otherwise 500 until the SE remembered to re-run the job.

This module runs those small, retry-safe DDLs once per process on the
first call to :func:`ensure_approval_idempotency_column`. Failure is
non-fatal -- we log a warning and let the call path fall through; the
downstream INSERT will either succeed (column already present) or fail
with a clear psycopg error the operator can diagnose.

Design rules:

* **Idempotent DDL only.** Every statement uses IF NOT EXISTS / partial
  unique index shape so a re-run on an already-migrated database is a
  no-op.
* **Per-process memoized.** A module-level flag guards a second
  execution; the bootstrap runs on first approve/reject per process.
* **Never silently papers over a deeper outage.** If the DDL itself
  raises ``LakebaseError`` we log and move on -- the calling INSERT
  will surface the real failure as 503.

R6-04 cross-process serialisation
---------------------------------

The ``mip_lakebase_migrate`` Databricks Job ALSO applies
``lakebase/schema.sql`` post-deploy. When the app boots immediately
after ``databricks bundle deploy -t dev`` the first approve/reject can
race the migrate job -- both run the same IF-NOT-EXISTS / partial
unique index DDL concurrently. Postgres serialises CREATE INDEX via
AccessExclusiveLock, but the ``ADD COLUMN IF NOT EXISTS`` + ``CREATE
UNIQUE INDEX IF NOT EXISTS`` sequence can still interleave in ways
that surface as ``duplicate_column`` / ``duplicate_object`` on some
psycopg+PG combinations.

We wrap the DDL in ``pg_advisory_lock(hashtext('<migration-key>'))``
so only one process holds the migration lock at a time; the second
waits its turn and then sees the idempotent DDL as a no-op. Lock is
released via ``pg_advisory_unlock`` in a ``finally`` block so an
aborted statement can't hold it past the transaction. The lock key
is a deterministic 64-bit integer derived from the migration name,
so future bootstrap DDLs each get their own key (register a new
``_advisory_key_for`` entry -- see ``_APPROVAL_REQUEST_ID_KEY``).
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit

log = logging.getLogger(__name__)


# R5-01 DDL -- matches sql/ddl/lakebase_add_request_id.sql. Keep the two
# in sync; the SE-facing standalone file exists so operators can apply
# the migration without re-running the whole schema.sql.
_APPROVAL_REQUEST_ID_DDL: tuple[str, ...] = (
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS request_id TEXT",
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

_LOCK = Lock()
_APPROVAL_REQUEST_ID_BOOTSTRAPPED: bool = False
_SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED: bool = False
_APPROVAL_FOLLOWUP_BOOTSTRAPPED: bool = False
_LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED: bool = False
_ASSIGNMENT_OUTCOME_BOOTSTRAPPED: bool = False


def ensure_approval_idempotency_column(client: LakebaseClient) -> None:
    """Apply the R5-01 ``request_id`` DDL once per process.

    Safe to call on every approve/reject -- the internal flag makes the
    second and every subsequent call a pure no-op (no Lakebase round-
    trip). Failures are logged at WARNING and swallowed: the caller's
    INSERT is the next thing to run and will report any real outage.

    R6-04: the DDL block is serialised across processes via
    ``pg_advisory_lock(hashtext(_APPROVAL_REQUEST_ID_KEY))`` so the app
    bootstrap and the ``mip_lakebase_migrate`` job can't interleave
    ``ADD COLUMN IF NOT EXISTS`` + ``CREATE UNIQUE INDEX IF NOT EXISTS``
    on some psycopg+PG combinations. The unlock is issued in a
    ``finally`` branch regardless of outcome; an aborted statement
    therefore cannot hold the lock past the request path.
    """
    global _APPROVAL_REQUEST_ID_BOOTSTRAPPED
    if _APPROVAL_REQUEST_ID_BOOTSTRAPPED:
        return
    with _LOCK:
        if _APPROVAL_REQUEST_ID_BOOTSTRAPPED:
            return
        lock_acquired = False
        try:
            if _approval_request_id_already_applied(client):
                emit(
                    log,
                    "lakebase_bootstrap_already_applied",
                    migration="r5_01_approvals_request_id",
                )
                _APPROVAL_REQUEST_ID_BOOTSTRAPPED = True
                return
            # R6-04: take the advisory lock first so a racing mip_lakebase_
            # migrate job waits its turn on the DDL block instead of
            # interleaving statements.
            client.execute(
                "SELECT pg_advisory_lock(hashtext(%(key)s))",
                {"key": _APPROVAL_REQUEST_ID_KEY},
            )
            lock_acquired = True
            for stmt in _APPROVAL_REQUEST_ID_DDL:
                client.execute(stmt)
        except LakebaseError as exc:
            # R6-02: leave the flag False so the next approve/reject
            # retries the idempotent DDL. The earlier implementation set
            # the flag on BOTH success and failure; if the first bootstrap
            # call landed during a Lakebase brownout, the process
            # permanently believed the migration succeeded and every
            # subsequent INSERT silently failed on an index that didn't
            # exist. Since every statement is IF NOT EXISTS, retrying
            # on the next request is safe and self-healing.
            emit(
                log,
                "lakebase_bootstrap_failed",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="r5_01_approvals_request_id",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock(client, lock_acquired)
            return
        except Exception as exc:  # noqa: BLE001 -- bootstrap must never crash request path
            # R6-02: same posture -- leave flag False for retry on the
            # next call. A persistent outage will log every request, but
            # that is the intended signal; silently latching success is
            # the worse failure mode.
            emit(
                log,
                "lakebase_bootstrap_unexpected_failure",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="r5_01_approvals_request_id",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock(client, lock_acquired)
            return
        # Success path: release the lock, then emit the structured event
        # and latch the flag. Release order matches the "lock first,
        # unlock last" pattern; a failure during the unlock itself is
        # logged but cannot regress the migration (DDL already applied).
        _release_advisory_lock(client, lock_acquired)
        emit(
            log,
            "lakebase_bootstrap_applied",
            migration="r5_01_approvals_request_id",
            statements=len(_APPROVAL_REQUEST_ID_DDL),
        )
        _APPROVAL_REQUEST_ID_BOOTSTRAPPED = True


def ensure_sales_workflow_request_id_columns(client: LakebaseClient) -> None:
    """Apply Sales Manager request-id DDL once per process.

    Existing demo workspaces may already have `lead_assignments` and
    `call_dispositions` from an earlier deploy. The table-level
    `CREATE IF NOT EXISTS` in `lakebase/schema.sql` will not add newly
    introduced columns to those existing tables, so the runtime write path
    must bootstrap the two `request_id` columns and their partial unique
    indexes before assignment/disposition inserts use them.
    """
    global _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED
    if _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED:
        return
    with _LOCK:
        if _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED:
            return
        lock_acquired = False
        try:
            if _sales_workflow_request_id_already_applied(client):
                emit(
                    log,
                    "lakebase_bootstrap_already_applied",
                    migration="sales_workflow_request_id",
                )
                _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED = True
                return
            client.execute(
                "SELECT pg_advisory_lock(hashtext(%(key)s))",
                {"key": _SALES_WORKFLOW_REQUEST_ID_KEY},
            )
            lock_acquired = True
            for stmt in _SALES_WORKFLOW_REQUEST_ID_DDL:
                client.execute(stmt)
        except LakebaseError as exc:
            emit(
                log,
                "lakebase_bootstrap_failed",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="sales_workflow_request_id",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _SALES_WORKFLOW_REQUEST_ID_KEY)
            return
        except Exception as exc:  # noqa: BLE001 -- bootstrap must never crash request path
            emit(
                log,
                "lakebase_bootstrap_unexpected_failure",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="sales_workflow_request_id",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _SALES_WORKFLOW_REQUEST_ID_KEY)
            return
        _release_advisory_lock_with_key(client, lock_acquired, _SALES_WORKFLOW_REQUEST_ID_KEY)
        emit(
            log,
            "lakebase_bootstrap_applied",
            migration="sales_workflow_request_id",
            statements=len(_SALES_WORKFLOW_REQUEST_ID_DDL),
        )
        _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED = True


def ensure_approval_followup_columns(client: LakebaseClient) -> None:
    """Apply the Feature C assignment + follow-up DDL once per process.

    Safe to call on every approve -- the per-process flag makes the
    second and every subsequent call a pure no-op. Failures are logged
    at WARNING and swallowed; the caller's INSERT is the next thing to
    run and will surface any real outage as 503. Serialised across
    processes via ``pg_advisory_lock`` so the app bootstrap and the
    ``mip_lakebase_migrate`` job can't interleave the two
    ``ADD COLUMN IF NOT EXISTS`` statements.
    """
    global _APPROVAL_FOLLOWUP_BOOTSTRAPPED
    if _APPROVAL_FOLLOWUP_BOOTSTRAPPED:
        return
    with _LOCK:
        if _APPROVAL_FOLLOWUP_BOOTSTRAPPED:
            return
        lock_acquired = False
        try:
            if _approval_followup_already_applied(client):
                emit(
                    log,
                    "lakebase_bootstrap_already_applied",
                    migration="approvals_followup",
                )
                _APPROVAL_FOLLOWUP_BOOTSTRAPPED = True
                return
            client.execute(
                "SELECT pg_advisory_lock(hashtext(%(key)s))",
                {"key": _APPROVAL_FOLLOWUP_KEY},
            )
            lock_acquired = True
            for stmt in _APPROVAL_FOLLOWUP_DDL:
                client.execute(stmt)
        except LakebaseError as exc:
            emit(
                log,
                "lakebase_bootstrap_failed",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="approvals_followup",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _APPROVAL_FOLLOWUP_KEY)
            return
        except Exception as exc:  # noqa: BLE001 -- bootstrap must never crash request path
            emit(
                log,
                "lakebase_bootstrap_unexpected_failure",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="approvals_followup",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _APPROVAL_FOLLOWUP_KEY)
            return
        _release_advisory_lock_with_key(client, lock_acquired, _APPROVAL_FOLLOWUP_KEY)
        emit(
            log,
            "lakebase_bootstrap_applied",
            migration="approvals_followup",
            statements=len(_APPROVAL_FOLLOWUP_DDL),
        )
        _APPROVAL_FOLLOWUP_BOOTSTRAPPED = True


def ensure_loan_officer_lifecycle_schema(client: LakebaseClient) -> None:
    """Apply the S2 loan-officer entity + lifecycle DDL once per process.

    Same posture as the other bootstraps: read-only preflight first so an
    already-migrated database never sees owner-only DDL, advisory lock so
    the app bootstrap and the ``mip_lakebase_migrate`` job cannot
    interleave, failures logged at WARNING and swallowed (the caller's
    query surfaces the real outage), and the latch stays False on failure
    so the next request retries the idempotent DDL.
    """
    global _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED
    if _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED:
        return
    with _LOCK:
        if _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED:
            return
        lock_acquired = False
        try:
            if _loan_officer_lifecycle_already_applied(client):
                emit(
                    log,
                    "lakebase_bootstrap_already_applied",
                    migration="s2_loan_officer_lifecycle",
                )
                _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED = True
                return
            client.execute(
                "SELECT pg_advisory_lock(hashtext(%(key)s))",
                {"key": _LOAN_OFFICER_LIFECYCLE_KEY},
            )
            lock_acquired = True
            for stmt in _LOAN_OFFICER_LIFECYCLE_DDL:
                client.execute(stmt)
        except LakebaseError as exc:
            emit(
                log,
                "lakebase_bootstrap_failed",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="s2_loan_officer_lifecycle",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _LOAN_OFFICER_LIFECYCLE_KEY)
            return
        except Exception as exc:  # noqa: BLE001 -- bootstrap must never crash request path
            emit(
                log,
                "lakebase_bootstrap_unexpected_failure",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="s2_loan_officer_lifecycle",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _LOAN_OFFICER_LIFECYCLE_KEY)
            return
        _release_advisory_lock_with_key(client, lock_acquired, _LOAN_OFFICER_LIFECYCLE_KEY)
        emit(
            log,
            "lakebase_bootstrap_applied",
            migration="s2_loan_officer_lifecycle",
            statements=len(_LOAN_OFFICER_LIFECYCLE_DDL),
        )
        _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED = True


def _approval_request_id_already_applied(client: LakebaseClient) -> bool:
    """Return True when the migration shape already exists.

    The Databricks deploy-time Lakebase migration is the normal owner of DDL.
    The running app principal may have table DML rights but not ownership, so
    blindly issuing ``ALTER TABLE`` on every fresh process can produce noisy
    ``InsufficientPrivilege`` warnings even when the schema is already correct.
    A read-only preflight lets the app latch success without attempting DDL.
    Older test doubles may only implement ``execute``; those fall through to
    the legacy idempotent DDL path.
    """
    fetchone = getattr(client, "fetchone", None)
    if not callable(fetchone):
        return False
    row = fetchone(_APPROVAL_REQUEST_ID_PREFLIGHT_SQL)
    if not row:
        return False
    return bool(row.get("has_request_id_column")) and bool(
        row.get("has_request_id_index")
    )


def _sales_workflow_request_id_already_applied(client: LakebaseClient) -> bool:
    """Return True when the sales workflow request-id schema exists.

    Databricks App runtime principals commonly have DML on ``mip_app`` but
    not table ownership. After the deploy-time migration has applied the
    columns and indexes, a read-only preflight avoids owner-only ``ALTER`` /
    ``DROP INDEX`` / ``CREATE INDEX`` attempts on the first sales-manager
    route hit.
    """
    fetchone = getattr(client, "fetchone", None)
    if not callable(fetchone):
        return False
    row = fetchone(_SALES_WORKFLOW_REQUEST_ID_PREFLIGHT_SQL)
    if not row:
        return False
    return (
        bool(row.get("has_assignment_request_id_column"))
        and bool(row.get("has_assignment_scope_column"))
        and bool(row.get("has_disposition_request_id_column"))
        and bool(row.get("has_assignment_request_id_index"))
        and bool(row.get("has_assignment_single_request_id_index"))
        and bool(row.get("has_disposition_request_id_index"))
    )


def _approval_followup_already_applied(client: LakebaseClient) -> bool:
    """Return True when the approval assignment/follow-up columns exist.

    The deploy migration owns this DDL. Runtime bootstrap is only a
    backstop for locally owned or partially migrated databases, so the app
    should skip ``ALTER TABLE`` when a read-only catalog check proves the
    shape is already present.
    """
    fetchone = getattr(client, "fetchone", None)
    if not callable(fetchone):
        return False
    row = fetchone(_APPROVAL_FOLLOWUP_PREFLIGHT_SQL)
    if not row:
        return False
    return bool(row.get("has_assigned_to_email_column")) and bool(
        row.get("has_follow_up_at_column")
    )


def _loan_officer_lifecycle_already_applied(client: LakebaseClient) -> bool:
    """Return True when the S2 loan-officer lifecycle schema exists."""
    fetchone = getattr(client, "fetchone", None)
    if not callable(fetchone):
        return False
    row = fetchone(_LOAN_OFFICER_LIFECYCLE_PREFLIGHT_SQL)
    if not row:
        return False
    return (
        bool(row.get("has_loan_officers_table"))
        and bool(row.get("has_assignment_loan_officer_id_column"))
        and bool(row.get("has_assignment_status_column"))
        and bool(row.get("has_assignment_lo_status_index"))
    )


def _release_advisory_lock(client: LakebaseClient, acquired: bool) -> None:
    """Best-effort ``pg_advisory_unlock`` of the bootstrap key.

    Callable from both success and failure paths. When the lock was
    never acquired (the SELECT itself raised) this is a no-op. A
    failure during the unlock is logged at WARNING but does not raise
    -- advisory locks auto-release on session end so a stuck lock
    heals after the next Lakebase connection cycle anyway.
    """
    if not acquired:
        return
    _release_advisory_lock_with_key(client, acquired, _APPROVAL_REQUEST_ID_KEY)


def _release_advisory_lock_with_key(client: LakebaseClient, acquired: bool, key: str) -> None:
    if not acquired:
        return
    try:
        client.execute(
            "SELECT pg_advisory_unlock(hashtext(%(key)s))",
            {"key": key},
        )
    except Exception as exc:  # noqa: BLE001 -- unlock failure is informational
        emit(
            log,
            "lakebase_bootstrap_advisory_unlock_failed",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="error",
            key=key,
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )


def _assignment_outcome_already_applied(client: LakebaseClient) -> bool:
    """Return True when the S6 assignment-outcome feedback schema exists."""
    fetchone = getattr(client, "fetchone", None)
    if not callable(fetchone):
        return False
    row = fetchone(_ASSIGNMENT_OUTCOME_PREFLIGHT_SQL)
    if not row:
        return False
    return (
        bool(row.get("has_assignment_id_column"))
        and bool(row.get("has_request_id_index"))
        and bool(row.get("has_assignment_outcome_index"))
    )


def ensure_assignment_outcome_schema(client: LakebaseClient) -> None:
    """Apply the S6 assignment-outcome feedback DDL once per process.

    Same posture as the other bootstraps: read-only preflight first so an
    already-migrated database never sees owner-only DDL, advisory lock so
    the app bootstrap and the ``mip_lakebase_migrate`` job cannot
    interleave, failures logged at WARNING and swallowed (the caller's
    write surfaces the real outage), and the latch stays False on failure
    so the next request retries the idempotent DDL.
    """
    global _ASSIGNMENT_OUTCOME_BOOTSTRAPPED
    if _ASSIGNMENT_OUTCOME_BOOTSTRAPPED:
        return
    with _LOCK:
        if _ASSIGNMENT_OUTCOME_BOOTSTRAPPED:
            return
        lock_acquired = False
        try:
            if _assignment_outcome_already_applied(client):
                emit(
                    log,
                    "lakebase_bootstrap_already_applied",
                    migration="s6_assignment_outcome",
                )
                _ASSIGNMENT_OUTCOME_BOOTSTRAPPED = True
                return
            client.execute(
                "SELECT pg_advisory_lock(hashtext(%(key)s))",
                {"key": _ASSIGNMENT_OUTCOME_KEY},
            )
            lock_acquired = True
            for stmt in _ASSIGNMENT_OUTCOME_DDL:
                client.execute(stmt)
        except LakebaseError as exc:
            emit(
                log,
                "lakebase_bootstrap_failed",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="s6_assignment_outcome",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _ASSIGNMENT_OUTCOME_KEY)
            return
        except Exception as exc:  # noqa: BLE001 -- bootstrap must never crash request path
            emit(
                log,
                "lakebase_bootstrap_unexpected_failure",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="error",
                migration="s6_assignment_outcome",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:500],
            )
            _release_advisory_lock_with_key(client, lock_acquired, _ASSIGNMENT_OUTCOME_KEY)
            return
        _release_advisory_lock_with_key(client, lock_acquired, _ASSIGNMENT_OUTCOME_KEY)
        emit(
            log,
            "lakebase_bootstrap_applied",
            migration="s6_assignment_outcome",
            statements=len(_ASSIGNMENT_OUTCOME_DDL),
        )
        _ASSIGNMENT_OUTCOME_BOOTSTRAPPED = True


def _reset_bootstrap_for_tests() -> None:
    """Test helper -- clear the per-process flag between tests."""
    global _APPROVAL_REQUEST_ID_BOOTSTRAPPED, _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED
    global _APPROVAL_FOLLOWUP_BOOTSTRAPPED, _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED
    global _ASSIGNMENT_OUTCOME_BOOTSTRAPPED
    _APPROVAL_REQUEST_ID_BOOTSTRAPPED = False
    _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED = False
    _APPROVAL_FOLLOWUP_BOOTSTRAPPED = False
    _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED = False
    _ASSIGNMENT_OUTCOME_BOOTSTRAPPED = False


def _bootstrap_state_for_tests() -> dict[str, Any]:
    """Test helper -- read the current bootstrap flag."""
    return {
        "request_id_bootstrapped": _APPROVAL_REQUEST_ID_BOOTSTRAPPED,
        "sales_workflow_request_id_bootstrapped": _SALES_WORKFLOW_REQUEST_ID_BOOTSTRAPPED,
        "approval_followup_bootstrapped": _APPROVAL_FOLLOWUP_BOOTSTRAPPED,
        "loan_officer_lifecycle_bootstrapped": _LOAN_OFFICER_LIFECYCLE_BOOTSTRAPPED,
        "assignment_outcome_bootstrapped": _ASSIGNMENT_OUTCOME_BOOTSTRAPPED,
    }
