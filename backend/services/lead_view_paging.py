"""Lead Queue server paging under the VIEW_LEADS ruling (D-audit-reads-a, tables-02).

``GET /leads`` serves a VIEW: page 0 (no cursor) mints a ``view_id``; every
page whose rows continue carries a signed ``X-Next-Cursor`` (lead_view_cursor)
that the client sends back, with the identical request, on an explicit "Load
next". Exactly ONE VIEW_LEADS row is written per served 2xx, grouped by
``view_id`` and ordered by ``page_index``; ``rendered_borrower_ids`` means
"returned in this response".

What a page past 0 does NOT do: run the count, the whole-cohort identity
proof or the Growth Agent handoff verification. The cursor echoes page 0's
total and handoff provenance, and binds page 0's refresh stamp: a row of a
later refresh answers 409 so one view never mixes snapshots. A cursor that
fails verification answers 422 ``lead_view_cursor_invalid`` (the reason is
logged, the cursor never is); a deployment with no cursor key answers 503 on
a cursor and serves page 0 unpaged (``X-Lead-Paging: unavailable``).

Only a page-size request (``limit == LEAD_PAGE_SIZE``) is paged: the quick
pick, Segment Intelligence and the up-to-5,000 callers keep their semantics.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC

from fastapi import HTTPException, Response

from backend.schemas.lead import LeadSummary
from backend.schemas.lead_query import LEAD_MAX_PAGE_INDEX, LEAD_PAGE_SIZE, LeadQueryParams
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lead_query_resolution import ResolvedLeadQuery
from backend.services.lead_view_cursor import (
    HANDOFF_PROVENANCE_KEYS,
    LeadCursorRejected,
    LeadCursorUnavailable,
    LeadViewCursor,
    decode_lead_cursor,
    encode_lead_cursor,
    lead_view_filter_digest,
    lead_view_filter_fingerprint,
    lead_view_fingerprint_candidates,
)
from backend.services.observability import emit
from backend.services.repositories.databricks_lead_cohorts import (
    GrowthAgentHandoffStale,
    validate_growth_agent_handoff_identity,
)
from backend.services.repositories.databricks_lead_order import LeadOrder, LeadPage

log = logging.getLogger(__name__)

LEAD_VIEW_CURSOR_INVALID_DETAIL = "lead_view_cursor_invalid"
LEAD_VIEW_REFRESHED_DETAIL = "The queue refreshed since this view loaded"

# Characters an ISO-8601 UTC timestamp may carry. Anything else (a CR or LF
# above all) never reaches a response header.
_DATA_REFRESHED_AT_SAFE = re.compile(r"[0-9TZ:.+-]+")


def data_refreshed_at_header(leads: list[LeadSummary]) -> str | None:
    """The newest gold refresh time among the returned rows, as ISO-8601 UTC.

    Audit delivery-08: the Lead Queue stamped its CSV export with a
    ``refreshed_at`` it fetched through a whole-book portfolio preview on
    every mount. The rows already carry it, so the list response states it
    with no extra statement. None when no row has a value.
    """
    stamps = [lead.row_refreshed_at for lead in leads if lead.row_refreshed_at is not None]
    if not stamps:
        return None
    latest = max(stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC) for stamp in stamps)
    value = latest.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value if _DATA_REFRESHED_AT_SAFE.fullmatch(value) else None


def lead_view_order(sort: str, sort_dir: str) -> LeadOrder:
    """The validated server sort; ``rank`` carries no direction."""

    return LeadOrder.of(sort, sort_dir)


@dataclass(frozen=True)
class LeadView:
    """One request's place in a view: page 0 (new view) or a decoded cursor."""

    view_id: str
    page_index: int
    order: LeadOrder
    fingerprint: str | None
    cursor: LeadViewCursor | None
    paged: bool
    handoff: dict[str, str] | None = None
    handoff_expires_at: int | None = None


def new_view_id() -> str:
    return uuid.uuid4().hex


def open_lead_view(
    *,
    params: LeadQueryParams,
    resolved: ResolvedLeadQuery,
    cursor: str | None,
    order: LeadOrder,
    limit: int,
    growth_handoff: Sequence[str],
    approval_request_batch: str | None,
) -> LeadView:
    """Mint page 0's view, or verify the cursor of a later page.

    ``params`` are the REQUEST's parameters (before an approval request
    resolves to its open borrowers): the fingerprint covers request-level
    inputs only, never a Lakebase-resolved list.
    """

    digest = lead_view_filter_digest(
        params,
        portfolio_criteria=resolved.portfolio_criteria,
        order=order,
        growth_handoff=growth_handoff[0].strip() if growth_handoff else None,
        approval_request_batch=approval_request_batch,
    )
    if cursor is None:
        proof = resolved.handoff_proof
        return LeadView(
            view_id=new_view_id(),
            page_index=0,
            order=order,
            fingerprint=lead_view_filter_fingerprint(digest),
            cursor=None,
            paged=limit == LEAD_PAGE_SIZE,
            handoff=_handoff_provenance(resolved),
            handoff_expires_at=proof.expires_at if proof is not None else None,
        )
    try:
        decoded = decode_lead_cursor(cursor, actor=resolved.actor, fp_candidates=lead_view_fingerprint_candidates(digest))
    except LeadCursorRejected as exc:
        emit(log, "lead_cursor_rejected", reason=exc.reason, outcome="refused")
        raise HTTPException(status_code=422, detail=LEAD_VIEW_CURSOR_INVALID_DETAIL) from exc
    except LeadCursorUnavailable as exc:
        emit(log, "lead_cursor_unavailable", outcome="error")
        raise HTTPException(status_code=503, detail=safe_dependency_detail("warehouse")) from exc
    return LeadView(
        view_id=decoded.view_id,
        page_index=decoded.page,
        order=order,
        fingerprint=decoded.fp,
        cursor=decoded,
        paged=True,
        handoff=decoded.handoff,
    )


def _handoff_provenance(resolved: ResolvedLeadQuery) -> dict[str, str] | None:
    proof = resolved.handoff_proof
    if proof is None:
        return None
    values = (
        proof.run_id,
        proof.filters_fingerprint,
        proof.cohort_fingerprint,
        proof.source_snapshot,
        proof.tool_result_hash,
    )
    return dict(zip(HANDOFF_PROVENANCE_KEYS, values, strict=True))


@dataclass(frozen=True)
class LeadViewRead:
    leads: list[LeadSummary]
    total_matching: int
    identity: dict[str, str | int] | None
    page: LeadPage | None


def _sort_kwargs(order: LeadOrder) -> dict[str, object]:
    # Only a server sort is passed on: repositories and test doubles written
    # before paging keep their signature for the rank order.
    return {} if order.is_rank else {"sort": order.sort, "sort_dir": order.sort_dir}


def read_lead_view(repo: object, resolved: ResolvedLeadQuery, view: LeadView, *, limit: int) -> LeadViewRead:
    """The rows of this page: page 0 as today (sorted), a later page by keyset."""

    args = resolved.repository_args
    if view.cursor is not None:
        list_page = getattr(repo, "list_page", None)
        if not callable(list_page):
            raise HTTPException(status_code=503, detail="Lead Queue paging is unavailable")
        page = list_page(
            limit=LEAD_PAGE_SIZE,
            sort=view.order.sort,
            sort_dir=view.order.sort_dir,
            after=view.cursor.after,
            **args,
        )
        return LeadViewRead(page.leads, view.cursor.total, None, page)
    if resolved.include_identity_proof or resolved.handoff_proof is not None:
        return _read_with_identity(repo, resolved, view, limit=limit)
    list_page = getattr(repo, "list_page", None)
    if callable(list_page):
        page = list_page(limit=limit, **_sort_kwargs(view.order), **args)
        leads = page.leads
    else:
        page = None
        leads = repo.list(limit=limit, **args)  # type: ignore[attr-defined]
    count_fn = getattr(repo, "count", None)
    # Test-local repositories and external connectors may implement only
    # the list contract. Production reports the complete cohort count.
    total = count_fn(**args) if callable(count_fn) else len(leads)
    return LeadViewRead(leads, int(total), None, page)


def _read_with_identity(repo: object, resolved: ResolvedLeadQuery, view: LeadView, *, limit: int) -> LeadViewRead:
    paged_read = getattr(repo, "list_with_identity_page", None)
    list_with_identity = getattr(repo, "list_with_identity", None)
    if not callable(paged_read) and not callable(list_with_identity):
        raise HTTPException(status_code=503, detail="Lead Queue cohort identity proof is unavailable")
    page: LeadPage | None = None
    try:
        if callable(paged_read):
            page, identity = paged_read(limit=limit, **_sort_kwargs(view.order), **resolved.repository_args)
            leads = page.leads
        else:
            leads, identity = list_with_identity(  # type: ignore[misc]
                limit=limit, **_sort_kwargs(view.order), **resolved.repository_args
            )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Lead Queue cohort identity proof is incomplete") from exc
    if resolved.handoff_proof is not None:
        try:
            validate_growth_agent_handoff_identity(resolved.handoff_proof, identity)
        except GrowthAgentHandoffStale as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return LeadViewRead(leads, int(identity.get("total") or 0), identity, page)


def refuse_an_emptied_view(cursor: str | None) -> None:
    """409 when a later page's population emptied since page 0.

    The assignee lost every borrower, or the approval request has no open
    borrower left: the view changed under the reader like a refresh does, so
    the client restarts at page 0 (which answers the empty view and writes
    its one row) instead of appending an empty page of a new view to rows
    the server no longer lists. Nothing is written for the 409.
    """

    if cursor is not None:
        raise HTTPException(status_code=409, detail=LEAD_VIEW_REFRESHED_DETAIL)


def refuse_a_refreshed_page(view: LeadView, leads: list[LeadSummary]) -> None:
    """409 when a later page's rows come from another gold refresh than page 0's."""

    if view.cursor is None:
        return
    for lead in leads:
        if lead.row_refreshed_at is None:
            continue
        if data_refreshed_at_header([lead]) != view.cursor.refreshed:
            raise HTTPException(status_code=409, detail=LEAD_VIEW_REFRESHED_DETAIL)


def stamp_lead_view(
    response: Response,
    view: LeadView,
    read: LeadViewRead,
    *,
    actor: str,
    refreshed: str | None,
) -> None:
    """The view headers, and the next cursor when this page's rows continue."""

    response.headers["X-Lead-View-Id"] = view.view_id
    response.headers["X-Page-Index"] = str(view.page_index)
    if not view.paged:
        return
    if read.page is None or view.fingerprint is None:
        _unavailable(response)
        return
    next_index = view.page_index + 1
    if not read.page.has_more or next_index > LEAD_MAX_PAGE_INDEX or read.page.last_keyset is None:
        return
    try:
        response.headers["X-Next-Cursor"] = encode_lead_cursor(
            actor=actor,
            view_id=view.view_id,
            page=next_index,
            fp=view.fingerprint,
            refreshed=view.cursor.refreshed if view.cursor is not None else refreshed,
            total=read.total_matching,
            handoff=view.handoff,
            handoff_expires_at=view.handoff_expires_at if view.cursor is None else view.cursor.exp,
            after=read.page.last_keyset,
        )
    except LeadCursorUnavailable:
        _unavailable(response)


def _unavailable(response: Response) -> None:
    response.headers["X-Lead-Paging"] = "unavailable"
    emit(log, "lead_cursor_unavailable", outcome="degraded")


def lead_view_audit_fields(view: LeadView, read: LeadViewRead, *, refreshed: str | None) -> dict[str, object]:
    """What a VIEW_LEADS row adds to ``view_leads_audit_payload``."""

    fields: dict[str, object] = {
        "view_id": view.view_id,
        "page_index": view.page_index,
        "sort": view.order.sort,
        "total_matching": read.total_matching,
    }
    if view.order.sort_dir is not None:
        fields["sort_dir"] = view.order.sort_dir
    if view.fingerprint is not None:
        fields["filter_fingerprint"] = view.fingerprint
    if refreshed is not None:
        fields["source_refreshed_at"] = refreshed
    if view.cursor is not None and view.handoff is not None:
        fields.update(view.handoff)
    return fields


def empty_view_audit_fields() -> dict[str, object]:
    """An early empty answer (no assignment match, a request with no open borrower)."""

    return {"view_id": new_view_id(), "page_index": 0, "sort": "rank", "total_matching": 0}
