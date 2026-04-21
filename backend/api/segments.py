from fastapi import APIRouter

from backend.schemas.lead import SegmentSummary
from backend.services import mock_data

router = APIRouter(prefix="/api", tags=["segments"])


@router.get("/segments", response_model=list[SegmentSummary])
def list_segments(portfolio_id: str | None = None) -> list[SegmentSummary]:
    # portfolio_id is accepted for forward-compat; mock mode ignores it.
    _ = portfolio_id
    return mock_data.SEGMENTS
