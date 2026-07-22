"""Campaign list/status aliases.

Portfolio Builder historically created campaigns through
``/api/portfolio/create``. Marketing workflows naturally look for
``/api/campaigns``; keep both surfaces backed by the same repository
contract so saved campaign state is retrievable and auditable.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.campaign_status import authorize_campaign_status_transition
from backend.schemas.common import validate_public_opaque_id
from backend.schemas.portfolio import (
    CampaignListResponse,
    CampaignStatusPatchRequest,
    CampaignSummary,
)
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseError
from backend.services.rbac import require_admin, require_approver
from backend.services.repositories import PortfolioRepository, get_portfolio_repository

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
RepoDep = Annotated[PortfolioRepository, Depends(get_portfolio_repository)]


def _is_admin(request: Request) -> bool:
    try:
        require_admin(request)
        return True
    except HTTPException:
        return False


def _assert_campaign_visible(result: dict[str, object], *, actor: str, is_admin: bool) -> None:
    owner = str(result.get("owner_email") or "").lower()
    if not is_admin and owner != actor.lower():
        raise HTTPException(status_code=404, detail="campaign not found")


@router.get("", response_model=CampaignListResponse)
def list_campaigns(
    request: Request,
    repo: RepoDep,
    owner_email: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> CampaignListResponse:
    try:
        actor = resolve_actor(request)
        is_admin = _is_admin(request)
        if owner_email and owner_email.lower() != actor.lower() and not is_admin:
            raise HTTPException(status_code=403, detail="forbidden")
        return repo.list_campaigns(
            owner_email=owner_email or actor,
            status=status,
            limit=limit,
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc


@router.get("/{campaign_id}", response_model=CampaignSummary)
def get_campaign(campaign_id: str, request: Request, repo: RepoDep) -> CampaignSummary:
    try:
        validate_public_opaque_id(campaign_id)
        result = repo.get(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="campaign_id is invalid") from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if not result:
        raise HTTPException(status_code=404, detail="campaign not found")
    _assert_campaign_visible(result, actor=resolve_actor(request), is_admin=_is_admin(request))
    return CampaignSummary(**result)


@router.patch(
    "/{campaign_id}",
    response_model=CampaignSummary,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def patch_campaign(
    campaign_id: str,
    payload: CampaignStatusPatchRequest,
    request: Request,
    repo: RepoDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> CampaignSummary:
    try:
        validate_public_opaque_id(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="campaign_id is invalid") from exc
    try:
        result = repo.get(campaign_id)
        if not result:
            raise HTTPException(status_code=404, detail="campaign not found")
        actor = resolve_actor(request)
        _assert_campaign_visible(result, actor=actor, is_admin=_is_admin(request))
        if (
            payload.status == "archived"
            and str(result.get("treatment_state") or "") == "building"
        ):
            actor = require_admin(request)
        if payload.status in {"approved", "live", "active"}:
            approver = require_approver(request)
            payload = authorize_campaign_status_transition(
                payload,
                campaign_id=campaign_id,
                current_status=str(result.get("status") or ""),
                approver_email=approver,
            )
            actor = approver
        return repo.patch_status(campaign_id, payload, actor=actor)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LakebaseError as exc:
        if "returned no row" in str(exc):
            raise HTTPException(status_code=404, detail="campaign not found") from exc
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
