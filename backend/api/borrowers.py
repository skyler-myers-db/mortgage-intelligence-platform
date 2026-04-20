from fastapi import APIRouter

router = APIRouter(prefix="/api/borrowers")


@router.get("/{borrower_id}")
def get_borrower(borrower_id: str) -> dict:
    return {"borrower_id": borrower_id, "name": "Demo Borrower"}


@router.get("/{borrower_id}/evidence")
def get_borrower_evidence(borrower_id: str) -> dict:
    return {"borrower_id": borrower_id, "evidence": []}
