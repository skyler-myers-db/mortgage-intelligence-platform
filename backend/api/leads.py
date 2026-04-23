"""Leads list API.

Slice 5 adds audit emission on the ranked-list view: one
``VIEW_LEADS`` row per render, carrying the segment filter (if any)
and the list of borrower_ids the user saw. Governance §4 wants this
so we can reconstruct "which list did the approver see when they
decided to approve". No PII lands in the audit row -- borrower ids are
already the synthetic ``B-#####`` form.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response

from backend.schemas.lead import LeadSummary
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.observability import emit
from backend.services.repositories import LeadRepository, get_lead_repository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["leads"])

# Kept in sync with DatabricksLeadRepository.{DEFAULT_LIMIT, MAX_LIMIT}.
# Exposed as module-level constants so the router's Query() annotations
# and the unit tests can both read one source of truth.
DEFAULT_LEAD_LIMIT: int = 500
MAX_LEAD_LIMIT: int = 5000

RepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]


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


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    request: Request,
    response: Response,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
    segment: str | None = None,
    portfolio_id: str | None = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_LEAD_LIMIT,
            description=(
                "Maximum leads to return. Defaults to 500; max 5000. When the "
                "resultset hits this cap the response sets `X-Truncated-At` "
                "so the UI can render 'Showing N — refine filters'."
            ),
        ),
    ] = DEFAULT_LEAD_LIMIT,
) -> list[LeadSummary]:
    # Hole-finder round 2 #24, 2026-04-23: plumb an optional `limit` down to
    # the gold read so lenders with >500 in-the-money borrowers aren't
    # silently capped at the first page. Bounded at MAX_LEAD_LIMIT to keep
    # the warehouse scan pageable.
    leads = repo.list(segment=segment, portfolio_id=portfolio_id, limit=limit)
    # When the result set hit the requested cap, advertise the truncation
    # explicitly so the frontend can tell "exactly N" vs "N and there's
    # more you didn't see". We can't distinguish at this layer between
    # "exactly N rows exist" and "more than N exist"; the header is a
    # conservative signal ("capped at") and the UI phrases it that way.
    if len(leads) >= limit:
        response.headers["X-Truncated-At"] = str(limit)
    background.add_task(
        _safe_audit_write,
        audit,
        actor=resolve_actor(request),
        action="view_leads_ranked",
        entity_type="lead_queue",
        entity_id=segment or "_all",
        payload_json={
            "rendered_borrower_ids": [lead.borrower_id for lead in leads],
            "portfolio_id": portfolio_id,
            "segment": segment,
            "limit": limit,
        },
        event_type="VIEW_LEADS",
        subject_segment=segment,
    )
    return leads
