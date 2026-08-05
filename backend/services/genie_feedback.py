"""Durable, replay-safe native Genie message feedback.

The service commits a Lakebase intent and audit row before calling the native
Genie feedback endpoint. A client request id scopes retries to one logical
rating. Native rating delivery is replay-safe because Databricks models the
operation as setting ``POSITIVE`` or ``NEGATIVE`` on the message.

Free-text feedback is rejected by the API. The service accepts thumbs only,
so neither caller prose nor a synthetic comment is forwarded to Genie.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.genie_audit import genie_audit_entity_id_from_parts
from backend.services.genie_client import GenieClientError, ResilientGenieClient
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit

log = logging.getLogger("backend.services.genie_feedback")

GenieFeedbackRating = Literal["POSITIVE", "NEGATIVE"]

_REQUEST_NAMESPACE = UUID("df508632-9ce2-4a17-bce1-eb3991fbcdf5")

_INSERT_INTENT_SQL = """
INSERT INTO mip_app.genie_feedback_requests (
    actor_email, request_id, conversation_id, message_id, rating,
    comment_present, status
) VALUES (
    %(actor_email)s, %(request_id)s, %(conversation_id)s, %(message_id)s,
    %(rating)s, %(comment_present)s, 'PENDING'
)
ON CONFLICT (actor_email, request_id) DO NOTHING
RETURNING feedback_request_id, actor_email, request_id, conversation_id,
          message_id, rating, comment_present, status, audit_event_id
"""

_SELECT_INTENT_FOR_UPDATE_SQL = """
SELECT feedback_request_id, actor_email, request_id, conversation_id,
       message_id, rating, comment_present, status, attempt_count, audit_event_id
FROM mip_app.genie_feedback_requests
WHERE actor_email = %(actor_email)s
  AND request_id = %(request_id)s
FOR UPDATE
"""

_ATTACH_INTENT_AUDIT_SQL = """
UPDATE mip_app.genie_feedback_requests
SET intent_audit_event_id = %(intent_audit_event_id)s,
    updated_at = now()
WHERE feedback_request_id = %(feedback_request_id)s
RETURNING feedback_request_id
"""

_CLAIM_INTENT_SQL = """
UPDATE mip_app.genie_feedback_requests
SET status = 'IN_FLIGHT',
    attempt_count = attempt_count + 1,
    last_attempt_at = now(),
    last_error_code = NULL,
    updated_at = now()
WHERE feedback_request_id = %(feedback_request_id)s
  AND (
      status IN ('PENDING', 'RETRYABLE_FAILED')
      OR (status = 'IN_FLIGHT' AND updated_at < now() - INTERVAL '2 minutes')
  )
RETURNING feedback_request_id, attempt_count
"""

_MARK_RETRYABLE_FAILED_SQL = """
UPDATE mip_app.genie_feedback_requests
SET status = 'RETRYABLE_FAILED',
    last_error_code = %(last_error_code)s,
    updated_at = now()
WHERE feedback_request_id = %(feedback_request_id)s
  AND status = 'IN_FLIGHT'
  AND attempt_count = %(attempt_count)s
RETURNING feedback_request_id, attempt_count
"""

_MARK_SUCCEEDED_SQL = """
UPDATE mip_app.genie_feedback_requests
SET status = 'SUCCEEDED',
    audit_event_id = %(audit_event_id)s,
    last_error_code = NULL,
    succeeded_at = now(),
    updated_at = now()
WHERE feedback_request_id = %(feedback_request_id)s
RETURNING feedback_request_id
"""


class GenieFeedbackConflictError(RuntimeError):
    """The same request id was reused for a different logical rating."""


class GenieFeedbackInProgressError(RuntimeError):
    """Another worker still owns the request's delivery lease."""


class GenieFeedbackDeliveryError(RuntimeError):
    """Native rating delivery failed and remains retryable by request id."""


@dataclass(frozen=True, slots=True)
class _FeedbackClaim:
    feedback_request_id: str
    attempt_count: int
    completed_audit_event_id: str | None = None


def resolve_genie_feedback_request_id(
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
    helpful: bool,
    request_id: str | None,
) -> str:
    """Use the client key or derive a stable key for legacy callers."""
    if request_id is not None:
        return request_id
    logical_request = "\x00".join(
        (actor, conversation_id, message_id, "POSITIVE" if helpful else "NEGATIVE")
    )
    return f"genie-{uuid5(_REQUEST_NAMESPACE, logical_request)}"


def _execute_one(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _assert_same_intent(
    row: dict[str, Any],
    *,
    conversation_id: str,
    message_id: str,
    rating: GenieFeedbackRating,
    comment_present: bool,
) -> None:
    expected = (conversation_id, message_id, rating, comment_present)
    actual = (
        str(row.get("conversation_id") or ""),
        str(row.get("message_id") or ""),
        str(row.get("rating") or ""),
        bool(row.get("comment_present")),
    )
    if actual != expected:
        raise GenieFeedbackConflictError


def _claim_feedback_intent(
    lakebase: LakebaseClient,
    *,
    actor: str,
    request_id: str,
    conversation_id: str,
    message_id: str,
    rating: GenieFeedbackRating,
    comment_present: bool,
) -> _FeedbackClaim:
    entity_id = genie_audit_entity_id_from_parts(
        message_id=message_id,
        conversation_id=conversation_id,
        fallback="genie",
    )
    params: dict[str, Any] = {
        "actor_email": actor,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "rating": rating,
        "comment_present": comment_present,
    }
    with lakebase.transaction() as conn:
        row = _execute_one(conn, _INSERT_INTENT_SQL, params)
        if row is not None:
            intent_event = write_audit_event_in_transaction(
                conn,
                actor=actor,
                action="genie.feedback.intent",
                entity_type="genie_message",
                entity_id=entity_id,
                payload_json={
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "helpful": rating == "POSITIVE",
                    "comment_present": comment_present,
                },
                event_type="GENIE_FEEDBACK_INTENT",
                request_id=request_id,
            )
            attached = _execute_one(
                conn,
                _ATTACH_INTENT_AUDIT_SQL,
                {
                    "feedback_request_id": row["feedback_request_id"],
                    "intent_audit_event_id": intent_event.event_id,
                },
            )
            if attached is None:
                raise LakebaseError("Genie feedback intent audit link failed")
        else:
            row = _execute_one(conn, _SELECT_INTENT_FOR_UPDATE_SQL, params)
            if row is None:
                raise LakebaseError("Genie feedback idempotency row disappeared")

        _assert_same_intent(
            row,
            conversation_id=conversation_id,
            message_id=message_id,
            rating=rating,
            comment_present=comment_present,
        )
        if row.get("status") == "SUCCEEDED":
            audit_event_id = row.get("audit_event_id")
            if audit_event_id is None:
                raise LakebaseError("Succeeded Genie feedback is missing its audit event")
            return _FeedbackClaim(
                feedback_request_id=str(row["feedback_request_id"]),
                attempt_count=int(row.get("attempt_count") or 0),
                completed_audit_event_id=str(audit_event_id),
            )

        claimed = _execute_one(
            conn,
            _CLAIM_INTENT_SQL,
            {"feedback_request_id": row["feedback_request_id"]},
        )
        if claimed is None:
            raise GenieFeedbackInProgressError
        return _FeedbackClaim(
            feedback_request_id=str(row["feedback_request_id"]),
            attempt_count=int(claimed["attempt_count"]),
        )


def _safe_delivery_error_code(exc: BaseException) -> str:
    if isinstance(exc, GenieClientError) and exc.status_code is not None:
        return f"http_{exc.status_code}"
    return "genie_client_error"


def _mark_retryable_failure(
    lakebase: LakebaseClient,
    *,
    feedback_request_id: str,
    attempt_count: int,
    error_code: str,
) -> None:
    with lakebase.transaction() as conn:
        _execute_one(
            conn,
            _MARK_RETRYABLE_FAILED_SQL,
            {
                "feedback_request_id": feedback_request_id,
                "attempt_count": attempt_count,
                "last_error_code": error_code,
            },
        )


def _mark_succeeded(
    lakebase: LakebaseClient,
    *,
    feedback_request_id: str,
    actor: str,
    request_id: str,
    conversation_id: str,
    message_id: str,
    rating: GenieFeedbackRating,
    comment_present: bool,
) -> tuple[str, bool]:
    entity_id = genie_audit_entity_id_from_parts(
        message_id=message_id,
        conversation_id=conversation_id,
        fallback="genie",
    )
    with lakebase.transaction() as conn:
        row = _execute_one(
            conn,
            _SELECT_INTENT_FOR_UPDATE_SQL,
            {"actor_email": actor, "request_id": request_id},
        )
        if row is None:
            raise LakebaseError("Genie feedback intent missing during completion")
        if str(row["feedback_request_id"]) != feedback_request_id:
            raise LakebaseError("Genie feedback intent changed during completion")
        _assert_same_intent(
            row,
            conversation_id=conversation_id,
            message_id=message_id,
            rating=rating,
            comment_present=comment_present,
        )
        if row.get("status") == "SUCCEEDED" and row.get("audit_event_id") is not None:
            return str(row["audit_event_id"]), False

        event = write_audit_event_in_transaction(
            conn,
            actor=actor,
            action="genie.feedback",
            entity_type="genie_message",
            entity_id=entity_id,
            payload_json={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "helpful": rating == "POSITIVE",
                "comment_present": comment_present,
            },
            event_type="GENIE_FEEDBACK",
            request_id=request_id,
        )
        updated = _execute_one(
            conn,
            _MARK_SUCCEEDED_SQL,
            {
                "feedback_request_id": feedback_request_id,
                "audit_event_id": event.event_id,
            },
        )
        if updated is None:
            raise LakebaseError("Genie feedback completion update failed")
        return event.event_id, True


def record_genie_feedback(
    lakebase: LakebaseClient,
    genie: ResilientGenieClient,
    *,
    actor: str,
    request_id: str | None,
    conversation_id: str,
    message_id: str,
    helpful: bool,
    comment: str | None,
) -> str:
    """Durably deliver one native rating and return its final audit event id."""
    if (comment or "").strip():
        raise ValueError("Free-text feedback is disabled")
    rating: GenieFeedbackRating = "POSITIVE" if helpful else "NEGATIVE"
    resolved_request_id = resolve_genie_feedback_request_id(
        actor=actor,
        conversation_id=conversation_id,
        message_id=message_id,
        helpful=helpful,
        request_id=request_id,
    )
    comment_present = False
    claim = _claim_feedback_intent(
        lakebase,
        actor=actor,
        request_id=resolved_request_id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating=rating,
        comment_present=comment_present,
    )
    if claim.completed_audit_event_id is not None:
        return claim.completed_audit_event_id

    try:
        genie.send_message_feedback(conversation_id, message_id, rating)
    except Exception as exc:  # noqa: BLE001 - convert to a fixed public failure
        error_code = _safe_delivery_error_code(exc)
        try:
            _mark_retryable_failure(
                lakebase,
                feedback_request_id=claim.feedback_request_id,
                attempt_count=claim.attempt_count,
                error_code=error_code,
            )
        except Exception as state_exc:  # noqa: BLE001 - preserve original dependency failure
            emit(
                log,
                "genie_feedback_failure_state_update_failed",
                level=logging.ERROR,
                dependency="lakebase",
                outcome="error",
                exc_type=type(state_exc).__name__,
            )
        raise GenieFeedbackDeliveryError from exc

    audit_event_id, _completed_here = _mark_succeeded(
        lakebase,
        feedback_request_id=claim.feedback_request_id,
        actor=actor,
        request_id=resolved_request_id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating=rating,
        comment_present=comment_present,
    )
    return audit_event_id
