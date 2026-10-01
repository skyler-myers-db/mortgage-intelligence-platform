"""Derived state of one approval-request borrower (pure; no I/O).

The order is binding (D-approval-flow-c record item 9):

1. the latest finalized approve/reject LINKED to the request (its decision
   intent carries the batch id), decided at or after the request was made,
   gives ``approved`` / ``rejected`` and its approval id;
2. an item the requester withdrew is ``withdrawn``;
3. an item closed as expired, or a request older than 30 days, is
   ``expired``;
4. any later finalized approve/reject for the borrower WITHOUT the link is
   ``decided_outside``;
5. otherwise the borrower is ``open``.

Hold and revoke rows never reach this function: the read selects approve and
reject only. A revoke reopens the borrower by expiring its open item instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.schemas.approval_request import ApprovalRequestRow

REQUEST_EXPIRY = timedelta(days=30)
_DECISIONS = {"approve": "approved", "reject": "rejected"}


@dataclass(frozen=True)
class ItemFacts:
    """One item row of the request read, with the two decision facts."""

    batch_id: str
    borrower_id: str
    status: str
    batch_created_at: datetime
    linked_action: str | None
    linked_approval_id: str | None
    outside_action: str | None
    read_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ItemFacts:
        return cls(
            batch_id=str(row["batch_id"]),
            borrower_id=str(row["borrower_id"]),
            status=str(row["status"]),
            batch_created_at=_aware(row["batch_created_at"]),
            linked_action=_text_or_none(row.get("linked_action")),
            linked_approval_id=_text_or_none(row.get("linked_approval_id")),
            outside_action=_text_or_none(row.get("outside_action")),
            read_at=_aware(row["read_at"]),
        )


def _text_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("approval request timestamps must be datetimes")
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def derive_row(facts: ItemFacts) -> ApprovalRequestRow:
    """Apply the binding order to one item's facts."""

    linked = _DECISIONS.get(facts.linked_action or "")
    if linked == "approved":
        return ApprovalRequestRow(
            borrower_id=facts.borrower_id, state="approved", approval_id=facts.linked_approval_id
        )
    if linked == "rejected":
        return ApprovalRequestRow(
            borrower_id=facts.borrower_id, state="rejected", approval_id=facts.linked_approval_id
        )
    if facts.status == "withdrawn":
        return ApprovalRequestRow(borrower_id=facts.borrower_id, state="withdrawn")
    if facts.status == "expired" or facts.batch_created_at < facts.read_at - REQUEST_EXPIRY:
        return ApprovalRequestRow(borrower_id=facts.borrower_id, state="expired")
    if facts.outside_action in _DECISIONS:
        return ApprovalRequestRow(borrower_id=facts.borrower_id, state="decided_outside")
    return ApprovalRequestRow(borrower_id=facts.borrower_id, state="open")


def rows_by_batch(rows: list[dict[str, Any]]) -> dict[str, list[ApprovalRequestRow]]:
    """Derive every item row and group them by request, in read order."""

    grouped: dict[str, list[ApprovalRequestRow]] = {}
    for row in rows:
        facts = ItemFacts.from_row(row)
        grouped.setdefault(facts.batch_id, []).append(derive_row(facts))
    return grouped
