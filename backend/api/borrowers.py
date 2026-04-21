from fastapi import APIRouter, HTTPException

from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360
from backend.services import mock_data

router = APIRouter(prefix="/api/borrowers", tags=["borrowers"])


def _find(borrower_id: str) -> Borrower360:
    for b in mock_data.BORROWERS:
        if b.borrower_id == borrower_id:
            return b
    raise HTTPException(status_code=404, detail=f"Borrower {borrower_id} not found")


@router.get("/{borrower_id}", response_model=Borrower360)
def get_borrower(borrower_id: str) -> Borrower360:
    return _find(borrower_id)


@router.get("/{borrower_id}/evidence", response_model=list[EvidenceEvent])
def get_borrower_evidence(borrower_id: str) -> list[EvidenceEvent]:
    return _find(borrower_id).evidence_events
