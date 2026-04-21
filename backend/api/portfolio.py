from fastapi import APIRouter

from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services import mock_data

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.post("/preview", response_model=PortfolioPreview)
def preview_portfolio(_: PortfolioPreviewRequest | None = None) -> PortfolioPreview:
    # Mock mode: criteria don't shift the preview numbers yet; deterministic payload.
    return mock_data.PORTFOLIO


@router.post("/create", response_model=PortfolioCreateResponse)
def create_portfolio(payload: PortfolioCreateRequest) -> PortfolioCreateResponse:
    return PortfolioCreateResponse(
        portfolio_id="demo-portfolio",
        name=payload.name,
        marketable_population=mock_data.PORTFOLIO.marketable_population,
    )


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: str) -> dict[str, object]:
    return {
        "portfolio_id": portfolio_id,
        "status": "ready",
        "marketable_population": mock_data.PORTFOLIO.marketable_population,
    }
