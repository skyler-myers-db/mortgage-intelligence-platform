from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.lead import LeadSummary
from backend.services.repositories import LeadRepository, get_lead_repository

router = APIRouter(prefix="/api", tags=["leads"])

RepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    repo: RepoDep,
    segment: str | None = None,
    portfolio_id: str | None = None,
) -> list[LeadSummary]:
    return repo.list(segment=segment, portfolio_id=portfolio_id)
