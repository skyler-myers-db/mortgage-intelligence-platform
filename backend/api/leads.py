from fastapi import APIRouter

from backend.schemas.lead import LeadSummary
from backend.services import mock_data

router = APIRouter(prefix="/api", tags=["leads"])


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(segment: str | None = None, portfolio_id: str | None = None) -> list[LeadSummary]:
    _ = portfolio_id
    leads = [LeadSummary(**b.model_dump()) for b in mock_data.BORROWERS]
    if segment:
        leads = [lead for lead in leads if segment in lead.segment_codes]
    return leads
