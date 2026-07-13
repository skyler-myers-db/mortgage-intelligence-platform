"""Portfolio preview, save, list, and status endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.common import validate_public_opaque_id
from backend.schemas.portfolio import (
    CampaignListResponse,
    CampaignRecommendationRequest,
    CampaignRecommendationResponse,
    CampaignStatusPatchRequest,
    CampaignSummary,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.audit_store import resolve_actor
from backend.services.campaign_intelligence import CampaignPerformanceContext, recommend_campaign
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseError
from backend.services.rbac import require_admin
from backend.services.repositories import PortfolioRepository, get_portfolio_repository
from backend.services.sales_state import SalesStateStore, get_sales_state_store

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# The Annotated form is the FastAPI-recommended DI pattern and keeps
# ruff's B008 quiet (Depends(...) is not a *value* default; it's the
# dependency marker resolved at request time).
RepoDep = Annotated[PortfolioRepository, Depends(get_portfolio_repository)]
SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]


def _is_admin(request: Request) -> bool:
    try:
        require_admin(request)
        return True
    except HTTPException:
        return False


def _assert_portfolio_visible(result: dict[str, object], *, actor: str, is_admin: bool) -> None:
    owner = str(result.get("owner_email") or "").lower()
    if not is_admin and owner != actor.lower():
        raise HTTPException(status_code=404, detail="portfolio not found")


@router.post("/preview", response_model=PortfolioPreview, responses=JSON_CONTENT_TYPE_RESPONSE)
def preview_portfolio(
    repo: RepoDep,
    _: Annotated[None, Depends(require_json_content_type)],
    payload: PortfolioPreviewRequest | None = None,
) -> PortfolioPreview:
    # Portfolio preview is a deterministic projection of the requested
    # criteria against the live gold-layer counts in Unity Catalog; the
    # repository contract returns a stable payload for a stable request.
    return repo.preview(payload)


@router.post(
    "/campaign-recommendation",
    response_model=CampaignRecommendationResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def campaign_recommendation(
    request: Request,
    repo: RepoDep,
    sales_state: SalesStateDep,
    payload: CampaignRecommendationRequest,
    _: Annotated[None, Depends(require_json_content_type)],
) -> CampaignRecommendationResponse:
    """Generate a validated strategy over the exact governed cohort.

    The Supervisor receives aggregate facts only. Metrics, citations, holdout
    bounds, and the approval boundary are enforced by the server response
    contract; an unavailable or invalid model response is labelled as a
    reviewed fallback rather than masquerading as AI output.
    """

    # AUDIT EXEMPT: read-only aggregate recommendation; no state is written.
    preview = repo.preview(PortfolioPreviewRequest(criteria=payload.criteria))
    performance: CampaignPerformanceContext | None = None
    try:
        actor = resolve_actor(request)
        sales_state.require_manager_actor(actor)
        visible_los = sales_state.visible_lo_emails(actor=actor)
        end_date = datetime.now(UTC).date()
        start_date = end_date - timedelta(days=89)
        conversion = sales_state.conversion(
            from_date=start_date.isoformat(),
            to_date=end_date.isoformat(),
            group_by="cohort",
            visible_lo_emails=visible_los,
        )
        outcomes = sales_state.outcome_summary(
            from_date=start_date.isoformat(),
            to_date=end_date.isoformat(),
            visible_lo_emails=visible_los,
        )
        performance = CampaignPerformanceContext(
            unique_contacts_reached=sum(
                int(row.get("unique_contacts_reached") or 0) for row in conversion
            ),
            unique_application_starts=sum(
                int(row.get("unique_application_starts") or 0) for row in conversion
            ),
            unique_applications_submitted=int(
                outcomes.get("unique_applications_submitted") or 0
            ),
            unique_closed_funded=int(outcomes.get("unique_closed_funded") or 0),
        )
    except (KeyError, PermissionError, LakebaseError):
        # Campaign recommendations remain available from exact UC cohort facts
        # when the actor lacks Sales Ops scope or Lakebase is unavailable.
        performance = None
    return recommend_campaign(preview, performance=performance)


@router.post("/create", response_model=PortfolioCreateResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def create_portfolio(
    request: Request,
    payload: PortfolioCreateRequest,
    repo: RepoDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> PortfolioCreateResponse:
    try:
        return repo.create(payload, actor=resolve_actor(request))
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


@router.get("", response_model=CampaignListResponse)
def list_portfolios(
    request: Request,
    repo: RepoDep,
    owner_email: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> CampaignListResponse:
    """Return fresh Lakebase campaign rows for the current actor.

    This route is intentionally not the hot KPI/cache path. The
    cacheable aggregate is ``POST /api/portfolio/preview``; the list
    view is mutation-adjacent campaign state and should reflect recent
    creates/status changes immediately.
    """
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
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


@router.get("/{portfolio_id}", response_model=CampaignSummary)
def get_portfolio(portfolio_id: str, request: Request, repo: RepoDep) -> dict[str, object]:
    try:
        validate_public_opaque_id(portfolio_id)
        result = repo.get(portfolio_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="portfolio_id is invalid") from exc
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if not result:
        raise HTTPException(status_code=404, detail="portfolio not found")
    _assert_portfolio_visible(result, actor=resolve_actor(request), is_admin=_is_admin(request))
    return result


@router.patch("/{portfolio_id}", response_model=CampaignSummary)
def patch_portfolio(
    portfolio_id: str,
    payload: CampaignStatusPatchRequest,
    request: Request,
    repo: RepoDep,
) -> CampaignSummary:
    try:
        validate_public_opaque_id(portfolio_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="portfolio_id is invalid") from exc
    try:
        result = repo.get(portfolio_id)
        if not result:
            raise HTTPException(status_code=404, detail="portfolio not found")
        _assert_portfolio_visible(result, actor=resolve_actor(request), is_admin=_is_admin(request))
        return repo.patch_status(portfolio_id, payload, actor=resolve_actor(request))
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LakebaseError as exc:
        if "returned no row" in str(exc):
            raise HTTPException(status_code=404, detail="portfolio not found") from exc
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
