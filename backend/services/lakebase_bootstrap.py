"""One-shot runtime bootstrap for Lakebase schema drift.

The mip_lakebase_migrate Databricks Job is the canonical owner of
``lakebase/schema.sql`` -- it runs during signed deployment and applies every
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
after the signed resource phase the first approve/reject can race the migrate
job -- both run the same IF-NOT-EXISTS / partial
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

The DDL, preflight SQL, and advisory-lock keys live in
``lakebase_bootstrap_sql``; the read-only "already applied?" predicates and
the advisory-lock release live in ``lakebase_bootstrap_state``. This module
keeps the orchestration -- the per-process flags, the lock, and the
``ensure_*`` entry points -- and re-exports the DDL tuples so
``backend.services.lakebase_bootstrap`` stays the single import surface.
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.lakebase_bootstrap_sql import (
    _APPROVAL_FOLLOWUP_DDL,
    _APPROVAL_FOLLOWUP_KEY,
    _APPROVAL_REQUEST_ID_DDL,
    _APPROVAL_REQUEST_ID_KEY,
    _ASSIGNMENT_OUTCOME_DDL,
    _ASSIGNMENT_OUTCOME_KEY,
    _LOAN_OFFICER_LIFECYCLE_DDL,
    _LOAN_OFFICER_LIFECYCLE_KEY,
    _SALES_WORKFLOW_REQUEST_ID_DDL,
    _SALES_WORKFLOW_REQUEST_ID_KEY,
)
from backend.services.lakebase_bootstrap_state import (
    _approval_followup_already_applied,
    _approval_request_id_already_applied,
    _assignment_outcome_already_applied,
    _loan_officer_lifecycle_already_applied,
    _release_advisory_lock,
    _release_advisory_lock_with_key,
    _sales_workflow_request_id_already_applied,
)
from backend.services.observability import emit

log = logging.getLogger(__name__)


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
