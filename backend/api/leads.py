"""Leads list API.

Slice 5 adds audit emission on the ranked-list view: one
``VIEW_LEADS`` row per render, carrying the segment filter (if any)
and the list of borrower_ids the user saw. Governance §4 wants this
so we can reconstruct "which list did the approver see when they
decided to approve". No PII lands in the audit row -- borrower ids are
already masked before API egress.

D-audit-reads-a (W5c): a served page is one page of a VIEW. Every served 2xx
writes exactly one VIEW_LEADS row, grouped by the server-minted ``view_id``
and ordered by ``page_index`` (``backend/services/lead_view_paging.py``).
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

from backend.schemas.approval_request import canonical_uuid_text
from backend.schemas.lead import LeadSummary
from backend.schemas.lead_facets import (
    LeadCountResponse,
    LeadFacetBucket,
    LeadFacetDimension,
    LeadFacetsResponse,
)
from backend.schemas.lead_query import (
    DEFAULT_LEAD_LIMIT,
    MAX_LEAD_LIMIT,
    LeadLimitDep,
    LeadPagingParamsDep,
    LeadQueryParams,
    LeadQueryParamsDep,
)
from backend.services.approval_requests import (
    ApprovalRequestForbidden,
    ApprovalRequestNotFound,
    open_borrower_ids_for_queue,
)
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.lead_query_resolution import (
    resolve_lead_query,
    view_leads_audit_payload,
    without_facet_dimension,
)
from backend.services.lead_view_paging import (
    data_refreshed_at_header,
    empty_view_audit_fields,
    lead_view_audit_fields,
    lead_view_order,
    open_lead_view,
    read_lead_view,
    refuse_a_refreshed_page,
    refuse_an_emptied_view,
    stamp_lead_view,
)
from backend.services.observability import emit
from backend.services.rbac import can_access_approver, require_authenticated_actor
from backend.services.repositories import LeadRepository, get_lead_repository
from backend.services.repositories.factory import get_lead_facet_repository
from backend.services.repositories.protocols import LeadFacetRepository
from backend.services.sales_state import (
    SalesStateStore,
    get_sales_state_store,
    hydrate_leads_with_sales_state,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])

# Re-exported: the limit bounds are declared next to the Query() annotation
# that enforces them, and callers (backend.services.lead_warm, the unit
# tests) have always read them from here.
__all__ = ["DEFAULT_LEAD_LIMIT", "MAX_LEAD_LIMIT", "router"]

RepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]
SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]
FacetRepoDep = Annotated[LeadFacetRepository, Depends(get_lead_facet_repository)]
FacetDimensionParam = Annotated[
    LeadFacetDimension,
    Query(description="The filter menu to count: state, segment, product or approval."),
]

IDENTITY_PROOF_LIST_ONLY_DETAIL = "include_identity_proof applies to GET /leads only"
# A count or facet over named borrowers reads their attributes without the
# VIEW_LEADS row the ranked list writes: a per-borrower read must be the
# audited list, so the audit-free aggregates refuse a borrower list.
BORROWER_LIST_LIST_ONLY_DETAIL = "borrower_ids applies to GET /leads only: a read of named borrowers is audited"
REQUEST_SCOPE_LIST_ONLY_DETAIL = (
    "approval_request_batch applies to GET /leads only: a read of named borrowers is audited"
)
REQUEST_SCOPE_COMBINATION_DETAIL = (
    "approval_request_batch cannot be combined with cohort_id, borrower_ids or growth_handoff"
)


def _refuse_list_only_params(params: LeadQueryParams) -> None:
    if params.include_identity_proof:
        raise HTTPException(status_code=422, detail=IDENTITY_PROOF_LIST_ONLY_DETAIL)
    if params.borrower_ids and params.borrower_ids.strip():
        raise HTTPException(status_code=422, detail=BORROWER_LIST_LIST_ONLY_DETAIL)
    if params.approval_request_batch is not None:
        raise HTTPException(status_code=422, detail=REQUEST_SCOPE_LIST_ONLY_DETAIL)


def _approval_request_scope(request: Request, request_batch: str, params: LeadQueryParams) -> list[str]:
    """The open borrowers of the named approval request (maker-checker, flow-02).

    Lakebase is resolved here, never as a route dependency, so the Lead Queue
    never fails on Lakebase configuration when no request is named.
    """

    if (
        params.cohort_id
        or (params.borrower_ids and params.borrower_ids.strip())
        or request.query_params.getlist("growth_handoff")
    ):
        raise HTTPException(status_code=422, detail=REQUEST_SCOPE_COMBINATION_DETAIL)
    actor = require_authenticated_actor(request)
    try:
        return open_borrower_ids_for_queue(
            get_lakebase_client(),
            batch_id=request_batch,
            actor=actor,
            is_approver=can_access_approver(request),
        )
    except ApprovalRequestNotFound as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except ApprovalRequestForbidden as exc:
        raise HTTPException(
            status_code=403, detail="Only approvers and the requester can open this approval request."
        ) from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc


def _safe_audit_write(store: AuditStore, **kwargs: object) -> None:
    """Background-task audit writer -- swallow + log every failure.

    R5-18: broaden from ``LakebaseError`` to ``Exception`` because this
    runs in BackgroundTasks and an unhandled exception is silently
    swallowed by FastAPI's runner. Emit only the exception class name
    (never ``str(exc)``, which can leak payload content) so operators
    see the pattern in structured logs without widening the PII
    surface.
    """
    try:
        store.write(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 -- background path must not raise
        emit(
            log,
            "audit.dropped",
            dependency="lakebase",
            exc_type=type(exc).__name__,
            outcome="error",
        )


# Kept under its old name: tests and readers import the formatter from here.
_data_refreshed_at_header = data_refreshed_at_header


def _empty_view(
    response: Response,
    background: BackgroundTasks,
    audit: AuditStore,
    *,
    actor: str,
    payload: dict[str, object],
) -> list[LeadSummary]:
    """An answer read nothing: still one served page of a new view, so one row."""

    fields = empty_view_audit_fields()
    response.headers["X-Total-Matching"] = "0"
    response.headers["X-Returned-Rows"] = "0"
    response.headers["X-Lead-View-Id"] = str(fields["view_id"])
    response.headers["X-Page-Index"] = "0"
    segment = payload.get("segment")
    background.add_task(
        _safe_audit_write,
        audit,
        actor=actor,
        action="view_leads_ranked",
        entity_type="lead_queue",
        entity_id=str(segment) if segment else "_all",
        payload_json={**payload, **fields},
        event_type="VIEW_LEADS",
        subject_segment=str(segment) if segment else None,
    )
    return []


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
    sales_state: SalesStateDep,
    params: LeadQueryParamsDep,
    limit: LeadLimitDep,
    paging: LeadPagingParamsDep,
) -> list[LeadSummary]:
    cursor = paging.cursor
    order = lead_view_order(paging.sort, paging.sort_dir)
    growth_handoff = request.query_params.getlist("growth_handoff")
    # One spelling (the pattern admits either case): the service, the
    # VIEW_LEADS row and every later reader see the id as the ledger prints it.
    request_batch = canonical_uuid_text(params.approval_request_batch)
    request_params = params
    if request_batch is not None:
        open_ids = _approval_request_scope(request, request_batch, params)
        if not open_ids:
            refuse_an_emptied_view(cursor)
            # Never an empty borrower_ids: it parses to None, the whole queue.
            return _empty_view(
                response,
                background,
                audit,
                actor=resolve_actor(request),
                payload={
                    "rendered_borrower_ids": [],
                    "portfolio_id": params.portfolio_id,
                    "segment": params.segment,
                    "limit": limit,
                    "approval_request_batch_id": request_batch,
                },
            )
        params = dataclasses.replace(
            params, borrower_ids=",".join(open_ids), approval_request_batch=request_batch
        )
    # The preamble (admin gates, assignee checks, cohort replay) runs on EVERY
    # page; only a page-0 request re-verifies a Growth Agent handoff, which a
    # later page's signed cursor binds.
    resolved = resolve_lead_query(
        request,
        sales_state,
        params,
        growth_handoff=growth_handoff,
        verify_handoff=cursor is None,
    )
    if resolved.assignment_empty:
        refuse_an_emptied_view(cursor)
        return _empty_view(
            response,
            background,
            audit,
            actor=resolved.actor,
            payload=view_leads_audit_payload(resolved, [], limit=limit),
        )
    actor = resolved.actor
    view = open_lead_view(
        params=request_params,
        resolved=resolved,
        cursor=cursor,
        order=order,
        limit=limit,
        growth_handoff=growth_handoff,
        approval_request_batch=request_batch,
    )
    read = read_lead_view(repo, resolved, view, limit=limit)
    refuse_a_refreshed_page(view, read.leads)
    handoff_proof = resolved.handoff_proof
    include_identity_proof = resolved.include_identity_proof
    cohort_id = resolved.cohort_id
    cohort_stated_count = resolved.cohort_stated_count
    cohort_unreplayable = resolved.cohort_unreplayable
    identity = read.identity
    total_matching = read.total_matching

    try:
        leads = hydrate_leads_with_sales_state(read.leads, sales_state, actor=actor)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
    response.headers["X-Total-Matching"] = str(total_matching)
    response.headers["X-Returned-Rows"] = str(len(leads))
    data_refreshed_at = data_refreshed_at_header(leads)
    if data_refreshed_at is not None:
        response.headers["X-Data-Refreshed-At"] = data_refreshed_at
    stamp_lead_view(response, view, read, actor=actor, refreshed=data_refreshed_at)
    if cohort_id and cohort_stated_count is not None:
        # What the Genie answer said, next to what this queue actually matched.
        # The queue replays only the reviewed geography/segment subset, so an
        # answer narrowed by any numeric threshold replays broader — measured
        # live 2026-08-10 at 55x (32 borrowers -> 1,766). The UI compares these
        # two and says so rather than presenting a different population under
        # the same question.
        response.headers["X-Cohort-Stated-Count"] = str(cohort_stated_count)
        if cohort_stated_count != total_matching:
            response.headers["X-Cohort-Count-Delta"] = str(total_matching - cohort_stated_count)
    if cohort_id and cohort_unreplayable:
        # Deliberately NOT nested under the stated-count branch. What the queue
        # could not replay is the reason its number differs, so it has to be
        # reported even when the cohort row carries no row_count to compare
        # against — otherwise the one case with no stated count is also the one
        # that says nothing about why (adversarial review 2026-08-11).
        # Already re-folded and capped by CohortReplay: these names are
        # model-authored and rows written by earlier builds stored them raw, so
        # a stored `ltv<=80` raised UnicodeEncodeError out of this route (an
        # unhandled 500 that made the cohort permanently unopenable) and CRLF
        # made h11 reject the response outright.
        response.headers["X-Cohort-Unreplayable-Filters"] = ",".join(cohort_unreplayable)
    if identity is not None and "ranked_total" in identity:
        # Geo-filtered reads report the geography population as the total
        # (map-tile promise); this header carries the ranked subset
        # (score >= 50) so the UI can state both truthfully (audit C4).
        response.headers["X-Ranked-Matching"] = str(identity["ranked_total"])
    if identity is not None:
        response.headers["X-Cohort-Snapshot-ID"] = str(identity["snapshot_id"])
        if include_identity_proof:
            response.headers["X-Cohort-Digest"] = str(identity["cohort_digest"])
    if handoff_proof is not None:
        response.headers["X-Cohort-Fingerprint"] = handoff_proof.cohort_fingerprint
        response.headers["X-Growth-Agent-Run-ID"] = handoff_proof.run_id
    # When the result set hit the requested cap, advertise the truncation
    # explicitly so the frontend can tell "exactly N" vs "N and there's
    # more you didn't see". We can't distinguish at this layer between
    # "exactly N rows exist" and "more than N exist"; the header is a
    # conservative signal ("capped at") and the UI phrases it that way.
    if len(leads) >= limit:
        response.headers["X-Truncated-At"] = str(limit)
    audit_payload = view_leads_audit_payload(resolved, leads, limit=limit)
    audit_payload.update(lead_view_audit_fields(view, read, refreshed=data_refreshed_at))
    if request_batch is not None:
        audit_payload["approval_request_batch_id"] = request_batch
    segment = resolved.segment
    parsed_segments = resolved.segment_codes
    background.add_task(
        _safe_audit_write,
        audit,
        actor=actor,
        action="view_leads_ranked",
        entity_type="lead_queue",
        entity_id=segment or (",".join(parsed_segments) if parsed_segments else "_all"),
        payload_json=audit_payload,
        event_type="VIEW_LEADS",
        subject_segment=segment or (",".join(parsed_segments) if parsed_segments else None),
    )
    return leads


@router.get("/leads/count", response_model=LeadCountResponse)
def count_leads(
    request: Request,
    repo: RepoDep,
    sales_state: SalesStateDep,
    params: LeadQueryParamsDep,
) -> LeadCountResponse:
    """Audit-free total for the Lead Queue filters (wow-power-6, wow-power-2).

    The same authorization preamble, filters, "Eligible only" default and
    short-TTL repository cache as the ranked list's X-Total-Matching. No
    borrower is shown, so no VIEW_LEADS row is written and the audit store is
    never resolved. A Growth Agent handoff binds ranked rows, so it is never
    read here.
    """

    _refuse_list_only_params(params)
    resolved = resolve_lead_query(request, sales_state, params, growth_handoff=None)
    if resolved.assignment_empty:
        return LeadCountResponse(total_matching=0)
    return LeadCountResponse(total_matching=int(repo.count(**resolved.repository_args)))


@router.get("/leads/facets", response_model=LeadFacetsResponse)
def lead_facets(
    request: Request,
    facets: FacetRepoDep,
    sales_state: SalesStateDep,
    dimension: FacetDimensionParam,
    params: LeadQueryParamsDep,
) -> LeadFacetsResponse:
    """Audit-free option counts for one Lead Queue filter menu (tables-06).

    The dimension's own filter is dropped, every other filter resolves
    exactly as the ranked list resolves it, and the buckets are closed
    vocabularies. Fetched only when a user opens that menu.
    """

    _refuse_list_only_params(params)
    params = without_facet_dimension(params, dimension)
    resolved = resolve_lead_query(request, sales_state, params, growth_handoff=None)
    if resolved.assignment_empty:
        return LeadFacetsResponse(dimension=dimension, total_matching=0, buckets=[])
    counts = facets.facets(dimension, **resolved.repository_args)
    return LeadFacetsResponse(
        dimension=dimension,
        total_matching=counts.total_matching,
        buckets=[LeadFacetBucket(value=value, count=count) for value, count in counts.buckets],
    )
