"""Saved workspace API.

Actor-scoped inbox state backed by Lakebase. This replaces the prior
browser-only saved leads/drafts convenience with durable app state and
an audit row for each state-changing action.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.workspace import (
    SavedDraft,
    SavedDraftInput,
    SavedLead,
    SavedLeadInput,
    WorkspaceMutationResponse,
    WorkspaceState,
)
from backend.config.settings import settings
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.workspace_store import WorkspaceStore, get_workspace_store

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

WorkspaceDep = Annotated[WorkspaceStore, Depends(get_workspace_store)]


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


@router.get("", response_model=WorkspaceState)
def read_workspace(request: Request, store: WorkspaceDep) -> WorkspaceState:
    try:
        return store.list(actor=_actor(request))
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc


@router.put("/leads/{borrower_id}", response_model=SavedLead)
def save_lead(
    borrower_id: str,
    payload: SavedLeadInput,
    request: Request,
    store: WorkspaceDep,
) -> SavedLead:
    if payload.borrower_id != borrower_id:
        raise HTTPException(status_code=400, detail="borrower_id path/body mismatch")
    try:
        return store.save_lead(actor=_actor(request), lead=payload)
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc


@router.delete("/leads/{borrower_id}", response_model=WorkspaceMutationResponse)
def delete_lead(
    borrower_id: str,
    request: Request,
    store: WorkspaceDep,
) -> WorkspaceMutationResponse:
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
) -> SavedDraft:
    if payload.borrower_id != borrower_id:
        raise HTTPException(status_code=400, detail="borrower_id path/body mismatch")
    try:
        return store.save_draft(actor=_actor(request), draft=payload)
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc


@router.delete("/drafts/{borrower_id}", response_model=WorkspaceMutationResponse)
def delete_draft(
    borrower_id: str,
    request: Request,
    store: WorkspaceDep,
    channel: Literal["email", "sms"] = "email",
) -> WorkspaceMutationResponse:
    try:
        return store.delete_draft(
            actor=_actor(request),
            borrower_id=borrower_id,
            channel=channel,
        )
    except LakebaseError as exc:
        raise _as_lakebase_503() from exc
