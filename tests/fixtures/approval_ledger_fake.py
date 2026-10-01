"""In-memory model of the approval-request and approvals SQL, for unit tests.

Test-only (never imported by backend). Each statement the approval-request
service sends is recognized by identity against the SQL constants and
answered from Python tables with the SAME semantics; the real statements are
proven against PostgreSQL by tests/integration/test_approval_requests_postgres.py.
A transaction snapshots the tables and restores them when the block raises,
so a rolled-back create leaves nothing behind, exactly like Lakebase.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from backend.services import approval_request_sql as sql
from backend.services import outreach_revoke
from backend.services.audit_lakebase_store import _INSERT_SQL as AUDIT_INSERT_SQL
from backend.services.lakebase import LakebaseError
from backend.services.outreach_decision_commit import (
    _APPROVAL_FINALIZE,
    _APPROVAL_INSERT_RETURNING,
    _APPROVAL_LOOKUP_BY_REQUEST_ID,
)
from backend.services.outreach_decision_ordering import (
    BORROWER_DECISION_LOCK,
    LATEST_BORROWER_DECISION,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_STATEMENTS: dict[str, str] = {
    value: name
    for name, value in vars(sql).items()
    if name.isupper() and isinstance(value, str) and value.lstrip().startswith(("SELECT", "UPDATE", "INSERT"))
}
_STATEMENTS[AUDIT_INSERT_SQL] = "INSERT_AUDIT"
_STATEMENTS.update(
    {
        BORROWER_DECISION_LOCK: "DECISION_LOCK",
        LATEST_BORROWER_DECISION: "LATEST_DECISION",
        _APPROVAL_LOOKUP_BY_REQUEST_ID: "APPROVAL_BY_REQUEST_ID",
        _APPROVAL_INSERT_RETURNING: "INSERT_APPROVAL",
        _APPROVAL_FINALIZE: "FINALIZE_APPROVAL",
        outreach_revoke.APPROVAL_ROW: "APPROVAL_ROW",
        outreach_revoke.OUTREACH_SINCE_APPROVAL: "OUTREACH_SINCE_APPROVAL",
        outreach_revoke.ACTIVE_ASSIGNMENT_FOR_UPDATE: "ACTIVE_ASSIGNMENT",
        outreach_revoke.RELEASE_ASSIGNMENT: "RELEASE_ASSIGNMENT",
    }
)


def _uuid(value: object) -> str:
    """A uuid column compared with a text parameter: the cast ignores case,
    and the column prints back lower-case (``uuid::text``)."""

    return str(value).lower()


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FakeApprovalLedger:
    """The tables the approval-request service touches, plus a statement log."""

    _supports_atomic_transactions = True

    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.approvals: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.statements: list[str] = []
        self.now = NOW
        self.down = False
        self.extra: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {}
        # Called with the candidate ids before INSERT_OPEN_ITEMS runs; a test
        # uses it to model a concurrent request that just took a borrower.
        self.before_insert_items: Callable[[list[str]], None] | None = None
        # The outreach ledgers a revoke consults.
        self.dispositions: list[dict[str, Any]] = []
        self.outcomes: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.assignments: dict[str, dict[str, Any]] = {}

    # -- seeding helpers ----------------------------------------------------
    def add_decision(
        self,
        borrower_id: str,
        action: str,
        *,
        decided_at: datetime | None = None,
        batch_id: str | None = None,
        finalized: bool = True,
        approval_id: str | None = None,
        **extra: Any,
    ) -> str:
        intent: dict[str, Any] = {"action": action, "borrower_id": borrower_id}
        if batch_id is not None:
            intent["approval_request_batch_id"] = batch_id
        row = {
            "approval_id": approval_id or str(uuid4()),
            "borrower_id": borrower_id,
            "action": action,
            "decided_at": decided_at or self.now,
            "audit_event_id": str(uuid4()) if finalized else None,
            "decision_intent": json.dumps(intent, sort_keys=True, separators=(",", ":")),
            "campaign_id": None,
            "channel": "email",
            "offer_code": "refi",
            "request_id": None,
            "actor_email": "pat.approver@summit.example",
            "decision_payload_hash": None,
            "decision_response": None,
            **extra,
        }
        self.approvals.append(row)
        return str(row["approval_id"])

    def add_batch(
        self,
        requested_by: str,
        borrower_ids: list[str],
        *,
        created_at: datetime | None = None,
        note: str = "Rate-sensitive refinance candidates.",
    ) -> str:
        batch_id = str(uuid4())
        self.batches[batch_id] = {
            "batch_id": batch_id,
            "requested_by": requested_by,
            "request_key": str(uuid4()),
            "request_intent_hash": "0" * 64,
            "note": note,
            "response": {"batch_id": batch_id},
            "audit_event_id": str(uuid4()),
            "created_at": created_at or self.now,
        }
        for borrower_id in borrower_ids:
            self.items[(batch_id, borrower_id)] = {"status": "open", "closed_at": None}
        return batch_id

    def audit_types(self) -> list[str]:
        return [str(row["event_type"]) for row in self.audits]

    def writes(self) -> int:
        return len(self.batches) + len(self.items) + len(self.audits)

    # -- LakebaseClient surface ---------------------------------------------
    def fetchone(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self._run(statement, params or {})
        return rows[0] if rows else None

    def fetchall(
        self, statement: str, params: dict[str, Any] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self._run(statement, params or {})[:limit]

    def execute(self, statement: str, params: dict[str, Any] | None = None) -> _Result:
        # Also the psycopg-connection surface inside transaction().
        return _Result(self._run(statement, params or {}))

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        tables = ("batches", "items", "approvals", "audits", "assignments")
        snapshot = copy.deepcopy([getattr(self, name) for name in tables])
        try:
            yield self
        except BaseException:
            for name, value in zip(tables, snapshot, strict=True):
                setattr(self, name, value)
            raise

    def _run(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.down:
            raise LakebaseError("lakebase down")
        name = _STATEMENTS.get(statement)
        if name is None:
            for key, handler in self.extra.items():
                if key in statement:
                    self.statements.append(key)
                    return handler(params)
            raise AssertionError(f"unexpected SQL: {statement[:80]}")
        self.statements.append(name)
        return getattr(self, f"_sql_{name.lower()}")(params)

    # -- statement models ------------------------------------------------------
    def _open_for(self, borrower_id: str) -> list[tuple[str, str]]:
        return [key for key, item in self.items.items() if key[1] == borrower_id and item["status"] == "open"]

    def _sql_batch_by_key(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {key: batch[key] for key in ("batch_id", "request_intent_hash", "response", "audit_event_id")}
            for batch in self.batches.values()
            if batch["requested_by"] == params["requested_by"] and batch["request_key"] == params["request_key"]
        ]

    def _latest(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        ordered = sorted(rows, key=lambda row: (row["decided_at"], str(row["approval_id"])), reverse=True)
        return ordered[0] if ordered else None

    def _sql_latest_finalized_decisions(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for borrower_id in params["borrower_ids"]:
            latest = self._latest(
                [row for row in self.approvals if row["borrower_id"] == borrower_id and row["audit_event_id"]]
            )
            if latest is not None:
                out.append({"borrower_id": borrower_id, "action": latest["action"]})
        return out

    def _sql_expire_stale_open_items(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        for (batch_id, borrower_id), item in self.items.items():
            batch = self.batches[batch_id]
            if (
                borrower_id in params["borrower_ids"]
                and item["status"] == "open"
                and batch["created_at"] < self.now - timedelta(days=30)
            ):
                item.update(status="expired", closed_at=self.now)
        return []

    def _sql_insert_batch(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self._sql_batch_by_key(params):
            return []
        self.batches[_uuid(params["batch_id"])] = {
            "batch_id": _uuid(params["batch_id"]),
            "requested_by": params["requested_by"],
            "request_key": params["request_key"],
            "request_intent_hash": params["request_intent_hash"],
            "note": params["note"],
            "response": None,
            "audit_event_id": None,
            "created_at": self.now,
        }
        return [{"batch_id": _uuid(params["batch_id"])}]

    def _sql_insert_open_items(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.before_insert_items is not None:
            self.before_insert_items(list(params["borrower_ids"]))
        out = []
        for borrower_id in params["borrower_ids"]:
            if self._open_for(borrower_id):
                continue
            self.items[(_uuid(params["batch_id"]), borrower_id)] = {"status": "open", "closed_at": None}
            out.append({"borrower_id": borrower_id})
        return out

    def _sql_insert_audit(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        audit_id = str(uuid4())
        self.audits.append({**params, "audit_id": audit_id, "metadata": json.loads(params["metadata"])})
        return [{"audit_id": audit_id, "audit_sequence": len(self.audits), "event_at": self.now}]

    def _sql_finalize_batch(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        batch = self.batches.get(_uuid(params["batch_id"]))
        if batch is None or batch["response"] is not None:
            return []
        batch.update(response=json.loads(params["response"]), audit_event_id=params["audit_event_id"])
        return [{"batch_id": batch["batch_id"]}]

    def _batch_view(self, batch: dict[str, Any]) -> dict[str, Any]:
        return {key: batch[key] for key in ("batch_id", "requested_by", "note", "created_at")}

    def _sql_batch_by_id(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        batch = self.batches.get(_uuid(params["batch_id"]))
        return [self._batch_view(batch)] if batch and batch["audit_event_id"] else []

    _sql_batch_by_id_for_update = _sql_batch_by_id

    def _sql_open_scope_batches(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [
            batch
            for batch in self.batches.values()
            if batch["audit_event_id"]
            and batch["created_at"] >= self.now - timedelta(days=30)
            and any(key[0] == batch["batch_id"] and item["status"] == "open" for key, item in self.items.items())
        ]
        rows.sort(key=lambda batch: (batch["created_at"], batch["batch_id"]))
        return [self._batch_view(batch) for batch in rows[:200]]

    def _sql_mine_scope_batches(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [
            batch
            for batch in self.batches.values()
            if batch["audit_event_id"]
            and batch["requested_by"] == params["requested_by"]
            and batch["created_at"] >= self.now - timedelta(days=30)
        ]
        rows.sort(key=lambda batch: (batch["created_at"], batch["batch_id"]), reverse=True)
        return [self._batch_view(batch) for batch in rows[:50]]

    def _sql_items_with_decisions(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        wanted = {_uuid(batch_id) for batch_id in params["batch_ids"]}
        for (batch_id, borrower_id), item in sorted(self.items.items()):
            if batch_id not in wanted:
                continue
            if params.get("borrower_id") is not None and borrower_id != params["borrower_id"]:
                continue
            created_at = self.batches[batch_id]["created_at"]
            finalized = [
                row
                for row in self.approvals
                if row["borrower_id"] == borrower_id
                and row["audit_event_id"]
                and row["action"] in ("approve", "reject")
            ]
            marker = f'"approval_request_batch_id":"{batch_id}"'
            linked = self._latest(
                [row for row in finalized if row["decided_at"] >= created_at and marker in (row["decision_intent"] or "")]
            )
            outside = self._latest([row for row in finalized if row["decided_at"] > created_at])
            out.append(
                {
                    "batch_id": batch_id,
                    "borrower_id": borrower_id,
                    "status": item["status"],
                    "batch_created_at": created_at,
                    "linked_action": linked["action"] if linked else None,
                    "linked_approval_id": str(linked["approval_id"]) if linked else None,
                    "outside_action": outside["action"] if outside else None,
                    "read_at": self.now,
                }
            )
        out.sort(key=lambda row: (row["batch_created_at"], row["batch_id"], row["borrower_id"]))
        return out

    def _sql_withdraw_open_items(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for borrower_id in params["borrower_ids"]:
            item = self.items.get((_uuid(params["batch_id"]), borrower_id))
            if item is not None and item["status"] == "open":
                item.update(status="withdrawn", closed_at=self.now)
                out.append({"borrower_id": borrower_id})
        return out

    def _sql_expire_open_items_for_borrower(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for key in self._open_for(params["borrower_id"]):
            self.items[key].update(status="expired", closed_at=self.now)
            out.append({"batch_id": key[0]})
        return out

    # -- the approvals and outreach statements a revoke sends --------------------
    def add_assignment(self, borrower_id: str, status: str = "assigned") -> str:
        assignment_id = str(uuid4())
        self.assignments[assignment_id] = {
            "assignment_id": assignment_id, "borrower_id": borrower_id, "status": status, "released_at": None,
        }
        return assignment_id

    def _sql_decision_lock(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def _sql_latest_decision(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        latest = self._latest([row for row in self.approvals if row["borrower_id"] == params["borrower_id"]])
        return [{"approval_id": str(latest["approval_id"]), "action": latest["action"]}] if latest else []

    def _sql_approval_by_request_id(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in self.approvals if row.get("request_id") == params["request_id"]][:1]

    def _sql_approval_row(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.approvals
            if _uuid(row["approval_id"]) == _uuid(params["approval_id"])
            and row["borrower_id"] == params["borrower_id"]
        ]

    def _sql_outreach_since_approval(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        def since(rows: list[dict[str, Any]]) -> int:
            return sum(
                1
                for row in rows
                if row["borrower_id"] == params["borrower_id"] and row["occurred_at"] >= params["decided_at"]
            )

        deliveries = sum(
            1
            for row in self.outbox
            if _uuid(row["approval_id"]) == _uuid(params["approval_id"]) and row["status"] == "delivered"
        )
        return [{"dispositions": since(self.dispositions), "outcomes": since(self.outcomes), "deliveries": deliveries}]

    def _sql_active_assignment(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"assignment_id": row["assignment_id"], "status": row["status"]}
            for row in self.assignments.values()
            if row["borrower_id"] == params["borrower_id"] and row["released_at"] is None
        ]

    def _sql_release_assignment(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        row = self.assignments.get(params["assignment_id"])
        if row is None or row["released_at"] is not None:
            return []
        row["released_at"] = self.now
        return [{"assignment_id": row["assignment_id"]}]

    def _sql_insert_approval(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if any(row.get("request_id") == params["request_id"] for row in self.approvals):
            return []
        self.approvals.append(
            {
                **params,
                # clock_timestamp(): strictly after every earlier decision.
                "decided_at": self.now + timedelta(microseconds=len(self.approvals) + 1),
                "audit_event_id": None,
                "decision_response": None,
            }
        )
        return [{"approval_id": params["approval_id"]}]

    def _sql_finalize_approval(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        for row in self.approvals:
            if _uuid(row["approval_id"]) == _uuid(params["approval_id"]):
                if row["decision_response"] is not None or row["audit_event_id"] is not None:
                    return []
                row["decision_response"] = json.loads(params["decision_response"])
                row["audit_event_id"] = params["audit_event_id"]
                return [{"approval_id": row["approval_id"]}]
        return []
