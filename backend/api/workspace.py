"""Saved workspace API.

Actor-scoped inbox state backed by Lakebase. This replaces the prior
browser-only saved leads/drafts convenience with durable app state and
an audit row for each state-changing action.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.config.settings import settings
from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead import LeadSummary
from backend.schemas.workspace import (
    SavedDraft,
    SavedDraftInput,
    SavedLead,
    SavedLeadInput,
    WorkspaceMutationResponse,
    WorkspaceState,
)
from backend.services.audit_store import resolve_actor
from backend.services.disclosures import MissingTenantDisclosureError, resolve_tenant_disclosure
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.repositories import (
    LeadRepository,
    OutreachRepository,
    get_lead_repository,
    get_outreach_repository,
)
from backend.services.workspace_store import WorkspaceStore, get_workspace_store

router = APIRouter(prefix="/workspace", tags=["workspace"])

WorkspaceDep = Annotated[WorkspaceStore, Depends(get_workspace_store)]
LeadRepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
OutreachRepoDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def _actor(request: Request) -> str:
    if settings.trust_forwarded_headers:
        email = request.headers.get("X-Forwarded-Email")
        if email:
            return email
        user = request.headers.get("X-Forwarded-User")
        if user:
            return user
        raise HTTPException(
            status_code=401,
            detail="workspace identity required",
        )
    return resolve_actor(request)


def _as_lakebase_503() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=safe_dependency_detail("lakebase"),
    )


def _path_borrower_id(value: str) -> str:
    try:
        return validate_public_borrower_id(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="borrower_id must be an app-scoped public borrower id",
        ) from exc


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _assert_workspace_marketing_eligible(borrower: Any) -> None:
    consent_status = str(getattr(borrower, "consent_status", "unknown") or "unknown")
    if consent_status != "opt_in":
        raise HTTPException(
            status_code=422,
            detail=f"borrower is not marketing-eligible: consent_status={consent_status}",
        )
    now = datetime.now(UTC)
    recontact_at = _coerce_datetime(getattr(borrower, "eligible_recontact_at", None))
    if recontact_at and recontact_at > now:
        raise HTTPException(
            status_code=409,
            detail=f"borrower hit frequency cap; earliest re-contact {recontact_at.date().isoformat()}",
        )
    last_touch_at = _coerce_datetime(getattr(borrower, "last_touch_at", None))
    if last_touch_at and last_touch_at > now - timedelta(days=30):
        earliest = last_touch_at + timedelta(days=30)
        raise HTTPException(
            status_code=409,
            detail=f"borrower hit frequency cap; earliest re-contact {earliest.date().isoformat()}",
        )
    if getattr(borrower, "marketing_eligible", False) is not True:
        reason = getattr(borrower, "suppression_reason", None) or "eligibility_not_proven"
        raise HTTPException(status_code=422, detail=f"borrower is not marketing-eligible: {reason}")


def _saved_lead_from_live(lead: LeadSummary, *, saved_at: str, updated_at: str) -> SavedLead:
    return SavedLead(
        borrower_id=lead.borrower_id,
        city=lead.city,
        state=lead.state,
        zip=lead.zip,
        recommended_offer=lead.recommended_offer,
        opportunity_score=lead.opportunity_score,
        confidence=lead.confidence,
        saved_at=saved_at,
        updated_at=updated_at,
    )


def _lead_input_from_live(lead: LeadSummary) -> SavedLeadInput:
    return SavedLeadInput(
        borrower_id=lead.borrower_id,
        city=lead.city,
        state=lead.state,
        zip=lead.zip,
        recommended_offer=lead.recommended_offer,
        opportunity_score=lead.opportunity_score,
        confidence=lead.confidence,
    )


def _hydrate_saved_leads(state: WorkspaceState, repo: LeadRepository) -> WorkspaceState:
    """Replace Lakebase/client metadata with current gold lead facts.

    Lakebase is the durable inbox index, not the source of borrower facts.
    Saved rows whose borrower_id no longer resolves are omitted from the
    active workspace response so the UI never renders stale/null borrower
    details as if they were live.
    """
    if not state.saved_leads:
        return state
    ids = [lead.borrower_id for lead in state.saved_leads]
    live_rows = repo.list(
        segment=None,
        portfolio_id=None,
        borrower_ids=ids,
        limit=len(ids),
    )
    live_by_id = {lead.borrower_id: lead for lead in live_rows}
    hydrated: list[SavedLead] = []
    for saved in state.saved_leads:
        live = live_by_id.get(saved.borrower_id)
        if live is None:
            continue
        hydrated.append(
            _saved_lead_from_live(
                live,
                saved_at=saved.saved_at,
                updated_at=saved.updated_at,
            )
        )
    return state.model_copy(update={"saved_leads": hydrated})


@router.get("", response_model=WorkspaceState)
def read_workspace(
    request: Request,
    store: WorkspaceDep,
    repo: LeadRepoDep,
) -> WorkspaceState:
    try:
        state = store.list(actor=_actor(request))
        return _hydrate_saved_leads(state, repo)
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc


@router.put("/leads/{borrower_id}", response_model=SavedLead)
def save_lead(
    borrower_id: str,
    payload: SavedLeadInput,
    request: Request,
    store: WorkspaceDep,
    repo: LeadRepoDep,
) -> SavedLead:
    borrower_id = _path_borrower_id(borrower_id)
    if payload.borrower_id != borrower_id:
        raise HTTPException(status_code=400, detail="borrower_id path/body mismatch")
    live_rows = repo.list(
        segment=None,
        portfolio_id=None,
        borrower_ids=[borrower_id],
        limit=1,
    )
    if not live_rows:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    live_input = _lead_input_from_live(live_rows[0])
    try:
        return store.save_lead(actor=_actor(request), lead=live_input)
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc


@router.delete("/leads/{borrower_id}", response_model=WorkspaceMutationResponse)
def delete_lead(
    borrower_id: str,
    request: Request,
    store: WorkspaceDep,
) -> WorkspaceMutationResponse:
    borrower_id = _path_borrower_id(borrower_id)
    try:
        return store.delete_lead(actor=_actor(request), borrower_id=borrower_id)
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc


@router.put("/drafts/{borrower_id}", response_model=SavedDraft)
def save_draft(
    borrower_id: str,
    payload: SavedDraftInput,
    request: Request,
    store: WorkspaceDep,
    repo: OutreachRepoDep,
    lakebase: LakebaseDep,
) -> SavedDraft:
    borrower_id = _path_borrower_id(borrower_id)
    if payload.borrower_id != borrower_id:
        raise HTTPException(status_code=400, detail="borrower_id path/body mismatch")
    borrower = repo.find_borrower(borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    _assert_workspace_marketing_eligible(borrower)
    try:
        resolve_tenant_disclosure(
            lakebase,
            state=str(getattr(borrower, "state", "") or ""),
            channel=payload.channel,
        )
    except MissingTenantDisclosureError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc
    try:
        return store.save_draft(actor=_actor(request), draft=payload)
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc


@router.delete("/drafts/{borrower_id}", response_model=WorkspaceMutationResponse)
def delete_draft(
    borrower_id: str,
    request: Request,
    store: WorkspaceDep,
    channel: Literal["email", "sms", "direct_mail"] = "email",
) -> WorkspaceMutationResponse:
    borrower_id = _path_borrower_id(borrower_id)
    try:
        return store.delete_draft(
            actor=_actor(request),
            borrower_id=borrower_id,
            channel=channel,
        )
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc
