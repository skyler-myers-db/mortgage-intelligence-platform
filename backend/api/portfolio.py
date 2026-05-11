from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.repositories import PortfolioRepository, get_portfolio_repository

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# The Annotated form is the FastAPI-recommended DI pattern and keeps
# ruff's B008 quiet (Depends(...) is not a *value* default; it's the
# dependency marker resolved at request time).
RepoDep = Annotated[PortfolioRepository, Depends(get_portfolio_repository)]


@router.post("/preview", response_model=PortfolioPreview)
def preview_portfolio(
    repo: RepoDep,
    payload: PortfolioPreviewRequest | None = None,
) -> PortfolioPreview:
    # Portfolio preview is a deterministic projection of the requested
    # criteria against the live gold-layer counts in Unity Catalog; the
    # repository contract returns a stable payload for a stable request.
    return repo.preview(payload)


@router.post("/create", response_model=PortfolioCreateResponse)
def create_portfolio(
    request: Request,
    payload: PortfolioCreateRequest,
    repo: RepoDep,
) -> PortfolioCreateResponse:
    try:
        return repo.create(payload, actor=resolve_actor(request))
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: str, repo: RepoDep) -> dict[str, object]:
    return repo.get(portfolio_id)
