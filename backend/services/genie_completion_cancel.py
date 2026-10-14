"""The owner's Stop on a job-backed Genie turn (audit 2026-09-21 ``genie-03``).

``POST /api/genie/message/cancel`` requests the cancel of the caller's own
completion job. It is decided in ONE Lakebase transaction on the turn's job
row, locked ``FOR UPDATE`` by its TURN KEY (actor, conversation, message):

* no job row yet (the Stop came before the complete's 202 named one, so the
  body carries no ``job_id``): the PRE-CANCEL. The turn's row is inserted
  already ``cancelled`` (``precancelled_at`` set, nothing recorded) with
  ``ON CONFLICT DO NOTHING`` on the table's turn UNIQUE, and its
  ``GENIE_TURN_CANCELLED`` audit row (status ``pre_job``) is written in the
  same transaction. A complete that arrives later joins that terminal row
  and runs nothing (``adoptable`` refuses it), so no RUN_GENIE row exists.
  When a concurrent complete committed the row first, the insert waits on it,
  inserts nothing, and the turn is re-locked and decided as below;
* a named ``job_id`` that is not the turn's row, or ids that cannot have a
  job, is a 404;
* a cancel already requested (or a job already ``cancelled``): ``cancelled``,
  an idempotent repeat with no write and no audit row;
* the governed record's commit point passed (``recorded_at`` set) or the job
  already ``succeeded``: the Stop came too late, and what the user is told
  depends on History, not on the job. After the transaction, the History
  settle below reads whether the turn's History row exists;
* ``failed`` or ``expired`` with nothing recorded: ``ended``, no write and no
  audit row;
* otherwise the cancel is ACCEPTED: ``cancel_requested_at`` is set by a
  conditional UPDATE that requires ``recorded_at IS NULL`` (the runner's
  commit point requires the converse, and a CHECK forbids both), a queued job
  ends ``cancelled`` at once, and the ``GENIE_TURN_CANCELLED`` audit row is
  written in the SAME transaction. One row per accepted cancel: the flag is
  only ever set once. An audit failure rolls the flag back (fail closed; a
  retry is safe).

The History settle (the 2026-09-30 Stop copy ruling) answers ``recorded``
only when the turn's ``mip_app.genie_messages`` row exists (``History`` shows
exactly those rows), whatever the job's status: a ``policy_blocked`` success
is never kept in History, and a runner that lost its lease after the record
may still have written it. Otherwise ``recording``, with the job's last read
status: ``running`` (the record is still being written), ``succeeded`` (an
answer History does not keep) or ``failed``/``expired`` (recording did not
finish). It reads once and, while the job still runs with no row, re-reads up
to four times 250 ms apart, so the common sub-second tail ends ``recorded``.
It never writes and never audits; an unreadable first read is a 503 (nothing
was written, so a retry is safe), and a later read error keeps the last
values. It deliberately does not use ``GENIE_MESSAGE_OWNERSHIP_SQL``: that
check keeps only ``source = 'genie'`` sessions and would deny rows History
shows.

``cancelled`` means this app will not verify or record the answer. It never
means Genie's own message was cancelled. Nothing typed by the user is
accepted: the body carries ids, the progress token and the 16-hex question
label the submit returned, never the question.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
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
    carries one is refused (422), so the prompt never travels on a Stop.
    ``job_id`` is the job the complete's 202 named; absent or null means the
    Stop came before that 202, and the turn's job is found (or pre-cancelled)
    by the turn's own ids."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    progress_token: str = Field(min_length=1, max_length=4_096)
    job_id: str | None = Field(default=None, pattern=jobs.JOB_ID_RE.pattern)
    #: The submit response's 16-hex label; checked against the token binding.
    question_hash: str = Field(pattern=QUESTION_LABEL_PATTERN)


# Locked by the TURN KEY (the table's UNIQUE), so a Stop finds the turn's job
# whether or not the browser knows its id yet.
_LOCK_SQL = """
SELECT job_id::text AS job_id, status, stage, question_hash, lease_owner,
       cancel_requested_at IS NOT NULL AS cancel_requested,
       recorded_at IS NOT NULL AS recorded
  FROM mip_app.genie_completion_jobs
 WHERE actor_email = %(actor_email)s
   AND conversation_id = %(conversation_id)s
   AND message_id = %(message_id)s
   FOR UPDATE
"""

# The pre-cancel: the turn's job row, created terminal. The turn UNIQUE makes
# a concurrent create_or_join (or a second Stop) wait on it, then join it.
_PRECANCEL_SQL = """
INSERT INTO mip_app.genie_completion_jobs (
  actor_email, conversation_id, message_id, question_hash, status, stage,
  lease_owner, lease_until, expires_at, deep, result_json, sections_json,
  cancel_requested_at, finished_at, precancelled_at
) VALUES (
  %(actor_email)s, %(conversation_id)s, %(message_id)s, %(question_hash)s,
  'cancelled', 'cancelled', %(lease_owner)s, now(),
  to_timestamp(%(expires_at_epoch)s::double precision), NULL, NULL, NULL,
  now(), now(), now()
)
ON CONFLICT (actor_email, conversation_id, message_id) DO NOTHING
RETURNING job_id::text AS job_id
"""

_ACCEPT_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET cancel_requested_at = now(), updated_at = now(),
       status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
       stage = CASE WHEN status = 'queued' THEN 'cancelled' ELSE stage END,
       finished_at = CASE WHEN status = 'queued' THEN now() ELSE finished_at END,
       sections_json = CASE WHEN status = 'queued' THEN NULL ELSE sections_json END
 WHERE job_id = %(job_id)s::uuid
   AND status IN ('queued', 'running')
   AND recorded_at IS NULL
   AND cancel_requested_at IS NULL
RETURNING status, lease_owner
"""

# The History settle's one read: the job's status and commit point, and
# whether the turn's History row exists. The message_id arm is primary; the
# label arm finds a row stored under the ``{source}-{hash}`` fallback id (a
# response with no message id), only when it was written after this job.
_HISTORY_SETTLE_SQL = """
SELECT j.status,
       j.recorded_at IS NOT NULL AS recorded,
       EXISTS (
           SELECT 1
             FROM mip_app.genie_messages m
            WHERE m.actor_email = j.actor_email
              AND m.conversation_id = j.conversation_id
              AND (
                  m.message_id = j.message_id
                  OR (m.question_hash = %(question_label)s AND m.created_at >= j.created_at)
              )
       ) AS history_row
  FROM mip_app.genie_completion_jobs j
 WHERE j.job_id = %(job_id)s::uuid
   AND j.actor_email = %(actor_email)s
"""

#: Re-reads after the first while the job runs with no History row yet.
_SETTLE_REREADS = 4
_SETTLE_INTERVAL_S = 0.25

_ENDED = frozenset({GenieJobStatus.FAILED, GenieJobStatus.EXPIRED})

#: What the request did: accepted (flag set, audited) or one of the no-ops.
_Outcome = Literal["accepted", "duplicate", "recorded", "recording", "ended"]
#: The locked read's verdict when the Stop changes nothing.
_Settled = Literal["duplicate", "needs_history", "ended"]
_WIRE: dict[_Outcome, Literal["cancelled", "recorded", "recording", "ended"]] = {
    "accepted": "cancelled",
    "duplicate": "cancelled",
    "recorded": "recorded",
    "recording": "recording",
    "ended": "ended",
}


def _one(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _settled_outcome(row: dict[str, Any]) -> _Settled | None:
    """The verdict of a Stop that changes nothing, or None to accept it."""

    status = GenieJobStatus(str(row["status"]))
    if row.get("cancel_requested") is True or status is GenieJobStatus.CANCELLED:
        return "duplicate"
    if row.get("recorded") is True or status is GenieJobStatus.SUCCEEDED:
        return "needs_history"
    if status in _ENDED:
        return "ended"
    return None


def _settle_read(lakebase: LakebaseClient, params: dict[str, Any]) -> tuple[GenieJobStatus, bool] | None:
    row = lakebase.fetchone(_HISTORY_SETTLE_SQL, params)
    if row is None:
        return None
    return GenieJobStatus(str(row["status"])), row.get("history_row") is True


def _settle_history(
    lakebase: LakebaseClient,
    *,
    actor: str,
    payload: GenieCancelRequest,
    job_id: str,
    status: GenieJobStatus,
) -> tuple[Literal["recorded", "recording"], GenieJobStatus]:
    """``recorded`` once the turn's History row exists, else ``recording``
    with the last status read. Reads only: no UPDATE, INSERT or audit."""

    params = {"job_id": job_id, "actor_email": actor, "question_label": payload.question_hash}
    try:
        read = _settle_read(lakebase, params)
    except Exception as exc:  # noqa: BLE001 - nothing was written; a retry is safe
        emit(
            log,
            "genie_job_cancel_requested",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="unavailable",
            error_type=type(exc).__name__,
            job_id=job_id,
        )
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    history = False
    if read is not None:
        status, history = read
    rereads = 0
    while status is GenieJobStatus.RUNNING and not history and rereads < _SETTLE_REREADS:
        time.sleep(_SETTLE_INTERVAL_S)
        rereads += 1
        try:
            read = _settle_read(lakebase, params)
        except Exception:  # noqa: BLE001 - a later read error keeps the last values
            break
        if read is None:
            break
        status, history = read
    return ("recorded" if history else "recording"), status


def _audit_accepted(conn: Any, *, actor: str, payload: GenieCancelRequest, job_id: str, prior: str) -> None:
    """The one GENIE_TURN_CANCELLED row of an accepted cancel; ``prior`` is the
    job's status before it, or ``pre_job`` for a pre-cancel."""

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
            "genie_job_id": job_id,
            "status": prior,
        },
        event_type="GENIE_TURN_CANCELLED",
    )


#: The audit metadata status of a pre-cancel: the turn had no job yet.
PRE_JOB_STATUS = "pre_job"


@dataclass(frozen=True)
class _Decision:
    """What the locked turn decided, carried out of the transaction."""

    job_id: str
    #: The job's status before the Stop; None for a pre-cancel.
    prior: GenieJobStatus | None
    status: GenieJobStatus
    lease_owner: str
    #: None: accepted (flag set, audited); else the settled verdict.
    verdict: _Settled | Literal["pre_cancelled"] | None


def _decide_locked(conn: Any, row: dict[str, Any], *, actor: str, payload: GenieCancelRequest, binding_hash: str) -> _Decision:
    job_id = str(row["job_id"])
    if payload.job_id is not None and payload.job_id != job_id:
        raise HTTPException(status_code=404, detail="Genie completion job not found")
    if str(row["question_hash"]) != binding_hash:
        raise HTTPException(status_code=400, detail="question does not match the submitted Genie turn")
    prior = GenieJobStatus(str(row["status"]))
    verdict = _settled_outcome(row)
    status = prior
    if verdict is None:
        accepted = _one(conn, _ACCEPT_SQL, {"job_id": job_id})
        if accepted is None:  # pragma: no cover - the row is locked above
            raise RuntimeError("the locked job row changed before its cancel")
        status = GenieJobStatus(str(accepted["status"]))
        _audit_accepted(conn, actor=actor, payload=payload, job_id=job_id, prior=prior.value)
    return _Decision(job_id, prior, status, str(row["lease_owner"]), verdict)


def _decide(
    conn: Any, *, actor: str, payload: GenieCancelRequest, binding_hash: str, expires_at_epoch: int
) -> _Decision:
    """The Stop, decided on the turn's locked row (or its pre-cancel)."""

    turn = {"actor_email": actor, "conversation_id": payload.conversation_id, "message_id": payload.message_id}
    row = _one(conn, _LOCK_SQL, turn)
    if row is not None:
        return _decide_locked(conn, row, actor=actor, payload=payload, binding_hash=binding_hash)
    if payload.job_id is not None or not jobs.job_turn_ids_eligible(payload.conversation_id, payload.message_id):
        raise HTTPException(status_code=404, detail="Genie completion job not found")
    inserted = _one(
        conn,
        _PRECANCEL_SQL,
        {
            **turn,
            "question_hash": binding_hash,
            "lease_owner": jobs.PROCESS_ID,
            "expires_at_epoch": int(expires_at_epoch),
        },
    )
    if inserted is not None:
        job_id = str(inserted["job_id"])
        _audit_accepted(conn, actor=actor, payload=payload, job_id=job_id, prior=PRE_JOB_STATUS)
        return _Decision(job_id, None, GenieJobStatus.CANCELLED, jobs.PROCESS_ID, "pre_cancelled")
    # A concurrent complete committed the turn's job first: the insert waited
    # on it and inserted nothing. Decide on that row, locked, as any Stop.
    row = _one(conn, _LOCK_SQL, turn)
    if row is None:  # pragma: no cover - the conflicting row is committed
        raise RuntimeError("the turn's job row vanished after its conflict")
    return _decide_locked(conn, row, actor=actor, payload=payload, binding_hash=binding_hash)


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
    expires_at_epoch: int = 0,
) -> GenieCancelResponse:
    """Decide the caller's Stop in one transaction; audit an accepted one.

    ``binding_hash`` is the progress token's verified 64-hex binding digest:
    the job row must carry the same one (400 otherwise), and a pre-cancelled
    row is created with it. A job that is not the caller's turn is a 404.
    ``expires_at_epoch`` (the token's ``exp``) is a pre-cancelled row's
    ``expires_at``, as for any job the turn's complete would create.
    """

    try:
        with lakebase.transaction() as conn:
            decision = _decide(
                conn, actor=actor, payload=payload, binding_hash=binding_hash, expires_at_epoch=expires_at_epoch
            )
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
    job_id, prior, status, verdict = decision.job_id, decision.prior, decision.status, decision.verdict
    outcome: _Outcome
    if verdict is None or verdict == "pre_cancelled":
        outcome = "accepted"
    elif verdict == "needs_history":
        outcome, status = _settle_history(lakebase, actor=actor, payload=payload, job_id=job_id, status=status)
    else:
        outcome = verdict
    if outcome == "accepted" and prior is GenieJobStatus.RUNNING and decision.lease_owner == jobs.PROCESS_ID:
        # The runner here reads only this mark at its cancel points, so it is
        # set only while that runner still tracks the job (it may have seen
        # the cancel at its commit point and ended already). A queued job is
        # already terminal: its claim fails, so it needs no mark; nor does a
        # pre-cancelled one, which no runner ever claims.
        jobs.HEARTBEAT.mark_cancelled(job_id)
    emit(
        log,
        "genie_job_cancel_requested",
        dependency="lakebase",
        outcome="pre_cancelled" if verdict == "pre_cancelled" else outcome,
        job_id=job_id,
        status=prior.value if prior is not None else PRE_JOB_STATUS,
    )
    return GenieCancelResponse(job_id=job_id, outcome=_WIRE[outcome], status=status)


__all__ = ["GenieCancelRequest", "request_cancel", "require_completion_jobs"]
