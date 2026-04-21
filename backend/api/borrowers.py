"""Borrower-360 read API.

Slice 5 adds audit emission: every dossier view emits a
``VIEW_BORROWER`` row via the Lakebase-backed audit store so governance
§4's "who viewed which CLIP" question is answerable. Audit failures
fall back to a log line rather than 503'ing the GET -- the user
experience of the dossier is more important than a missed audit row
during a transient Lakebase outage, and Slice 6 adds proper resilience
for the audit path specifically.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.lakebase import LakebaseError
from backend.services.repositories import BorrowerRepository, get_borrower_repository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/borrowers", tags=["borrowers"])

RepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]


def _safe_audit_write(store: AuditStore, **kwargs: object) -> None:
    """Write an audit row, swallowing + logging Lakebase failures.

    Used by background tasks: the user-facing response already returned
    by the time this runs, so raising here would just orphan the
    connection. We log.warning so operators see the problem in the
    structured log without disrupting the demo.
    """
    try:
        store.write(**kwargs)  # type: ignore[arg-type]
    except LakebaseError as exc:
        log.warning("audit.write dropped: %s", exc)


@router.get("/{borrower_id}", response_model=Borrower360)
def get_borrower(
    borrower_id: str,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
) -> Borrower360:
    borrower = repo.get(borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    # Governance §4: record score components + thresholds so we can
    # reconstruct "what the approver saw" after the fact. No PII lands
    # in metadata -- display_name and subject_property stay out.
    background.add_task(
        _safe_audit_write,
        audit,
        actor=resolve_actor(request),
        action="view_borrower_360",
        entity_type="borrower",
        entity_id=borrower.borrower_id,
        payload_json={
            "opportunity_score": borrower.opportunity_score,
            "confidence": borrower.confidence,
            "segment_codes": borrower.segment_codes,
            "recommended_offer": borrower.recommended_offer,
        },
        evidence_ids=list(borrower.evidence_ids),
        event_type="VIEW_BORROWER",
        subject_clip=borrower.clip_id,
    )
    return borrower


@router.get("/{borrower_id}/evidence", response_model=list[EvidenceEvent])
def get_borrower_evidence(borrower_id: str, repo: RepoDep) -> list[EvidenceEvent]:
    events = repo.evidence(borrower_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    return events
