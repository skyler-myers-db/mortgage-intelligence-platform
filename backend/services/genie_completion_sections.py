"""Verified sections of a running deep job: the reveal writer.

Audit 2026-09-21 ``genie-01`` phase 1b. The deep sweep judges each planned
sub-analysis as it is collected (its own output-policy scan, after its own
sub-turn already passed claims verification) and reports the plan-ordered
snapshot of every section judged ``ship`` so far through
``report_sections``. This module puts that snapshot on the job row's
``sections_json`` so ``/message/status`` can show it as "Partial research".

Ruling R1 (integrator, delegated owner, 2026-10-01): no verified section
reaches the client before the server has written its
``GENIE_SECTION_REVEALED`` audit row (job and turn ids, the section's plan
index, row count, SQL hash and verification verdict; never question text
or a row value). The audit rows and the ``sections_json`` UPDATE share ONE
Lakebase transaction, so a section is stored for the poll only when its row
committed, and an UPDATE that matches no running job rolls the rows back.
When the audit write fails the section is WITHHELD until the final answer
(fail closed); sections already audited may still be shown.

Below ``REVEAL_SECTION_FLOOR`` sections only the count is stored: nothing is
revealed and nothing is audited. The final RUN_GENIE row, action tokens and
the session row stay at the single ``recorded_at`` commit point; every
terminal statement NULLs ``sections_json``.

Like ``jobs.STAGE_WRITER``: one daemon thread per process, the latest
snapshot wins per job, never on the governed thread, and a failed write
never fails the answer (a throttled WARNING).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from backend.services import genie_completion_jobs as jobs
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.genie_audit import genie_audit_entity_id_from_parts
from backend.services.lakebase import LakebaseClient
from backend.services.observability import (
    emit,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

log = logging.getLogger("mip-genie-jobs")

#: Sections are revealed from this many verified ones: the sweep's own
#: shipping floor (``databricks_genie_sweep._MIN_PLANNED``), so a reveal never
#: shows fewer sections than a shipped answer could hold.
REVEAL_SECTION_FLOOR = 3
#: Rows kept per revealed section; ``row_count`` keeps the true count.
REVEAL_MAX_ROWS = 50
#: Above this the write is skipped (the column CHECK allows 8 MiB).
SECTIONS_JSON_MAX_BYTES = 4_194_304
_PAYLOAD_VERSION = 1

_WRITE_SECTIONS_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET sections_json = %(sections_json)s::jsonb, updated_at = now()
 WHERE job_id = %(job_id)s::uuid AND status = 'running' AND lease_owner = %(lease_owner)s
   AND recorded_at IS NULL AND cancel_requested_at IS NULL
RETURNING job_id::text AS job_id
"""


@dataclass(frozen=True)
class RevealContext:
    """The turn a reveal belongs to; captured on the governed thread."""

    actor: str
    conversation_id: str
    message_id: str
    #: The submit's 16-hex question label; never the question.
    question_label: str
    correlation_id: str


@dataclass
class _JobReveal:
    rev: int = 0
    audited: set[int] = field(default_factory=set)
    withheld: set[int] = field(default_factory=set)
    skipped_reported: bool = False


class _NotRevealable(Exception):
    """The job is no longer running for this process (ended, stopped,
    recorded): its audit rows roll back with the UPDATE."""


def _trimmed(item: dict[str, Any]) -> dict[str, Any]:
    rows = item.get("table_rows")
    if isinstance(rows, list) and len(rows) > REVEAL_MAX_ROWS:
        return {**item, "table_rows": rows[:REVEAL_MAX_ROWS]}
    return item


def _sql_hash(sql: Any) -> str | None:
    if not isinstance(sql, str) or not sql.strip():
        return None
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()[:16]


def _audit_revealed(conn: Any, context: RevealContext, job_id: str, item: dict[str, Any]) -> None:
    write_audit_event_in_transaction(
        conn,
        actor=context.actor,
        action="genie.section_revealed",
        entity_type="genie_message",
        entity_id=genie_audit_entity_id_from_parts(
            conversation_id=context.conversation_id,
            message_id=context.message_id,
            question_hash=context.question_label,
        ),
        payload_json={
            "conversation_id": context.conversation_id,
            "message_id": context.message_id,
            "question_hash": context.question_label,
            "genie_job_id": job_id,
            "section_index": int(item["index"]),
            "row_count": int(item.get("row_count") or 0),
            "sql_hash": _sql_hash(item.get("sql_query")),
            "verification_verdict": (
                "verified_rows_digest" if item.get("narrative_withheld") is True else "verified"
            ),
        },
        event_type="GENIE_SECTION_REVEALED",
    )


def _payload(rev: int, count: int, sections: list[dict[str, Any]]) -> str:
    body = {"v": _PAYLOAD_VERSION, "rev": rev, "count": count, "sections": sections}
    return json.dumps(body, separators=(",", ":"), default=str)


def _update_params(job_id: str, payload: str) -> dict[str, Any]:
    return {"job_id": job_id, "sections_json": payload, "lease_owner": jobs.PROCESS_ID}


def _write_unaudited(lakebase: LakebaseClient, job_id: str, payload: str) -> None:
    """A payload that reveals nothing new: the count, or audited sections."""

    lakebase.fetchone(_WRITE_SECTIONS_SQL, _update_params(job_id, payload))


def write_sections(
    lakebase: LakebaseClient,
    job_id: str,
    context: RevealContext,
    snapshot: list[dict[str, Any]],
    state: _JobReveal,
) -> None:
    """Store one snapshot; audit every newly revealed section first (R1)."""

    count = len(snapshot)
    if count == 0:
        # A new sweep in the same turn: its plan indices are new sections.
        state.audited.clear()
        state.withheld.clear()
    state.rev += 1
    if count < REVEAL_SECTION_FLOOR:
        _write_unaudited(lakebase, job_id, _payload(state.rev, count, []))
        return
    items = [_trimmed(item) for item in snapshot]
    new = [item for item in items if item["index"] not in state.audited | state.withheld]
    new_indices = {int(item["index"]) for item in new}
    shown = [item for item in items if item["index"] in state.audited or item["index"] in new_indices]
    payload = _payload(state.rev, count, shown)
    if len(payload.encode("utf-8")) > SECTIONS_JSON_MAX_BYTES:
        if not state.skipped_reported:
            state.skipped_reported = True
            emit(log, "genie_job_sections_skipped", level=logging.WARNING, dependency="lakebase",
                 outcome="skipped", reason="too_large", job_id=job_id, sections=count)
        return
    try:
        with lakebase.transaction() as conn:
            for item in new:
                _audit_revealed(conn, context, job_id, item)
            if conn.execute(_WRITE_SECTIONS_SQL, _update_params(job_id, payload)).fetchone() is None:
                raise _NotRevealable()
    except _NotRevealable:
        return
    except Exception as exc:  # noqa: BLE001 - fail closed: the new sections stay withheld
        state.withheld |= new_indices
        jobs._warn_throttled("genie_job_section_write_failed", error_type=type(exc).__name__)
        audited = [item for item in items if item["index"] in state.audited]
        try:
            _write_unaudited(lakebase, job_id, _payload(state.rev, count, audited))
        except Exception:  # noqa: BLE001 - the count is advisory
            return
        return
    state.audited |= new_indices


#: (client, context, latest snapshot, a sweep reset is pending). The reset
#: survives coalescing: an empty snapshot overwritten by the next sweep's first
#: one before it drained must still start that sweep's audit set from nothing.
_Pending = tuple[LakebaseClient, RevealContext, list[dict[str, Any]], bool]


class _SectionWriter:
    """Writes each running job's LATEST snapshot off the governed thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._states: dict[str, _JobReveal] = {}
        self._writing = 0
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    def submit(
        self,
        lakebase: LakebaseClient,
        job_id: str,
        context: RevealContext,
        snapshot: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            queued = self._pending.get(job_id)
            reset = not snapshot or (queued is not None and queued[3])
            self._pending[job_id] = (lakebase, context, list(snapshot), reset)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="genie-job-sections", daemon=True)
                self._thread.start()
        self._wake.set()

    def forget(self, job_id: str) -> None:
        """The job's runner ended: drop its pending snapshot and its rev."""

        with self._lock:
            self._pending.pop(job_id, None)
            self._states.pop(job_id, None)

    def idle(self) -> bool:
        with self._lock:
            return not self._pending and not self._writing

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    return
                job_id = next(iter(self._pending))
                lakebase, context, snapshot, reset = self._pending.pop(job_id)
                state = self._states.setdefault(job_id, _JobReveal())
                if reset:
                    # Only this thread mutates a job's state (R1: a new
                    # sweep's plan indices are new sections to audit).
                    state.audited.clear()
                    state.withheld.clear()
                self._writing += 1
            token = set_correlation_id(context.correlation_id)
            try:
                write_sections(lakebase, job_id, context, snapshot, state)
            except Exception as exc:  # noqa: BLE001 - a failed write never fails the answer
                jobs._warn_throttled("genie_job_section_write_failed", error_type=type(exc).__name__)
            finally:
                reset_correlation_id(token)
                with self._lock:
                    self._writing -= 1

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            self._drain()


SECTION_WRITER = _SectionWriter()


def reveal_context(
    *, actor: str, conversation_id: str, message_id: str, question_label: str
) -> RevealContext:
    return RevealContext(
        actor=actor,
        conversation_id=conversation_id,
        message_id=message_id,
        question_label=question_label,
        correlation_id=get_correlation_id(),
    )


__all__ = [
    "REVEAL_MAX_ROWS",
    "REVEAL_SECTION_FLOOR",
    "SECTIONS_JSON_MAX_BYTES",
    "SECTION_WRITER",
    "RevealContext",
    "reveal_context",
    "write_sections",
]
