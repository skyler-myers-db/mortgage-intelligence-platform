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
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from backend.schemas.common import EvidenceEvent, validate_public_borrower_id
from backend.schemas.lead import Borrower360, LeadSummary
from backend.schemas.proof import BorrowerProof
from backend.services.audit_decision_inputs import decision_inputs_from_borrower
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.observability import emit
from backend.services.proof_policy import hash_sql
from backend.services.repositories import BorrowerRepository, get_borrower_repository
from backend.services.sales_state import (
    SalesStateStore,
    get_sales_state_store,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/borrowers", tags=["borrowers"])

RepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]
SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]


def _path_borrower_id(value: str) -> str:
    try:
        return validate_public_borrower_id(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="borrower_id must be an app-scoped public borrower id",
        ) from exc


def _safe_audit_write(store: AuditStore, **kwargs: object) -> None:
    """Write an audit row, swallowing + logging every failure.

    Used by background tasks: the user-facing response has already
    returned by the time this runs, so raising here would just orphan
    the connection.

    R5-18: widened from ``LakebaseError`` to ``Exception`` because any
    unhandled exception on the background-task path is silently
    swallowed by the BackgroundTasks runner; a bare ``TypeError`` from a
    malformed payload could disappear without an operator ever seeing
    it. We emit a structured ``event=audit.dropped`` carrying the
    exception CLASS NAME only -- never ``str(exc)``, which could echo
    back caller-supplied payload content and defeat the PII posture of
    the audit ledger.
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


def _hydrate_borrower_sales_state(
    borrower: Borrower360,
    *,
    sales_state: SalesStateStore,
    actor: str,
) -> Borrower360:
    try:
        visible_lo_emails = sales_state.visible_lo_emails(actor=actor)
    except KeyError:
        visible_lo_emails = set()
    if visible_lo_emails == set():
        return borrower
    lifecycle = sales_state.lifecycle_for(borrower.borrower_id)
    update: dict[str, object] = {
        "approval_status": lifecycle.get("approval_status") or borrower.approval_status,
        "outreach_status": lifecycle.get("outreach_status") or borrower.outreach_status,
        "approved_at": lifecycle.get("approved_at"),
        "outreach_at": lifecycle.get("outreach_at"),
    }
    assignment = lifecycle.get("assignment")
    if assignment is not None and (
        visible_lo_emails is None or assignment.assigned_to_email in visible_lo_emails
    ):
        update.update(
            {
                "assigned_to_email": assignment.assigned_to_email,
                "assigned_to_label": assignment.assigned_to_label,
                "assigned_at": assignment.assigned_at,
                "assignment_expires_at": assignment.expires_at,
            }
        )
    disposition = lifecycle.get("latest_disposition")
    if disposition is not None and (
        visible_lo_emails is None or disposition.lo_email in visible_lo_emails
    ):
        update.update(
            {
                "latest_disposition_outcome": disposition.outcome,
                "latest_disposition_at": disposition.occurred_at,
                "latest_callback_at": disposition.callback_at,
            }
        )
    approved_at = update.get("approved_at")
    if hasattr(approved_at, "tzinfo"):
        age = datetime.now(tz=approved_at.tzinfo) - approved_at
        update["aging_days"] = max(0, age.days)
    return borrower.model_copy(update=update)


@router.get("/search", response_model=list[LeadSummary])
def search_borrowers(
    repo: RepoDep,
    q: Annotated[
        str,
        Query(min_length=2, max_length=64, description="Borrower id, ZIP, city, county, state, or masked property ref."),
    ],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> list[LeadSummary]:
    return repo.search(q, limit=limit)


@router.get("/{borrower_id}", response_model=Borrower360)
def get_borrower(
    borrower_id: str,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
    sales_state: SalesStateDep,
    fresh: Annotated[
        bool,
        Query(description="Bypass the process cache while reconciling a gold refresh."),
    ] = False,
) -> Borrower360:
    borrower_id = _path_borrower_id(borrower_id)
    borrower = repo.get_fresh(borrower_id) if fresh else repo.get(borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    actor = resolve_actor(request)
    try:
        borrower = _hydrate_borrower_sales_state(
            borrower,
            sales_state=sales_state,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 -- dossier read should remain available
        emit(
            log,
            "sales_state_hydration_failed",
            dependency="lakebase",
            exc_type=type(exc).__name__,
            outcome="error",
        )
    # Governance §4: record score components + thresholds so we can
    # reconstruct "what the approver saw" after the fact. No PII lands
    # in metadata -- display_name and subject_property stay out.
    background.add_task(
        _safe_audit_write,
        audit,
        actor=actor,
        action="view_borrower_360",
        entity_type="borrower",
        entity_id=borrower.borrower_id,
        payload_json={
            "opportunity_score": borrower.opportunity_score,
            "confidence": borrower.confidence,
            "segment_codes": borrower.segment_codes,
            "recommended_offer": borrower.recommended_offer,
            "decision_inputs": decision_inputs_from_borrower(borrower),
        },
        evidence_ids=list(borrower.evidence_ids),
        event_type="VIEW_BORROWER",
        subject_clip=borrower.clip_id,
    )
    return borrower


@router.get("/{borrower_id}/evidence", response_model=list[EvidenceEvent])
def get_borrower_evidence(borrower_id: str, repo: RepoDep) -> list[EvidenceEvent]:
    borrower_id = _path_borrower_id(borrower_id)
    events = repo.evidence(borrower_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    return events


@router.get("/{borrower_id}/proof", response_model=BorrowerProof)
def get_borrower_proof(
    borrower_id: str,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: StoreDep,
) -> BorrowerProof:
    borrower_id = _path_borrower_id(borrower_id)
    proof = repo.proof(borrower_id)
    if proof is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    actor = resolve_actor(request)
    proof_sql_hash = hash_sql("|".join(query.sql_hash for query in proof.reproduce))
    background.add_task(
        _safe_audit_write,
        audit,
        actor=actor,
        action="view_borrower_proof",
        entity_type="borrower",
        entity_id=proof.borrower_id,
        payload_json={
            "borrower_id": proof.borrower_id,
            "source_assets": proof.source_assets,
            "sql_hash": proof_sql_hash,
            "row_count": 1,
        },
        evidence_ids=[event.evidence_id for event in proof.evidence_rows],
        event_type="VIEW_BORROWER_PROOF",
    )
    return proof
