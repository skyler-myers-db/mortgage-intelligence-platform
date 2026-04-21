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

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from backend.schemas.lead import LeadSummary
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.lakebase import LakebaseError
from backend.services.repositories import LeadRepository, get_lead_repository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["leads"])

RepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]


def _safe_audit_write(store: AuditStore, **kwargs: object) -> None:
    try:
        store.write(**kwargs)  # type: ignore[arg-type]
    except LakebaseError as exc:
        log.warning("audit.write dropped: %s", exc)


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
    segment: str | None = None,
    portfolio_id: str | None = None,
) -> list[LeadSummary]:
    leads = repo.list(segment=segment, portfolio_id=portfolio_id)
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
        },
        event_type="VIEW_LEADS",
        subject_segment=segment,
    )
    return leads
