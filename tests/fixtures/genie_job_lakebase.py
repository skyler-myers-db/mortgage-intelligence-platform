"""In-memory Lakebase double that knows ``mip_app.genie_completion_jobs``.

Test-only (never imported by backend code). It answers the job store's SQL
statements by IDENTITY (``backend/services/genie_completion_jobs.py``
constants) and models their semantics in Python under a lock, with its own
clock standing in for Postgres ``now()``. The SQL text itself is exercised
against a real PostgreSQL in
``tests/integration/test_genie_completion_jobs_postgres.py``.

Everything else (the session/message record writes, the replay-ownership
lookup) keeps the minimal two-call surface the Genie router tests use:
``executed`` lists every non-job write, ``message_recorded`` makes the
ownership lookup report the turn as already recorded.

The cancel route's ONE transaction (audit ``genie-03``) is modelled too:
``transaction()`` snapshots the rows and the audit rows and restores both
when the block raises, so a failed audit insert rolls its flag back.
``audit_rows`` holds every ``action_audit`` insert made in a transaction;
``fail_audit_inserts`` makes the next N of them raise.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from backend.services import genie_completion_cancel as cancel
from backend.services import genie_completion_jobs as jobs
from backend.services import genie_completion_record as record
from backend.services.lakebase import LakebaseError

_LIVE = ("queued", "running")


class FakeJobLakebase:
    _supports_atomic_transactions = False

    def __init__(self, *, jobs_table: bool = True, cancel_columns: bool = True, now: datetime | None = None) -> None:
        self.jobs_table = jobs_table
        #: False: the table exists without its 2026_09_25 columns.
        self.cancel_columns = cancel_columns
        # Starts at the wall clock: job expiry is minted from the progress
        # token's real ``exp``. Tests move it forward with ``advance``.
        self.now = now or datetime.now(UTC)
        self.rows: dict[str, dict[str, Any]] = {}
        self.executed: list[str] = []
        self.message_recorded = False
        self.fail_stage_writes = False
        self.stage_writes: list[tuple[str, int | None, int | None]] = []
        self.job_statements: list[str] = []
        self.audit_rows: list[dict[str, Any]] = []
        self.fail_audit_inserts = 0
        #: Called with the statement name before each job statement runs.
        self.before: Callable[[str], None] | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ helpers

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now = self.now + timedelta(seconds=seconds)

    def only_job(self) -> dict[str, Any]:
        with self._lock:
            assert len(self.rows) == 1, list(self.rows.values())
            return dict(next(iter(self.rows.values())))

    def insert_row(self, **overrides: Any) -> dict[str, Any]:
        """Seed a job row directly (another actor's, a dead process's...)."""

        with self._lock:
            row = self._new_row(
                {
                    "actor_email": "someone@example.com",
                    "conversation_id": f"conv-{uuid4().hex[:8]}",
                    "message_id": f"msg-{uuid4().hex[:8]}",
                    "question_hash": "a" * 64,
                    "lease_owner": "other-process",
                    "expires_at_epoch": (self.now + timedelta(minutes=15)).timestamp(),
                }
            )
            row.update(overrides)
            self.rows[row["job_id"]] = row
            return dict(row)

    def _new_row(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": str(uuid4()),
            "actor_email": params["actor_email"],
            "conversation_id": params["conversation_id"],
            "message_id": params["message_id"],
            "question_hash": params["question_hash"],
            "status": "queued",
            "stage": "queued",
            "parts_done": None,
            "parts_planned": None,
            "failure_kind": None,
            "result_json": None,
            "lease_owner": params["lease_owner"],
            "lease_until": self.now + timedelta(seconds=jobs.LEASE_S),
            "expires_at": datetime.fromtimestamp(float(params["expires_at_epoch"]), UTC),
            "finished_at": None,
            "cancel_requested_at": None,
            "recorded_at": None,
            "deep": params.get("deep"),
            "created_at": self.now,
        }

    _HIDDEN = frozenset(
        {"actor_email", "conversation_id", "message_id", "finished_at", "cancel_requested_at", "recorded_at", "created_at"}
    )

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        view = {key: row[key] for key in row if key not in self._HIDDEN}
        view["cancel_requested"] = row["cancel_requested_at"] is not None
        view["recorded"] = row["recorded_at"] is not None
        view["db_now"] = self.now
        return view

    def _check(self, row: dict[str, Any]) -> None:
        """The table's cancel CHECKs, so a code bug cannot pass silently."""

        if row["cancel_requested_at"] is not None and row["recorded_at"] is not None:
            raise LakebaseError("genie_completion_jobs_cancel_or_record_chk (fake)")
        if row["status"] == "cancelled" and (row["cancel_requested_at"] is None or row["result_json"] is not None):
            raise LakebaseError("genie_completion_jobs_cancelled_shape_chk (fake)")

    def _by_turn(self, params: dict[str, Any]) -> dict[str, Any] | None:
        for row in self.rows.values():
            if (
                row["actor_email"] == params["actor_email"]
                and row["conversation_id"] == params["conversation_id"]
                and row["message_id"] == params["message_id"]
            ):
                return row
        return None

    def _stale(self, row: dict[str, Any]) -> bool:
        return (row["status"] in _LIVE and row["lease_until"] < self.now) or (
            row["status"] == "succeeded" and row["expires_at"] < self.now
        )

    def _expire(self, row: dict[str, Any]) -> None:
        row.update(status="expired", stage="expired", result_json=None)
        row["finished_at"] = row["finished_at"] or self.now

    # -------------------------------------------------------- the surface

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        params = params or {}
        if sql in _JOB_SQL and self.before is not None:
            self.before(_JOB_SQL[sql])
        with self._lock:
            if sql is jobs._PROBE_SQL:
                return {"present": self.jobs_table and self.cancel_columns}
            if sql in _JOB_SQL:
                self._require_table()
                self.job_statements.append(_JOB_SQL[sql])
                return self._job_fetchone(sql, params)
            if self.message_recorded and "genie_messages" in sql:
                return {
                    "conversation_id": params.get("conversation_id"),
                    "message_id": params.get("message_id"),
                }
            return None

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params = params or {}
        with self._lock:
            if sql is jobs._SWEEP_SQL:
                self._require_table()
                self.job_statements.append("sweep")
                swept: list[dict[str, Any]] = []
                for row in self.rows.values():
                    if len(swept) >= min(limit, int(params["limit"])):
                        break
                    if self._stale(row):
                        swept.append({"job_id": row["job_id"], "prior_status": row["status"]})
                        self._expire(row)
                return swept
            if sql is jobs._HEARTBEAT_SQL:
                self.job_statements.append("heartbeat")
                renewed: list[dict[str, Any]] = []
                for job_id in params["job_ids"]:
                    row = self.rows.get(job_id)
                    if row and row["lease_owner"] == params["lease_owner"] and row["status"] in _LIVE:
                        row["lease_until"] = self.now + timedelta(seconds=jobs.LEASE_S)
                        renewed.append({"job_id": job_id, "cancel_requested": row["cancel_requested_at"] is not None})
                return renewed[:limit]
            return []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        params = params or {}
        with self._lock:
            if sql is jobs._STAGE_SQL:
                self.job_statements.append("stage")
                if self.fail_stage_writes:
                    raise LakebaseError("stage write refused (fake)")
                row = self.rows.get(params["job_id"])
                if row and row["status"] == "running" and row["lease_owner"] == params["lease_owner"]:
                    row.update(
                        stage=params["stage"],
                        parts_done=params["parts_done"],
                        parts_planned=params["parts_planned"],
                    )
                    self.stage_writes.append((params["stage"], params["parts_done"], params["parts_planned"]))
                return
            self.executed.append(sql)

    # ------------------------------------------------ the cancel transaction

    @contextmanager
    def transaction(self) -> Iterator[_FakeConn]:
        with self._lock:
            snapshot = (copy.deepcopy(self.rows), list(self.audit_rows))
            try:
                yield _FakeConn(self)
            except BaseException:
                self.rows, self.audit_rows = snapshot
                raise

    def _transaction_execute(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if sql is cancel._LOCK_SQL:
            self.job_statements.append("cancel_lock")
            row = self.rows.get(str(params["job_id"]))
            if row is None or self._by_turn(params) is not row:
                return None
            return {
                "status": row["status"],
                "stage": row["stage"],
                "question_hash": row["question_hash"],
                "lease_owner": row["lease_owner"],
                "cancel_requested": row["cancel_requested_at"] is not None,
                "recorded": row["recorded_at"] is not None,
            }
        if sql is cancel._ACCEPT_SQL:
            self.job_statements.append("cancel_accept")
            row = self.rows.get(str(params["job_id"]))
            if (
                row is None
                or row["status"] not in _LIVE
                or row["recorded_at"] is not None
                or row["cancel_requested_at"] is not None
            ):
                return None
            row["cancel_requested_at"] = self.now
            if row["status"] == "queued":
                row.update(status="cancelled", stage="cancelled", finished_at=self.now)
            self._check(row)
            return {"status": row["status"], "lease_owner": row["lease_owner"]}
        if "INSERT INTO mip_app.action_audit" in sql:
            if self.fail_audit_inserts:
                self.fail_audit_inserts -= 1
                raise LakebaseError("audit insert refused (fake)")
            audit_row = {"audit_id": uuid4(), "audit_sequence": len(self.audit_rows) + 1, "event_at": self.now, **params}
            self.audit_rows.append(audit_row)
            return audit_row
        raise AssertionError(f"unexpected transaction SQL: {sql[:80]}")

    def _require_table(self) -> None:
        if not self.jobs_table:
            raise LakebaseError('relation "mip_app.genie_completion_jobs" does not exist')

    def _job_fetchone(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if sql is jobs._INSERT_SQL:
            if self._by_turn(params) is not None:
                if "ON CONFLICT (actor_email, conversation_id, message_id) DO NOTHING" not in sql:
                    raise LakebaseError("duplicate key value violates uq_genie_completion_jobs_turn")
                return None
            row = self._new_row(params)
            self.rows[row["job_id"]] = row
            return self._view(row)
        if sql is jobs._SELECT_TURN_SQL:
            row = self._by_turn(params)
            return self._view(row) if row else None
        row = self.rows.get(str(params.get("job_id")))
        if sql is jobs._SELECT_JOB_SQL:
            if row is None or self._by_turn(params) is not row:
                return None
            return self._view(row)
        if row is None:
            return None
        if sql is jobs._EXPIRE_JOB_SQL:
            if row["status"] != params["prior_status"] or row["lease_until"] != params["prior_lease_until"]:
                return None
            self._expire(row)
            return self._view(row)
        owned = row["lease_owner"] == params.get("lease_owner")
        if sql is record._CANCEL_STATE_SQL:
            return {"cancel_requested": row["cancel_requested_at"] is not None}
        if sql is record._COMMIT_SQL:
            if (
                row["status"] != "running"
                or not owned
                or row["cancel_requested_at"] is not None
                or row["recorded_at"] is not None
            ):
                return None
            row["recorded_at"] = self.now
            self._check(row)
            return {"job_id": row["job_id"]}
        if sql is record._END_CANCELLED_SQL:
            if (
                row["status"] not in _LIVE
                or not owned
                or row["cancel_requested_at"] is None
                or row["recorded_at"] is not None
            ):
                return None
            row.update(
                status="cancelled", stage="cancelled", parts_done=None, parts_planned=None, result_json=None, finished_at=self.now
            )
            self._check(row)
            return {"job_id": row["job_id"]}
        if sql is jobs._CLAIM_SQL:
            if row["status"] != "queued" or not owned or row["cancel_requested_at"] is not None:
                return None
            row.update(status="running", lease_until=self.now + timedelta(seconds=jobs.LEASE_S))
            return {"job_id": row["job_id"]}
        if sql is jobs._SUCCEED_SQL:
            if row["status"] != "running" or not owned:
                return None
            row.update(
                status="succeeded",
                stage="done",
                result_json=json.loads(params["result_json"]),
                parts_done=None,
                parts_planned=None,
                finished_at=self.now,
            )
            return {"job_id": row["job_id"]}
        if sql is jobs._FAIL_SQL:
            if row["status"] not in _LIVE or not owned:
                return None
            row.update(
                status="failed",
                stage="failed",
                failure_kind=params["failure_kind"],
                parts_done=None,
                parts_planned=None,
                finished_at=self.now,
            )
            return {"job_id": row["job_id"]}
        raise AssertionError("unmodelled job statement")


class _FakeResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _FakeConn:
    def __init__(self, lakebase: FakeJobLakebase) -> None:
        self.lakebase = lakebase

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> _FakeResult:
        return _FakeResult(self.lakebase._transaction_execute(sql, params or {}))


_JOB_SQL: dict[str, str] = {
    jobs._INSERT_SQL: "insert",
    jobs._SELECT_TURN_SQL: "select_turn",
    jobs._SELECT_JOB_SQL: "select_job",
    jobs._EXPIRE_JOB_SQL: "expire",
    jobs._CLAIM_SQL: "claim",
    jobs._SUCCEED_SQL: "succeed",
    jobs._FAIL_SQL: "fail",
    record._COMMIT_SQL: "commit",
    record._CANCEL_STATE_SQL: "cancel_state",
    record._END_CANCELLED_SQL: "end_cancelled",
}
