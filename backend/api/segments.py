from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.lead import SegmentSummary
from backend.services.repositories import SegmentRepository, get_segment_repository

router = APIRouter(prefix="/api", tags=["segments"])

RepoDep = Annotated[SegmentRepository, Depends(get_segment_repository)]


@router.get("/segments", response_model=list[SegmentSummary])
def list_segments(repo: RepoDep, portfolio_id: str | None = None) -> list[SegmentSummary]:
    return repo.list(portfolio_id=portfolio_id)
