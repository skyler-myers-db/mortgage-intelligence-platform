"""Migration-state predicates and advisory-lock release for the bootstrap.

Single responsibility: answer "is this migration already applied?" with a
read-only catalog preflight, and release the advisory lock the
orchestration takes around DDL. Both behaviors are separable from the
``ensure_*`` orchestration so idempotency and lock handling stay
independently testable.

The predicates exist because the running app principal usually holds DML
but not table ownership: proving the shape is already there lets the app
skip owner-only DDL instead of retrying it on every fresh process.
"""
from __future__ import annotations

import logging

from backend.services.lakebase import LakebaseClient
from backend.services.lakebase_bootstrap_sql import (
    _APPROVAL_FOLLOWUP_PREFLIGHT_SQL,
    _APPROVAL_REQUEST_ID_KEY,
    _APPROVAL_REQUEST_ID_PREFLIGHT_SQL,
    _ASSIGNMENT_OUTCOME_PREFLIGHT_SQL,
    _LOAN_OFFICER_LIFECYCLE_PREFLIGHT_SQL,
    _SALES_WORKFLOW_REQUEST_ID_PREFLIGHT_SQL,
)
from backend.services.observability import emit

log = logging.getLogger(__name__)


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
    return (
        bool(row.get("has_request_id_column"))
        and bool(row.get("has_decision_intent_column"))
        and bool(row.get("has_decision_payload_hash_column"))
        and bool(row.get("has_decision_response_column"))
        and bool(row.get("has_audit_event_id_column"))
        and bool(row.get("has_request_id_index"))
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
