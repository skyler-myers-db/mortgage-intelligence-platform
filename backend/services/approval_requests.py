"""Maker-checker approval requests: list, withdraw, link and queue scope.

Audit 2026-09-21 flow-02 / shell-06 (report 12.4 #10, D-approval-flow-c). A
signed-in non-approver asks an approver to review named borrowers with a
required, screened note (the create flow is its own module). Nothing here
drafts copy or decides a borrower: approve and reject stay the approvers'
only decision writes, through the unchanged review flow, and each may carry
the request's batch id (``verify_decision_link`` refuses a closed request
and the requester deciding their own request).

Listing is audit-free: it is Lakebase workflow app state with no borrower
attribute. Withdraw writes APPROVAL_REQUEST_WITHDRAWN in its own transaction,
and only when it closed a borrower.

All requester/actor comparisons are case-folded and trimmed; the requester is
stored that way.
"""

from __future__ import annotations

from typing import Any, Literal

from backend.schemas.approval_request import (
    ApprovalRequestBatchView,
    ApprovalRequestList,
    ApprovalRequestScope,
    ApprovalRequestWithdrawn,
)
from backend.services import approval_request_sql as sql
from backend.services.approval_request_state import ItemFacts, derive_row, rows_by_batch
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.identity_display import display_name_for
from backend.services.lakebase import LakebaseClient

_MAX_ITEMS_PER_BATCH = 500
OPEN_SCOPE_LIMIT = 100


class ApprovalRequestNotFound(LookupError):
    """No finalized request has this batch id."""


class ApprovalRequestForbidden(PermissionError):
    """The caller is neither an approver nor the requester."""


class ApprovalRequestConflict(RuntimeError):
    """The request key already belongs to a different request payload."""


class ApprovalRequestRefused(RuntimeError):
    """No selected borrower could be requested; the refusal is audited."""

    def __init__(self, skipped_counts: dict[str, int], audit_event_id: str) -> None:
        super().__init__("no selected borrower can be requested")
        self.skipped_counts = skipped_counts
        self.audit_event_id = audit_event_id


class ApprovalRequestLinkRefused(RuntimeError):
    """A decision named a request it may not decide through."""

    def __init__(self, kind: Literal["not_open", "self"]) -> None:
        super().__init__(kind)
        self.kind: Literal["not_open", "self"] = kind


def normalize_actor(actor: str) -> str:
    return actor.strip().lower()


def _item_rows(
    lakebase: LakebaseClient, batch_ids: list[str], *, borrower_id: str | None = None
) -> list[dict[str, Any]]:
    return lakebase.fetchall(
        sql.ITEMS_WITH_DECISIONS,
        {"batch_ids": batch_ids, "borrower_id": borrower_id},
        limit=len(batch_ids) * _MAX_ITEMS_PER_BATCH,
    )


def _item_rows_in_transaction(conn: Any, batch_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        sql.ITEMS_WITH_DECISIONS, {"batch_ids": [batch_id], "borrower_id": None}
    ).fetchall()
    return [dict(row) for row in rows]


def list_approval_requests(
    lakebase: LakebaseClient,
    *,
    actor: str,
    scope: ApprovalRequestScope,
) -> ApprovalRequestList:
    """At most two statements: the batches, then their items with decisions."""

    requested_by = normalize_actor(actor)
    if scope == "open":
        batches = lakebase.fetchall(sql.OPEN_SCOPE_BATCHES, None, limit=2 * OPEN_SCOPE_LIMIT)
    else:
        batches = lakebase.fetchall(sql.MINE_SCOPE_BATCHES, {"requested_by": requested_by}, limit=50)
    if not batches:
        return ApprovalRequestList(scope=scope, batches=[])
    grouped = rows_by_batch(_item_rows(lakebase, [str(batch["batch_id"]) for batch in batches]))
    views: list[ApprovalRequestBatchView] = []
    for batch in batches:
        batch_id = str(batch["batch_id"])
        rows = grouped.get(batch_id, [])
        if scope == "open" and not any(row.state == "open" for row in rows):
            continue
        requester = str(batch["requested_by"])
        views.append(
            ApprovalRequestBatchView(
                batch_id=batch_id,
                requested_by_display=display_name_for(requester),
                is_mine=normalize_actor(requester) == requested_by,
                note=str(batch["note"]),
                created_at=batch["created_at"],
                requested_by=requester if scope == "mine" else None,
                rows=rows,
            )
        )
        if scope == "open" and len(views) >= OPEN_SCOPE_LIMIT:
            break
    return ApprovalRequestList(scope=scope, batches=views)


def withdraw_approval_request(
    lakebase: LakebaseClient,
    *,
    actor: str,
    batch_id: str,
) -> ApprovalRequestWithdrawn:
    """The requester closes every derived-open borrower; a repeat writes nothing."""

    with lakebase.transaction() as conn:
        batch = conn.execute(sql.BATCH_BY_ID_FOR_UPDATE, {"batch_id": batch_id}).fetchone()
        if batch is None:
            raise ApprovalRequestNotFound(batch_id)
        if normalize_actor(str(batch["requested_by"])) != normalize_actor(actor):
            raise ApprovalRequestForbidden(batch_id)
        rows = [derive_row(ItemFacts.from_row(row)) for row in _item_rows_in_transaction(conn, batch_id)]
        open_ids = [row.borrower_id for row in rows if row.state == "open"]
        withdrawn: list[str] = []
        if open_ids:
            withdrawn = [
                str(row["borrower_id"])
                for row in conn.execute(
                    sql.WITHDRAW_OPEN_ITEMS, {"batch_id": batch_id, "borrower_ids": open_ids}
                ).fetchall()
            ]
        audit_event_id: str | None = None
        if withdrawn:
            event = write_audit_event_in_transaction(
                conn,
                actor=actor,
                action="outreach.approval_request_withdraw",
                entity_type="approval_request_batch",
                entity_id=batch_id,
                payload_json={
                    "approval_request_batch_id": batch_id,
                    "withdrawn_count": len(withdrawn),
                    "borrower_ids": withdrawn,
                },
                event_type="APPROVAL_REQUEST_WITHDRAWN",
            )
            audit_event_id = event.event_id
        return ApprovalRequestWithdrawn(
            batch_id=batch_id,
            withdrawn_now=len(withdrawn),
            already_closed=len(rows) - len(withdrawn),
            audit_event_id=audit_event_id,
        )


def verify_decision_link(
    lakebase: LakebaseClient,
    *,
    batch_id: str,
    borrower_id: str,
    actor: str,
) -> None:
    """Refuse a decision linked to a closed request or to the actor's own.

    Advisory: it runs before the decision's own commit, outside it. Two
    approvers racing on one linked borrower both pass; the ledger keeps both
    finalized rows, and the request reads the LATEST linked one.
    """

    batch = lakebase.fetchone(sql.BATCH_BY_ID, {"batch_id": batch_id})
    rows = _item_rows(lakebase, [batch_id], borrower_id=borrower_id) if batch is not None else []
    if not rows or derive_row(ItemFacts.from_row(rows[0])).state != "open":
        raise ApprovalRequestLinkRefused("not_open")
    if batch is not None and normalize_actor(str(batch["requested_by"])) == normalize_actor(actor):
        raise ApprovalRequestLinkRefused("self")


def open_borrower_ids_for_queue(
    lakebase: LakebaseClient,
    *,
    batch_id: str,
    actor: str,
    is_approver: bool,
) -> list[str]:
    """The request's derived-open borrowers, for an approver or the requester."""

    batch = lakebase.fetchone(sql.BATCH_BY_ID, {"batch_id": batch_id})
    if batch is None:
        raise ApprovalRequestNotFound(batch_id)
    if not is_approver and normalize_actor(str(batch["requested_by"])) != normalize_actor(actor):
        raise ApprovalRequestForbidden(batch_id)
    return [
        row.borrower_id
        for row in rows_by_batch(_item_rows(lakebase, [batch_id])).get(batch_id, [])
        if row.state == "open"
    ]


def expire_open_items_for_borrower(conn: Any, borrower_id: str) -> list[str]:
    """Inside a caller's transaction: close the borrower's open request items.

    The revoke uses it so a borrower that is pending again can be requested
    again (the one-open-item index would otherwise hold it for 30 days).
    """

    rows = conn.execute(sql.EXPIRE_OPEN_ITEMS_FOR_BORROWER, {"borrower_id": borrower_id}).fetchall()
    return [str(row["batch_id"]) for row in rows]
