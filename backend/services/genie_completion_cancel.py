"""The owner's Stop on a job-backed Genie turn (audit 2026-09-21 ``genie-03``).

``POST /api/genie/message/cancel`` requests the cancel of the caller's own
completion job. It is decided in ONE Lakebase transaction on the job row,
locked ``FOR UPDATE``:

* a cancel already requested (or a job already ``cancelled``): ``cancelled``,
  an idempotent repeat with no write and no audit row;
* the governed record already exists (``succeeded``, or ``running`` with
  ``recorded_at`` set): ``recorded``, no write and no audit row, because the
  answer was verified and recorded before the Stop;
* ``failed`` or ``expired``: ``ended``, no write and no audit row;
* otherwise the cancel is ACCEPTED: ``cancel_requested_at`` is set by a
  conditional UPDATE that requires ``recorded_at IS NULL`` (the runner's
  commit point requires the converse, and a CHECK forbids both), a queued job
  ends ``cancelled`` at once, and the ``GENIE_TURN_CANCELLED`` audit row is
  written in the SAME transaction. One row per accepted cancel: the flag is
  only ever set once. An audit failure rolls the flag back (fail closed; a
  retry is safe).

``cancelled`` means this app will not verify or record the answer. It never
means Genie's own message was cancelled. Nothing typed by the user is
accepted: the body carries ids, the progress token and the 16-hex question
label the submit returned, never the question.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.services import genie_completion_jobs as jobs
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_answers import GenieCancelResponse
from backend.services.genie_audit import genie_audit_entity_id_from_parts
from backend.services.genie_completion_stages import GenieJobStatus
from backend.services.lakebase import LakebaseClient
from backend.services.observability import emit

log = logging.getLogger("mip-genie-jobs")

QUESTION_LABEL_PATTERN = r"^[0-9a-f]{16}$"


class GenieCancelRequest(BaseModel):
    """``/message/cancel`` body. There is no ``question`` field: a body that
    carries one is refused (422), so the prompt never travels on a Stop."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    progress_token: str = Field(min_length=1, max_length=4_096)
    job_id: str = Field(pattern=jobs.JOB_ID_RE.pattern)
    #: The submit response's 16-hex label; checked against the token binding.
    question_hash: str = Field(pattern=QUESTION_LABEL_PATTERN)


_LOCK_SQL = """
SELECT status, stage, question_hash, lease_owner,
       cancel_requested_at IS NOT NULL AS cancel_requested,
       recorded_at IS NOT NULL AS recorded
  FROM mip_app.genie_completion_jobs
 WHERE job_id = %(job_id)s::uuid
   AND actor_email = %(actor_email)s
   AND conversation_id = %(conversation_id)s
   AND message_id = %(message_id)s
   FOR UPDATE
"""

_ACCEPT_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET cancel_requested_at = now(), updated_at = now(),
       status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
       stage = CASE WHEN status = 'queued' THEN 'cancelled' ELSE stage END,
       finished_at = CASE WHEN status = 'queued' THEN now() ELSE finished_at END
 WHERE job_id = %(job_id)s::uuid
   AND status IN ('queued', 'running')
   AND recorded_at IS NULL
   AND cancel_requested_at IS NULL
RETURNING status, lease_owner
"""

_ENDED = frozenset({GenieJobStatus.FAILED, GenieJobStatus.EXPIRED})

#: What the request did: accepted (flag set, audited) or one of the no-ops.
_Outcome = Literal["accepted", "duplicate", "recorded", "ended"]
_WIRE: dict[_Outcome, Literal["cancelled", "recorded", "ended"]] = {
    "accepted": "cancelled",
    "duplicate": "cancelled",
    "recorded": "recorded",
    "ended": "ended",
}


def _one(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _settled_outcome(row: dict[str, Any]) -> _Outcome | None:
    """The outcome of a Stop that changes nothing, or None to accept it."""

    status = GenieJobStatus(str(row["status"]))
    if row.get("cancel_requested") is True or status is GenieJobStatus.CANCELLED:
        return "duplicate"
    if status is GenieJobStatus.SUCCEEDED or row.get("recorded") is True:
        return "ended" if status in _ENDED else "recorded"
    if status in _ENDED:
        return "ended"
    return None


def _audit_accepted(conn: Any, *, actor: str, payload: GenieCancelRequest, prior: GenieJobStatus) -> None:
    write_audit_event_in_transaction(
        conn,
        actor=actor,
        action="genie.turn_cancelled",
        entity_type="genie_message",
        entity_id=genie_audit_entity_id_from_parts(
            conversation_id=payload.conversation_id,
            message_id=payload.message_id,
            question_hash=payload.question_hash,
        ),
        payload_json={
            "conversation_id": payload.conversation_id,
            "message_id": payload.message_id,
            "question_hash": payload.question_hash,
            "genie_job_id": payload.job_id,
            "status": prior.value,
        },
        event_type="GENIE_TURN_CANCELLED",
    )


def require_completion_jobs(lakebase: LakebaseClient) -> None:
    """The route's gate on the 2026_09_25 job table: a 404 when it (or a
    column) is absent, since there is no job to cancel, and a 503 when
    Lakebase could not answer the probe. An absence is cached; a failed probe
    never is, so a False with no fresh cache entry behind it is an outage."""

    if jobs.completion_jobs_available(lakebase):
        return
    probed = jobs._probe_cache_get(lakebase)
    if probed is None or probed[0] <= time.monotonic():
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))
    raise HTTPException(status_code=404, detail="Genie completion job not found")


def request_cancel(
    lakebase: LakebaseClient,
    *,
    actor: str,
    payload: GenieCancelRequest,
    binding_hash: str,
) -> GenieCancelResponse:
    """Decide the caller's Stop in one transaction; audit an accepted one.

    ``binding_hash`` is the progress token's verified 64-hex binding digest:
    the job row must carry the same one (400 otherwise). A job that is not
    the caller's turn is a 404.
    """

    params = {
        "job_id": payload.job_id,
        "actor_email": actor,
        "conversation_id": payload.conversation_id,
        "message_id": payload.message_id,
    }
    try:
        with lakebase.transaction() as conn:
            row = _one(conn, _LOCK_SQL, params)
            if row is None:
                raise HTTPException(status_code=404, detail="Genie completion job not found")
            if str(row["question_hash"]) != binding_hash:
                raise HTTPException(status_code=400, detail="question does not match the submitted Genie turn")
            prior = GenieJobStatus(str(row["status"]))
            outcome: _Outcome | None = _settled_outcome(row)
            status, lease_owner = prior, str(row["lease_owner"])
            if outcome is None:
                accepted = _one(conn, _ACCEPT_SQL, {"job_id": payload.job_id})
                if accepted is None:  # pragma: no cover - the row is locked above
                    raise RuntimeError("the locked job row changed before its cancel")
                status = GenieJobStatus(str(accepted["status"]))
                _audit_accepted(conn, actor=actor, payload=payload, prior=prior)
                outcome = "accepted"
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the flag rolled back with the audit row
        emit(
            log,
            "genie_job_cancel_requested",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="unavailable",
            error_type=type(exc).__name__,
            job_id=payload.job_id,
        )
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if outcome == "accepted" and prior is GenieJobStatus.RUNNING and lease_owner == jobs.PROCESS_ID:
        # The runner here reads only this mark at its cancel points. A queued
        # job is already terminal: its claim fails, so it needs no mark.
        jobs.CANCELS.mark(payload.job_id)
    emit(
        log,
        "genie_job_cancel_requested",
        dependency="lakebase",
        outcome=outcome,
        job_id=payload.job_id,
        status=prior.value,
    )
    return GenieCancelResponse(job_id=payload.job_id, outcome=_WIRE[outcome], status=status)


__all__ = ["GenieCancelRequest", "request_cancel", "require_completion_jobs"]
