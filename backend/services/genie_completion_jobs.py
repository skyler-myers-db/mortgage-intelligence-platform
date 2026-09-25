"""Lakebase store for Genie completion jobs (audit 2026-09-21 ``genie-01``).

One row of ``mip_app.genie_completion_jobs`` per (actor, conversation,
message): the governed completion of one submitted live turn. Postgres
``now()`` is the only clock, so every worker agrees on leases and expiry.

Lifecycle::

    create_or_join  INSERT ... ON CONFLICT DO NOTHING (queued, leased to this
                    process); a conflict joins the existing row instead.
    claim           queued -> running, only by the lease owner, never after a
                    cancel was requested.
    heartbeat       one daemon thread per process renews lease_until for the
                    process's own queued/running jobs every 10 s, and marks
                    CANCELS for any whose cancel another process accepted.
    write_stage     best effort; a failure is a throttled WARNING, never an
                    answer failure. The runner's stages go through
                    STAGE_WRITER: one daemon thread per process, latest stage
                    wins per job, never on the governed thread.
    succeed / fail  terminal, only by the lease owner of a running job; the
                    record's commit point and the cancelled end live in
                    genie_completion_record (audit ``genie-03``).
    read_for_actor  the status poll: expires a job whose lease went stale (its
                    process died) or whose served answer is past expires_at,
                    then returns it. Any mismatch of job, actor, conversation
                    or message is None.

Expiry happens on read (the status poll, a joined complete) and in a bounded
sweep every new job runs, never on process start: a live runner on another
worker keeps renewing its lease and is never expired, and a dead process
stops renewing, so the next read anywhere expires its jobs. Nothing is
deleted; an expired row keeps its metadata and loses ``result_json``.

The stored ``result_json`` never carries question text or live authorization;
see ``genie_completion_delivery``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from fastapi import HTTPException

from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_completion_stages import (
    GenieJobFailureKind,
    GenieJobStage,
    GenieJobStatus,
)
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit
from backend.services.resilience import DependencyDownError

log = logging.getLogger("mip-genie-jobs")

#: This process's lease identity. A restarted process gets a new one, so it
#: can never renew, claim or finish a job the previous process leased.
PROCESS_ID = uuid4().hex

LEASE_S = 45
HEARTBEAT_S = 10.0
SWEEP_LIMIT = 50
PROBE_TTL_S = 60.0
_STAGE_WARNING_INTERVAL_S = 60.0

#: The same opaque-id grammar as the table CHECKs. Ids outside it cannot get
#: a job: submit then advertises none, an older tab's completion runs inline
#: and an async complete is refused.
JOB_TURN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_BINDING_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

_LEASE = f"now() + interval '{LEASE_S} seconds'"
_COLUMNS = """job_id::text AS job_id, status, stage, parts_done, parts_planned,
       failure_kind, result_json, lease_owner, lease_until, expires_at, question_hash,
       cancel_requested_at IS NOT NULL AS cancel_requested,
       recorded_at IS NOT NULL AS recorded, deep, now() AS db_now"""

# Present only with the 2026_09_25 columns too: an App promoted ahead of that
# migration completes inline instead of 503ing every job statement.
_PROBE_SQL = """
SELECT to_regclass('mip_app.genie_completion_jobs') IS NOT NULL
   AND (SELECT count(*) FROM pg_attribute
         WHERE attrelid = to_regclass('mip_app.genie_completion_jobs')
           AND attname IN ('cancel_requested_at', 'recorded_at', 'deep')
           AND NOT attisdropped) = 3 AS present
"""

_SWEEP_SQL = """
UPDATE mip_app.genie_completion_jobs AS job
   SET status = 'expired', stage = 'expired', result_json = NULL,
       updated_at = now(), finished_at = COALESCE(job.finished_at, now())
  FROM (
        SELECT job_id, status AS prior_status
          FROM mip_app.genie_completion_jobs
         WHERE (status IN ('queued', 'running') AND lease_until < now())
            OR (status = 'succeeded' AND expires_at < now())
         LIMIT %(limit)s
           FOR UPDATE SKIP LOCKED
       ) AS stale
 WHERE job.job_id = stale.job_id
RETURNING job.job_id::text AS job_id, stale.prior_status AS prior_status
"""

_INSERT_SQL = f"""
INSERT INTO mip_app.genie_completion_jobs (
  actor_email, conversation_id, message_id, question_hash, status, stage,
  lease_owner, lease_until, expires_at, deep
) VALUES (
  %(actor_email)s, %(conversation_id)s, %(message_id)s, %(question_hash)s,
  'queued', 'queued', %(lease_owner)s, {_LEASE},
  to_timestamp(%(expires_at_epoch)s::double precision), %(deep)s
)
ON CONFLICT (actor_email, conversation_id, message_id) DO NOTHING
RETURNING {_COLUMNS}
"""

_SELECT_TURN_SQL = f"""
SELECT {_COLUMNS}
  FROM mip_app.genie_completion_jobs
 WHERE actor_email = %(actor_email)s
   AND conversation_id = %(conversation_id)s
   AND message_id = %(message_id)s
"""

_SELECT_JOB_SQL = f"""
SELECT {_COLUMNS}
  FROM mip_app.genie_completion_jobs
 WHERE job_id = %(job_id)s::uuid
   AND actor_email = %(actor_email)s
   AND conversation_id = %(conversation_id)s
   AND message_id = %(message_id)s
"""

_EXPIRE_JOB_SQL = f"""
UPDATE mip_app.genie_completion_jobs
   SET status = 'expired', stage = 'expired', result_json = NULL,
       updated_at = now(), finished_at = COALESCE(finished_at, now())
 WHERE job_id = %(job_id)s::uuid
   AND status = %(prior_status)s
   AND lease_until = %(prior_lease_until)s
RETURNING {_COLUMNS}
"""

_CLAIM_SQL = f"""
UPDATE mip_app.genie_completion_jobs
   SET status = 'running', lease_until = {_LEASE}, updated_at = now()
 WHERE job_id = %(job_id)s::uuid AND status = 'queued' AND lease_owner = %(lease_owner)s
   AND cancel_requested_at IS NULL
RETURNING job_id::text AS job_id
"""

_HEARTBEAT_SQL = f"""
UPDATE mip_app.genie_completion_jobs
   SET lease_until = {_LEASE}, updated_at = now()
 WHERE job_id = ANY(%(job_ids)s::uuid[])
   AND lease_owner = %(lease_owner)s
   AND status IN ('queued', 'running')
RETURNING job_id::text AS job_id, cancel_requested_at IS NOT NULL AS cancel_requested
"""

_STAGE_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET stage = %(stage)s, parts_done = %(parts_done)s, parts_planned = %(parts_planned)s,
       updated_at = now()
 WHERE job_id = %(job_id)s::uuid AND status = 'running' AND lease_owner = %(lease_owner)s
"""

_SUCCEED_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET status = 'succeeded', stage = 'done', result_json = %(result_json)s::jsonb,
       parts_done = NULL, parts_planned = NULL, finished_at = now(), updated_at = now()
 WHERE job_id = %(job_id)s::uuid AND status = 'running' AND lease_owner = %(lease_owner)s
RETURNING job_id::text AS job_id
"""

_FAIL_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET status = 'failed', stage = 'failed', failure_kind = %(failure_kind)s,
       parts_done = NULL, parts_planned = NULL, finished_at = now(), updated_at = now()
 WHERE job_id = %(job_id)s::uuid
   AND status IN ('queued', 'running')
   AND lease_owner = %(lease_owner)s
RETURNING job_id::text AS job_id
"""


@dataclass(frozen=True)
class GenieCompletionJob:
    """One job row as the app sees it. ``db_now`` is Postgres' clock at read."""

    job_id: str
    status: GenieJobStatus
    stage: GenieJobStage
    parts_done: int | None
    parts_planned: int | None
    failure_kind: GenieJobFailureKind | None
    result_json: dict[str, Any] | None
    lease_owner: str
    lease_until: datetime
    expires_at: datetime
    question_hash: str
    db_now: datetime
    cancel_requested: bool = False
    recorded: bool = False
    #: The submit-time deep flag; None on rows from before 2026_09_25.
    deep: bool | None = None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL


@dataclass(frozen=True)
class JobEnrollment:
    job: GenieCompletionJob
    created: bool


_TERMINAL = frozenset(
    {GenieJobStatus.SUCCEEDED, GenieJobStatus.FAILED, GenieJobStatus.EXPIRED, GenieJobStatus.CANCELLED}
)
_LIVE = frozenset({GenieJobStatus.QUEUED, GenieJobStatus.RUNNING})


def _job_from_row(row: dict[str, Any]) -> GenieCompletionJob:
    raw_result = row.get("result_json")
    if isinstance(raw_result, str):
        raw_result = json.loads(raw_result)
    failure = row.get("failure_kind")
    return GenieCompletionJob(
        job_id=str(row["job_id"]),
        status=GenieJobStatus(str(row["status"])),
        stage=GenieJobStage(str(row["stage"])),
        parts_done=_optional_int(row.get("parts_done")),
        parts_planned=_optional_int(row.get("parts_planned")),
        failure_kind=GenieJobFailureKind(str(failure)) if failure else None,
        result_json=cast(dict[str, Any], raw_result) if isinstance(raw_result, dict) else None,
        lease_owner=str(row["lease_owner"]),
        lease_until=cast(datetime, row["lease_until"]),
        expires_at=cast(datetime, row["expires_at"]),
        question_hash=str(row["question_hash"]),
        db_now=cast(datetime, row["db_now"]),
        cancel_requested=row.get("cancel_requested") is True,
        recorded=row.get("recorded") is True,
        deep=row["deep"] if isinstance(row.get("deep"), bool) else None,
    )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))


# --------------------------------------------------------------- the probe

_PROBE_CACHE: weakref.WeakKeyDictionary[Any, tuple[float, bool]] = weakref.WeakKeyDictionary()
_PROBE_LOCK = threading.Lock()


def completion_jobs_available(lakebase: LakebaseClient) -> bool:
    """Whether the job table and its 2026_09_25 columns exist (cached 60 s).

    False when the App was promoted ahead of the Lakebase migration (or the
    probe itself failed; that is not cached): submit tells the browser not to
    ask for a job, an older tab's complete runs today's inline completion, the
    real governed path, and an async complete is refused with a non-retryable
    503. One WARNING per absence.
    """

    now = time.monotonic()
    cached = _probe_cache_get(lakebase)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        row = lakebase.fetchone(_PROBE_SQL)
    except Exception as exc:  # noqa: BLE001 - no job for this request
        emit(
            log,
            "genie_jobs_probe_failed",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="unavailable",
            error_type=type(exc).__name__,
        )
        return False
    present = row is not None and row.get("present") is True
    previous = _probe_cache_get(lakebase)
    _probe_cache_put(lakebase, (now + PROBE_TTL_S, present))
    if not present and (previous is None or previous[1]):
        emit(
            log,
            "genie_jobs_table_absent",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="inline_completion",
        )
    return present


def _probe_cache_get(lakebase: LakebaseClient) -> tuple[float, bool] | None:
    try:
        with _PROBE_LOCK:
            return _PROBE_CACHE.get(lakebase)
    except TypeError:  # a client that cannot be weakly referenced: no cache
        return None


def _probe_cache_put(lakebase: LakebaseClient, value: tuple[float, bool]) -> None:
    try:
        with _PROBE_LOCK:
            _PROBE_CACHE[lakebase] = value
    except TypeError:
        return


def job_turn_ids_eligible(conversation_id: str, message_id: str) -> bool:
    return bool(JOB_TURN_ID_RE.fullmatch(conversation_id) and JOB_TURN_ID_RE.fullmatch(message_id))


# --------------------------------------------------------- create / join


def sweep_expired(lakebase: LakebaseClient) -> int:
    """Expire up to ``SWEEP_LIMIT`` stale jobs of ANY actor; NULL their result.

    Runs with every new job, so no stored answer outlives its window by more
    than one later turn even when its tab never polls again.
    """

    rows = lakebase.fetchall(_SWEEP_SQL, {"limit": SWEEP_LIMIT}, limit=SWEEP_LIMIT)
    for row in rows:
        prior = str(row.get("prior_status") or "")
        emit(
            log,
            "genie_job_expired",
            dependency="lakebase",
            outcome="expired",
            reason="past_expiry" if prior == GenieJobStatus.SUCCEEDED else "stale_lease",
            via="sweep",
            job_id=str(row.get("job_id") or ""),
        )
    return len(rows)


def create_or_join(
    lakebase: LakebaseClient,
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
    question_hash: str,
    expires_at_epoch: int,
    deep: bool,
) -> JobEnrollment:
    """Create this turn's job, or join the one that already exists.

    ``question_hash`` is the progress token's binding digest. A stored digest
    that differs is a 400: it cannot be the same governed turn.
    """

    if not _BINDING_HASH_RE.fullmatch(question_hash):
        raise HTTPException(status_code=400, detail="question does not match the submitted Genie turn")
    params = {
        "actor_email": actor,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "question_hash": question_hash,
        "lease_owner": PROCESS_ID,
        "expires_at_epoch": int(expires_at_epoch),
        "deep": bool(deep),
    }
    try:
        sweep_expired(lakebase)
        row = lakebase.fetchone(_INSERT_SQL, params)
        created = row is not None
        if row is None:
            row = lakebase.fetchone(_SELECT_TURN_SQL, params)
    except (LakebaseError, DependencyDownError) as exc:
        raise _unavailable(exc) from exc
    if row is None:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))
    job = _job_from_row(row)
    if job.question_hash != question_hash:
        raise HTTPException(status_code=400, detail="question does not match the submitted Genie turn")
    emit(
        log,
        "genie_job_enqueued",
        dependency="lakebase",
        outcome="created" if created else "joined",
        joined=not created,
        job_id=job.job_id,
        status=job.status.value,
    )
    return JobEnrollment(job=job, created=created)


def claim(lakebase: LakebaseClient, job_id: str) -> bool:
    """queued -> running, only for this process's lease and never after a
    cancel was requested. False when lost."""

    row = lakebase.fetchone(_CLAIM_SQL, {"job_id": job_id, "lease_owner": PROCESS_ID})
    return row is not None


# ----------------------------------------------------------------- cancels


class _CancelMarks:
    """This process's own jobs whose cancel was accepted (by the cancel route
    here, or by another process as seen through the heartbeat). The governed
    thread reads only this set, never Lakebase, for its cancel points."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._marked: set[str] = set()

    def mark(self, job_id: str) -> None:
        with self._lock:
            self._marked.add(job_id)

    def is_marked(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._marked

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._marked.discard(job_id)


CANCELS = _CancelMarks()


# ------------------------------------------------------------- heartbeat


class _Heartbeat:
    """Renews this process's leases; one daemon thread, started on demand."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, LakebaseClient] = {}
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    def track(self, job_id: str, lakebase: LakebaseClient) -> None:
        with self._lock:
            self._jobs[job_id] = lakebase
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="genie-job-heartbeat", daemon=True
                )
                self._thread.start()

    def untrack(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def tracked(self) -> set[str]:
        with self._lock:
            return set(self._jobs)

    def beat(self) -> None:
        """One renewal pass: one UPDATE per client for all its tracked jobs;
        its RETURNING marks CANCELS for a cancel another process accepted."""

        with self._lock:
            by_client: dict[int, tuple[LakebaseClient, list[str]]] = {}
            for job_id, client in self._jobs.items():
                by_client.setdefault(id(client), (client, []))[1].append(job_id)
        for client, job_ids in by_client.values():
            try:
                rows = client.fetchall(
                    _HEARTBEAT_SQL,
                    {"job_ids": job_ids, "lease_owner": PROCESS_ID},
                    limit=len(job_ids),
                )
            except Exception as exc:  # noqa: BLE001 - the next beat retries
                _warn_throttled("genie_job_heartbeat_failed", error_type=type(exc).__name__)
                continue
            for row in rows:
                if row.get("cancel_requested") is True:
                    CANCELS.mark(str(row["job_id"]))

    def _loop(self) -> None:
        while True:
            self._wake.wait(HEARTBEAT_S)
            self._wake.clear()
            self.beat()


HEARTBEAT = _Heartbeat()


# ------------------------------------------------------------ stage / end

_WARNED_AT: dict[str, float] = {}
_WARN_LOCK = threading.Lock()


def _warn_throttled(event: str, **fields: Any) -> None:
    now = time.monotonic()
    with _WARN_LOCK:
        last = _WARNED_AT.get(event)
        if last is not None and now - last < _STAGE_WARNING_INTERVAL_S:
            return
        _WARNED_AT[event] = now
    emit(log, event, level=logging.WARNING, dependency="lakebase", outcome="skipped", **fields)


def write_stage(
    lakebase: LakebaseClient,
    job_id: str,
    stage: GenieJobStage,
    parts_done: int | None = None,
    parts_planned: int | None = None,
) -> None:
    """Best effort: a failed stage write never fails the answer."""

    try:
        lakebase.execute(
            _STAGE_SQL,
            {
                "job_id": job_id,
                "stage": GenieJobStage(stage).value,
                "parts_done": parts_done,
                "parts_planned": parts_planned,
                "lease_owner": PROCESS_ID,
            },
        )
    except Exception as exc:  # noqa: BLE001 - progress is advisory
        _warn_throttled("genie_job_stage_write_failed", stage=str(stage), error_type=type(exc).__name__)


_PendingStage = tuple[LakebaseClient, GenieJobStage, int | None, int | None]


class _StageWriter:
    """Writes each running job's LATEST stage off the governed thread.

    ``submit`` (the runner's stage sink) only records the stage and wakes one
    daemon thread per process, so a slow Lakebase (the resilient client
    retries three times with backoff) never delays the governed completion:
    the deep sweep reports from inside its wait loop, and a stage write that
    outlasted its budget there would leave finished sub-analyses uncollected.
    A stage superseded before it was written is skipped (latest wins), and one
    job's writes stay in report order. A write that lands after the job ended
    matches no row (``status = 'running'``).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingStage] = {}
        self._writing = 0
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    def submit(
        self,
        lakebase: LakebaseClient,
        job_id: str,
        stage: GenieJobStage,
        parts_done: int | None = None,
        parts_planned: int | None = None,
    ) -> None:
        with self._lock:
            self._pending[job_id] = (lakebase, stage, parts_done, parts_planned)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="genie-job-stages", daemon=True)
                self._thread.start()
        self._wake.set()

    def idle(self) -> bool:
        """Nothing pending and nothing being written."""

        with self._lock:
            return not self._pending and not self._writing

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    return
                job_id = next(iter(self._pending))
                lakebase, stage, parts_done, parts_planned = self._pending.pop(job_id)
                self._writing += 1
            try:
                write_stage(lakebase, job_id, stage, parts_done, parts_planned)
            finally:
                with self._lock:
                    self._writing -= 1

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            self._drain()


STAGE_WRITER = _StageWriter()


def succeed(lakebase: LakebaseClient, job_id: str, result: dict[str, Any]) -> bool:
    """Store the de-authorized answer. False when this process lost the job."""

    row = lakebase.fetchone(
        _SUCCEED_SQL,
        {
            "job_id": job_id,
            "result_json": json.dumps(result, separators=(",", ":")),
            "lease_owner": PROCESS_ID,
        },
    )
    return row is not None


def fail(lakebase: LakebaseClient, job_id: str, kind: GenieJobFailureKind) -> bool:
    row = lakebase.fetchone(
        _FAIL_SQL,
        {"job_id": job_id, "failure_kind": GenieJobFailureKind(kind).value, "lease_owner": PROCESS_ID},
    )
    return row is not None


# ------------------------------------------------------------------ read


def _stale_reason(job: GenieCompletionJob) -> str | None:
    """Why this job must expire now, by Postgres' clock, or None."""

    if job.status in _LIVE and job.lease_until < job.db_now:
        return "stale_lease"
    if job.status is GenieJobStatus.SUCCEEDED and job.expires_at < job.db_now:
        return "past_expiry"
    return None


def read_for_actor(
    lakebase: LakebaseClient,
    *,
    job_id: str,
    actor: str,
    conversation_id: str,
    message_id: str,
) -> GenieCompletionJob | None:
    """The caller's own job, expired first when its lease or window lapsed."""

    if not JOB_ID_RE.fullmatch(job_id):
        return None
    params = {
        "job_id": job_id,
        "actor_email": actor,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
    try:
        row = lakebase.fetchone(_SELECT_JOB_SQL, params)
        if row is None:
            return None
        job = _job_from_row(row)
        reason = _stale_reason(job)
        if reason is None:
            return job
        expired = lakebase.fetchone(
            _EXPIRE_JOB_SQL,
            {"job_id": job_id, "prior_status": job.status.value, "prior_lease_until": job.lease_until},
        )
        if expired is None:
            # Changed under us (renewed, finished): report what is there now.
            row = lakebase.fetchone(_SELECT_JOB_SQL, params)
            return _job_from_row(row) if row is not None else None
    except (LakebaseError, DependencyDownError) as exc:
        raise _unavailable(exc) from exc
    emit(
        log,
        "genie_job_expired",
        dependency="lakebase",
        outcome="expired",
        reason=reason,
        via="read",
        job_id=job_id,
    )
    return _job_from_row(expired)


__all__ = [
    "CANCELS",
    "HEARTBEAT",
    "JOB_ID_RE",
    "PROCESS_ID",
    "STAGE_WRITER",
    "GenieCompletionJob",
    "JobEnrollment",
    "claim",
    "completion_jobs_available",
    "create_or_join",
    "fail",
    "job_turn_ids_eligible",
    "read_for_actor",
    "succeed",
    "sweep_expired",
    "write_stage",
]
