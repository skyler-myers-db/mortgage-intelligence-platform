from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
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
    # Mock mode: criteria don't shift the preview numbers yet; deterministic payload.
    return repo.preview(payload)


@router.post("/create", response_model=PortfolioCreateResponse)
def create_portfolio(payload: PortfolioCreateRequest, repo: RepoDep) -> PortfolioCreateResponse:
    return repo.create(payload)


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: str, repo: RepoDep) -> dict[str, object]:
    return repo.get(portfolio_id)
