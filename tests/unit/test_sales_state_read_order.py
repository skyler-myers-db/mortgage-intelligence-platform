from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from backend.services.sales_state import (
    SalesStateStore,
    clear_sales_state_cache,
)

_LOW_ID = "00000000-0000-0000-0000-000000000001"
_HIGH_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def _approval(
    *, borrower_id: str, approval_id: str, action: str, decided_at: datetime
) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "borrower_id": borrower_id,
        "action": action,
        "offer_code": "refi_plus_heloc",
        "decided_at": decided_at,
    }


def _disposition(
    *,
    borrower_id: str,
    disposition_id: str,
    outcome: str,
    occurred_at: datetime,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "disposition_id": disposition_id,
        "borrower_id": borrower_id,
        "lo_email": "lo01@summit.example",
        "outcome": outcome,
        "attempt_number": 1,
        "occurred_at": occurred_at,
        "created_at": created_at,
        "callback_at": None,
        "notes": None,
        "audit_event_id": None,
    }


class _TotalOrderClient:
    """Model physical-last tie behavior unless the SQL supplies the UUID order."""

    def __init__(
        self,
        *,
        approvals: list[dict[str, Any]] | None = None,
        dispositions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.approvals = approvals or []
        self.dispositions = dispositions or []
        self.fetchone_sql: list[str] = []
        self.fetchall_sql: list[str] = []

    @staticmethod
    def _latest(
        rows: list[dict[str, Any]],
        *,
        sql: str,
        timestamp_fields: tuple[str, ...],
        id_field: str,
    ) -> dict[str, Any] | None:
        if not rows:
            return None
        has_id_tiebreaker = f"{id_field}::text DESC" in _compact(sql)

        def _key(item: tuple[int, dict[str, Any]]) -> tuple[Any, ...]:
            insertion_index, row = item
            final_key: str | int = str(row[id_field]) if has_id_tiebreaker else insertion_index
            return tuple(row[field] for field in timestamp_fields) + (final_key,)

        _, latest = max(enumerate(rows), key=_key)
        return latest

    def _latest_approval(self, sql: str, borrower_id: str) -> dict[str, Any] | None:
        rows = [row for row in self.approvals if row["borrower_id"] == borrower_id]
        return self._latest(
            rows,
            sql=sql,
            timestamp_fields=("decided_at",),
            id_field="approval_id",
        )

    def _latest_disposition(self, sql: str, borrower_id: str) -> dict[str, Any] | None:
        rows = [row for row in self.dispositions if row["borrower_id"] == borrower_id]
        return self._latest(
            rows,
            sql=sql,
            timestamp_fields=("occurred_at", "created_at"),
            id_field="disposition_id",
        )

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self.fetchone_sql.append(sql)
        borrower_id = str((params or {}).get("borrower_id") or "")
        if "WITH latest_approval AS" in sql:
            approval = self._latest_approval(sql, borrower_id)
            disposition = self._latest_disposition(sql, borrower_id)
            action = approval.get("action") if approval else None
            return {
                "approval_status": {
                    "approve": "approved",
                    "reject": "rejected",
                    "hold": "hold",
                }.get(action, "pending"),
                "outreach_status": (
                    "actioned"
                    if disposition is not None
                    else "queued"
                    if action == "approve"
                    else "none"
                ),
                "approval_id": (
                    approval.get("approval_id") if approval and action == "approve" else None
                ),
                "approved_at": (
                    approval.get("decided_at") if approval and action == "approve" else None
                ),
                "outreach_at": disposition.get("occurred_at") if disposition else None,
                "synced_at": datetime.now(UTC),
            }
        if "FROM mip_app.lead_assignments a" in sql:
            return None
        if "FROM mip_app.call_dispositions" in sql:
            row = self._latest_disposition(sql, borrower_id)
            return dict(row) if row else None
        raise AssertionError(f"unexpected fetchone query: {_compact(sql)}")

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.fetchall_sql.append(sql)
        if "WITH latest_approval AS" in sql and "age_days" in sql:
            now = datetime.now(UTC)
            out: list[dict[str, Any]] = []
            for borrower_id in sorted({str(row["borrower_id"]) for row in self.approvals}):
                approval = self._latest_approval(sql, borrower_id)
                disposition = self._latest_disposition(sql, borrower_id)
                if approval is None or approval["action"] != "approve" or disposition is not None:
                    continue
                decided_at = approval["decided_at"]
                if (now - decided_at).days < int((params or {})["older_than_days"]):
                    continue
                out.append(
                    {
                        "borrower_id": borrower_id,
                        "approval_status": "approved",
                        "approved_at": decided_at,
                        "age_days": (now - decided_at).days,
                        "outreach_status": "queued",
                        "outreach_at": None,
                        "assigned_to_email": None,
                        "latest_disposition_outcome": None,
                        "latest_disposition_at": None,
                    }
                )
            return sorted(out, key=lambda row: row["approved_at"])[:limit]
        if "FROM mip_app.call_dispositions" in sql and "DISTINCT ON" in sql:
            borrower_ids = sorted(set((params or {}).get("borrower_ids") or []))
            return [
                dict(row)
                for borrower_id in borrower_ids
                if (row := self._latest_disposition(sql, borrower_id)) is not None
            ][:limit]
        raise AssertionError(f"unexpected fetchall query: {_compact(sql)}")


def test_single_and_batched_dispositions_use_total_order_on_timestamp_ties() -> None:
    clear_sales_state_cache()
    tied_at = datetime(2026, 7, 1, 14, 30, tzinfo=UTC)
    dispositions = [
        _disposition(
            borrower_id="B-TIE-SINGLE",
            disposition_id=_HIGH_ID,
            outcome="connected",
            occurred_at=tied_at,
            created_at=tied_at,
        ),
        _disposition(
            borrower_id="B-TIE-SINGLE",
            disposition_id=_LOW_ID,
            outcome="dead",
            occurred_at=tied_at,
            created_at=tied_at,
        ),
        _disposition(
            borrower_id="B-TIE-BATCH",
            disposition_id=_HIGH_ID,
            outcome="application_started",
            occurred_at=tied_at,
            created_at=tied_at,
        ),
        _disposition(
            borrower_id="B-TIE-BATCH",
            disposition_id=_LOW_ID,
            outcome="called_no_answer",
            occurred_at=tied_at,
            created_at=tied_at,
        ),
    ]
    client = _TotalOrderClient(dispositions=dispositions)
    store = SalesStateStore(client)  # type: ignore[arg-type]

    single = store.latest_disposition_for("B-TIE-SINGLE")
    batched = store.latest_dispositions_for(["B-TIE-BATCH", "B-TIE-SINGLE"])

    assert single is not None
    assert single.disposition_id == _HIGH_ID
    assert single.outcome == "connected"
    assert batched["B-TIE-SINGLE"].disposition_id == _HIGH_ID
    assert batched["B-TIE-BATCH"].outcome == "application_started"
    single_sql = next(sql for sql in client.fetchone_sql if "call_dispositions" in sql)
    batch_sql = next(sql for sql in client.fetchall_sql if "call_dispositions" in sql)
    assert (
        "ORDER BY occurred_at DESC, created_at DESC, disposition_id::text DESC LIMIT 1"
        in _compact(single_sql)
    )
    assert (
        "ORDER BY borrower_id, occurred_at DESC, created_at DESC, "
        "disposition_id::text DESC" in _compact(batch_sql)
    )


def test_lifecycle_uses_gold_total_order_on_tied_reversed_rows() -> None:
    clear_sales_state_cache()
    tied_at = datetime(2026, 7, 1, 14, 30, tzinfo=UTC)
    client = _TotalOrderClient(
        approvals=[
            _approval(
                borrower_id="B-TIE-LIFECYCLE",
                approval_id=_HIGH_ID,
                action="reject",
                decided_at=tied_at,
            ),
            _approval(
                borrower_id="B-TIE-LIFECYCLE",
                approval_id=_LOW_ID,
                action="approve",
                decided_at=tied_at,
            ),
        ],
        dispositions=[
            _disposition(
                borrower_id="B-TIE-LIFECYCLE",
                disposition_id=_HIGH_ID,
                outcome="connected",
                occurred_at=tied_at,
                created_at=tied_at,
            ),
            _disposition(
                borrower_id="B-TIE-LIFECYCLE",
                disposition_id=_LOW_ID,
                outcome="dead",
                occurred_at=tied_at,
                created_at=tied_at,
            ),
        ],
    )
    store = SalesStateStore(client)  # type: ignore[arg-type]

    lifecycle = store.lifecycle_for("B-TIE-LIFECYCLE")

    assert lifecycle["approval_status"] == "rejected"
    assert lifecycle["approval_id"] is None
    assert lifecycle["latest_disposition"].disposition_id == _HIGH_ID
    assert lifecycle["latest_disposition"].outcome == "connected"
    lifecycle_sql = next(sql for sql in client.fetchone_sql if "WITH latest_approval AS" in sql)
    normalized = _compact(lifecycle_sql)
    assert "ORDER BY decided_at DESC, approval_id::text DESC LIMIT 1" in normalized
    assert (
        "ORDER BY occurred_at DESC, created_at DESC, disposition_id::text DESC LIMIT 1"
        in normalized
    )


def test_aging_uses_gold_total_order_before_filtering_latest_approval() -> None:
    clear_sales_state_cache()
    tied_at = datetime.now(UTC) - timedelta(days=14)
    client = _TotalOrderClient(
        approvals=[
            _approval(
                borrower_id="B-TIE-AGING-REJECTED",
                approval_id=_HIGH_ID,
                action="reject",
                decided_at=tied_at,
            ),
            _approval(
                borrower_id="B-TIE-AGING-REJECTED",
                approval_id=_LOW_ID,
                action="approve",
                decided_at=tied_at,
            ),
            _approval(
                borrower_id="B-TIE-AGING-APPROVED",
                approval_id=_HIGH_ID,
                action="approve",
                decided_at=tied_at,
            ),
            _approval(
                borrower_id="B-TIE-AGING-APPROVED",
                approval_id=_LOW_ID,
                action="reject",
                decided_at=tied_at,
            ),
        ]
    )
    store = SalesStateStore(client)  # type: ignore[arg-type]

    rows = store.aging(older_than_days=7)

    assert [row["borrower_id"] for row in rows] == ["B-TIE-AGING-APPROVED"]
    aging_sql = client.fetchall_sql[-1]
    normalized = _compact(aging_sql)
    assert "ORDER BY borrower_id, decided_at DESC, approval_id::text DESC" in normalized
    assert (
        "ORDER BY borrower_id, occurred_at DESC, created_at DESC, "
        "disposition_id::text DESC" in normalized
    )
