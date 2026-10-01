"""Leads list API.

Slice 5 adds audit emission on the ranked-list view: one
``VIEW_LEADS`` row per render, carrying the segment filter (if any)
and the list of borrower_ids the user saw. Governance §4 wants this
so we can reconstruct "which list did the approver see when they
decided to approve". No PII lands in the audit row -- borrower ids are
already masked before API egress.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

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
    LeadQueryParams,
    LeadQueryParamsDep,
)
from backend.services.audit_store import AuditStore, get_audit_store
from backend.services.lakebase import LakebaseError
from backend.services.lead_query_resolution import (
    resolve_lead_query,
    view_leads_audit_payload,
    without_facet_dimension,
)
from backend.services.observability import emit
from backend.services.repositories import LeadRepository, get_lead_repository
from backend.services.repositories.databricks_lead_cohorts import (
    GrowthAgentHandoffStale,
    validate_growth_agent_handoff_identity,
)
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


def _refuse_list_only_params(params: LeadQueryParams) -> None:
    if params.include_identity_proof:
        raise HTTPException(status_code=422, detail=IDENTITY_PROOF_LIST_ONLY_DETAIL)
    if params.borrower_ids and params.borrower_ids.strip():
        raise HTTPException(status_code=422, detail=BORROWER_LIST_LIST_ONLY_DETAIL)


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


# Characters an ISO-8601 UTC timestamp may carry. Anything else (a CR or LF
# above all) never reaches a response header.
_DATA_REFRESHED_AT_SAFE = re.compile(r"[0-9TZ:.+-]+")


def _data_refreshed_at_header(leads: list[LeadSummary]) -> str | None:
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
) -> list[LeadSummary]:
    resolved = resolve_lead_query(
        request,
        sales_state,
        params,
        growth_handoff=request.query_params.getlist("growth_handoff"),
    )
    if resolved.assignment_empty:
        response.headers["X-Total-Matching"] = "0"
        response.headers["X-Returned-Rows"] = "0"
        return []
    actor = resolved.actor
    repository_args = resolved.repository_args
    handoff_proof = resolved.handoff_proof
    include_identity_proof = resolved.include_identity_proof
    cohort_id = resolved.cohort_id
    cohort_stated_count = resolved.cohort_stated_count
    cohort_unreplayable = resolved.cohort_unreplayable
    identity: dict[str, str | int] | None = None
    if include_identity_proof or handoff_proof is not None:
        list_with_identity = getattr(repo, "list_with_identity", None)
        if not callable(list_with_identity):
            raise HTTPException(
                status_code=503,
                detail="Lead Queue cohort identity proof is unavailable",
            )
        try:
            leads, identity = list_with_identity(limit=limit, **repository_args)
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail="Lead Queue cohort identity proof is incomplete",
            ) from exc
        if handoff_proof is not None:
            try:
                validate_growth_agent_handoff_identity(handoff_proof, identity)
            except GrowthAgentHandoffStale as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        total_matching = int(identity.get("total") or 0)
    else:
        leads = repo.list(limit=limit, **repository_args)
        count_fn = getattr(repo, "count", None)
        # Test-local repositories and external connectors may implement only
        # the list contract. Production reports the complete cohort count.
        total_matching = count_fn(**repository_args) if callable(count_fn) else len(leads)

    try:
        leads = hydrate_leads_with_sales_state(leads, sales_state, actor=actor)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
    response.headers["X-Total-Matching"] = str(total_matching)
    response.headers["X-Returned-Rows"] = str(len(leads))
    data_refreshed_at = _data_refreshed_at_header(leads)
    if data_refreshed_at is not None:
        response.headers["X-Data-Refreshed-At"] = data_refreshed_at
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
