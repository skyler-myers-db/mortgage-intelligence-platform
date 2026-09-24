"""The governed completion of a live Genie turn, run exactly once per turn.

Audit 2026-09-21 ``genie-01``. ``/message/complete`` used to run the whole
governed tail (respond_existing, the output guard, the RUN_GENIE audit row,
token issuance, session recording and, for deep asks, a 90-200 s sweep)
inside one blocking POST, and a reloaded or retried complete ran all of it a
second time. The tail now lives in :func:`complete_governed_turn` and runs:

* as a JOB (``respond_async``): the request creates the turn's one job row,
  adopts its Genie concurrency slot, enqueues :func:`run_completion_job` and
  answers 202; the browser polls ``/message/status``;
* INLINE (older tabs): the request creates the job, runs the tail in its own
  thread holding its own slot, and records the outcome on the job, so a
  retried legacy complete JOINS it and waits instead of running it again;
* WITHOUT A JOB when the table is not provisioned yet (the App promoted
  ahead of its migration): exactly today's inline path.

Single-shot audit and token issuance: one (actor, conversation, message) has
exactly one job row (the table's UNIQUE key) and exactly one claimant (the
queued -> running claim is conditional on this process's lease), so the
RUN_GENIE row is written and the answer's tokens issued once per turn. The
already_recorded replay defence stays for turns completed before jobs
existed. Crash window: a process that dies between the audit write and
``succeed`` leaves at most one RUN_GENIE row for a job that then expires
(its lease stops renewing and the next read expires it); the answer is not
recoverable from the job, so the browser shows the expired hint.

The runner never reads identity from ambient state: the enqueuing request
hands it the actor, the verified question, the live-campaign marker and the
DI-resolved repository, audit store and Lakebase client, so dependency
overrides hold. It runs in a FRESH context on its own pool with the
request's correlation id bound (audit rows join the request log); the
request's Server-Timing collector is absent there and records nothing.
"""

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock

from fastapi import HTTPException
from pydantic import Field

from backend.config.settings import settings
from backend.services import genie_completion_jobs as jobs
from backend.services.audit_store import AuditStore
from backend.services.backpressure import DependencySlot
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_audit import genie_audit_entity_id
from backend.services.genie_client import GenieClientError
from backend.services.genie_completion_delivery import deauthorized_result, delivered_response
from backend.services.genie_completion_jobs import GenieCompletionJob
from backend.services.genie_completion_stages import (
    GenieJobFailureKind,
    GenieJobStage,
    GenieJobStatus,
    report_stage,
    stage_sink,
)
from backend.services.genie_deterministic import (
    _block_unsafe_genie_output,
    _required_audit_write,
)
from backend.services.genie_message_policy import (
    GenieCompleteRequest,
    GenieMessageRequest,
    genie_response_has_unsafe_visible_text,
)
from backend.services.genie_session_guard import GENIE_MESSAGE_OWNERSHIP_SQL
from backend.services.genie_turn_record import _finalize_genie_response
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit, set_correlation_id
from backend.services.repositories.protocols import GenieAnswerRepository
from backend.services.resilience import DependencyDownError

log = logging.getLogger("mip-genie")

#: A legacy complete that joined a running job waits at most this long (and
#: never past the token's expiry), polling the job row once a second.
JOINED_WAIT_MAX_S = 280.0
JOINED_POLL_S = 1.0


class GenieCompleteAsyncRequest(GenieCompleteRequest):
    """``/message/complete`` body. ``respond_async`` opts in to the 202 job.

    Older servers ignore the extra field and answer 200, which the browser
    recognises (deploy-skew safety in both directions).
    """

    respond_async: bool = False


class GenieCompletionJobStatusRequest(GenieCompleteRequest):
    """``/message/status`` body: a POST so the token never lands in a URL.

    ``question`` is hash-checked against the progress token exactly as on
    complete, and is how the answer gets its question back: the stored job
    result never holds it.
    """

    job_id: str = Field(pattern=jobs.JOB_ID_RE.pattern)


@dataclass(frozen=True)
class GovernedTurn:
    """Everything the governed tail needs, resolved by the enqueuing request."""

    actor: str
    question: str
    conversation_id: str
    message_id: str
    live_campaign_run_marker: str | None
    repo: GenieAnswerRepository
    audit: AuditStore
    lakebase: LakebaseClient


def complete_governed_turn(turn: GovernedTurn) -> GenieMessageResponse:
    """The governed completion of one submitted live turn (moved verbatim in
    meaning from the complete route; audit kwargs byte-identical)."""

    actor = turn.actor
    lakebase = turn.lakebase
    audit = turn.audit
    live_campaign_run_marker = turn.live_campaign_run_marker
    guard_payload = GenieMessageRequest(
        question=turn.question,
        conversation_id=turn.conversation_id,
    )
    try:
        result = turn.repo.respond_existing(
            turn.question,
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
        )
    except GenieClientError as exc:
        raise DependencyDownError(
            "genie",
            reason="genie client returned an unrecoverable response",
            last_error=exc,
            kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
        ) from exc
    report_stage(GenieJobStage.FINALIZING)
    if genie_response_has_unsafe_visible_text(result):
        blocked = _block_unsafe_genie_output(
            audit,
            actor=actor,
            payload=guard_payload,
            response=result,
        )
        return _finalize_genie_response(
            lakebase,
            actor=actor,
            response=blocked,
            live_campaign_run_marker=live_campaign_run_marker,
        )
    # Replay hygiene (2026-07-31 adversarial review): a turn completed before
    # completion jobs existed has its durable genie_messages row as the
    # replay marker; repeats re-serve the governed answer without a second
    # audit row. The job row is the primary single-shot guard now.
    already_recorded = False
    if result.message_id:
        try:
            already_recorded = (
                lakebase.fetchone(
                    GENIE_MESSAGE_OWNERSHIP_SQL,
                    {
                        "actor_email": actor,
                        "conversation_id": turn.conversation_id,
                        "message_id": result.message_id,
                    },
                )
                is not None
            )
        except LakebaseError:
            # Fail toward auditing: an unreadable ledger must never suppress
            # the audit row for a turn that may not have one yet.
            already_recorded = False
    if already_recorded:
        emit(
            log,
            "genie_complete_replayed",
            dependency="lakebase",
            outcome="deduplicated",
            conversation_id=turn.conversation_id,
            message_id=result.message_id,
        )
    else:
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.run_query",
            entity_type="genie_message",
            entity_id=genie_audit_entity_id(result),
            payload_json={
                "conversation_id": result.conversation_id,
                "message_id": result.message_id,
                "question_hash": result.question_hash,
                "row_count": result.row_count or 0,
                "source_assets": result.trusted_assets,
                "visualization_kind": result.visualization.kind if result.visualization else None,
            },
            event_type="RUN_GENIE",
        )
    return _finalize_genie_response(
        lakebase,
        actor=actor,
        response=result,
        live_campaign_run_marker=live_campaign_run_marker,
    )


def failure_kind_for(exc: BaseException) -> GenieJobFailureKind:
    """Map a failed completion to its canned family; never exception text."""

    if isinstance(exc, GenieClientError):
        return GenieJobFailureKind.UPSTREAM_ERROR
    if isinstance(exc, DependencyDownError):
        if isinstance(exc.__cause__, GenieClientError):
            return GenieJobFailureKind.UPSTREAM_ERROR
        return GenieJobFailureKind.DEPENDENCY_DOWN
    if isinstance(exc, HTTPException) and exc.status_code == 503:
        return GenieJobFailureKind.DEPENDENCY_DOWN
    return GenieJobFailureKind.INTERNAL


# ------------------------------------------------------------- job runner

_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = Lock()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(
                max_workers=max(1, int(settings.mip_genie_concurrency_limit)),
                thread_name_prefix="genie-job",
            )
        return _EXECUTOR


def _finished(job_id: str, status: GenieJobStatus, started: float, kind: GenieJobFailureKind | None) -> None:
    emit(
        log,
        "genie_job_finished",
        dependency="genie",
        duration_ms=round((time.monotonic() - started) * 1000, 1),
        outcome=status.value,
        status=status.value,
        failure_kind=kind.value if kind else None,
        job_id=job_id,
    )


def _fail_job(turn: GovernedTurn, job_id: str, exc: BaseException, started: float) -> None:
    kind = failure_kind_for(exc)
    if kind is GenieJobFailureKind.INTERNAL:
        emit(
            log,
            "genie_job_internal_error",
            level=logging.ERROR,
            dependency="genie",
            outcome="failed",
            error_type=type(exc).__name__,
            job_id=job_id,
        )
    try:
        jobs.fail(turn.lakebase, job_id, kind)
    except Exception as fail_exc:  # noqa: BLE001 - the lease then expires it
        emit(
            log,
            "genie_job_fail_write_failed",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="skipped",
            error_type=type(fail_exc).__name__,
            job_id=job_id,
        )
    _finished(job_id, GenieJobStatus.FAILED, started, kind)


def _store_success(turn: GovernedTurn, job_id: str, response: GenieMessageResponse, started: float) -> None:
    stored = jobs.succeed(turn.lakebase, job_id, deauthorized_result(response, turn.question))
    if not stored:
        # Lost the lease (expired by a reader after missed heartbeats): the
        # audit row exists, the answer cannot be served from this job.
        emit(log, "genie_job_lease_lost", level=logging.WARNING, dependency="lakebase", outcome="lost", job_id=job_id)
    _finished(job_id, GenieJobStatus.SUCCEEDED if stored else GenieJobStatus.EXPIRED, started, None)


def _run_job(turn: GovernedTurn, job_id: str, slot: DependencySlot | None, correlation_id: str) -> None:
    started = time.monotonic()
    set_correlation_id(correlation_id)
    try:
        if not jobs.claim(turn.lakebase, job_id):
            emit(log, "genie_job_claim_lost", level=logging.WARNING, dependency="lakebase", outcome="lost", job_id=job_id)
            return

        def write(stage: GenieJobStage, done: int | None, planned: int | None) -> None:
            jobs.write_stage(turn.lakebase, job_id, stage, done, planned)

        with stage_sink(write):
            response = complete_governed_turn(turn)
        _store_success(turn, job_id, response, started)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a canned job failure
        _fail_job(turn, job_id, exc, started)
    finally:
        jobs.HEARTBEAT.untrack(job_id)
        if slot is not None:
            slot.release()


def run_completion_job(
    turn: GovernedTurn,
    job: GenieCompletionJob,
    *,
    slot: DependencySlot | None,
    correlation_id: str,
) -> None:
    """Enqueue ``job``: its lease is renewed from now until it finishes."""

    jobs.HEARTBEAT.track(job.job_id, turn.lakebase)
    try:
        # A fresh, EMPTY context per job: nothing leaks between jobs sharing a
        # pool thread, and the request's Server-Timing collector is absent.
        _executor().submit(contextvars.Context().run, _run_job, turn, job.job_id, slot, correlation_id)
    except BaseException:
        jobs.HEARTBEAT.untrack(job.job_id)
        if slot is not None:
            slot.release()
        raise


def run_inline_job(turn: GovernedTurn, job: GenieCompletionJob) -> GenieMessageResponse:
    """Legacy complete that created the job: today's inline path, recorded."""

    started = time.monotonic()
    jobs.HEARTBEAT.track(job.job_id, turn.lakebase)
    try:
        if not jobs.claim(turn.lakebase, job.job_id):
            raise HTTPException(status_code=503, detail=safe_dependency_detail("genie"))
        try:
            response = complete_governed_turn(turn)
        except BaseException as exc:
            _fail_job(turn, job.job_id, exc, started)
            raise
        # The caller gets the live object (tokens as issued); the job keeps
        # the de-authorized copy for a joined retry. Recording it is best
        # effort: the governed answer is already audited and in hand.
        try:
            _store_success(turn, job.job_id, response, started)
        except Exception as exc:  # noqa: BLE001 - a joiner then sees the lease expire
            emit(
                log,
                "genie_job_store_failed",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="skipped",
                error_type=type(exc).__name__,
                job_id=job.job_id,
            )
        return response
    finally:
        jobs.HEARTBEAT.untrack(job.job_id)


def await_joined_job(turn: GovernedTurn, job: GenieCompletionJob) -> GenieMessageResponse:
    """Legacy complete that JOINED a job: wait for it, never run it again."""

    deadline = time.monotonic() + min(
        JOINED_WAIT_MAX_S, max(0.0, (job.expires_at - job.db_now).total_seconds())
    )
    current: GenieCompletionJob | None = job
    while current is not None and not current.terminal and time.monotonic() < deadline:
        time.sleep(JOINED_POLL_S)
        current = jobs.read_for_actor(
            turn.lakebase,
            job_id=job.job_id,
            actor=turn.actor,
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
        )
    if current is not None and current.status is GenieJobStatus.SUCCEEDED and current.result_json:
        response = delivered_response(
            current.result_json,
            question=turn.question,
            actor=turn.actor,
            live_campaign_run_marker=turn.live_campaign_run_marker,
        )
        if response is not None:
            return response
    # Failed, expired or still running at the bound: a non-retryable 503 and,
    # above all, no second completion and no second audit row.
    raise HTTPException(status_code=503, detail=safe_dependency_detail("genie"))


def _reset_executor_for_tests() -> None:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        executor, _EXECUTOR = _EXECUTOR, None
    if executor is not None:
        executor.shutdown(wait=True)


__all__ = [
    "GenieCompleteAsyncRequest",
    "GenieCompletionJobStatusRequest",
    "GovernedTurn",
    "await_joined_job",
    "complete_governed_turn",
    "failure_kind_for",
    "run_completion_job",
    "run_inline_job",
]
