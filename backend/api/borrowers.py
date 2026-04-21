from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360
from backend.services.repositories import BorrowerRepository, get_borrower_repository

router = APIRouter(prefix="/api/borrowers", tags=["borrowers"])

RepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]


@router.get("/{borrower_id}", response_model=Borrower360)
def get_borrower(borrower_id: str, repo: RepoDep) -> Borrower360:
    borrower = repo.get(borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    return borrower


@router.get("/{borrower_id}/evidence", response_model=list[EvidenceEvent])
def get_borrower_evidence(borrower_id: str, repo: RepoDep) -> list[EvidenceEvent]:
    events = repo.evidence(borrower_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")
    return events
