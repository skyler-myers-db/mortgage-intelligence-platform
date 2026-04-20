from fastapi import APIRouter

router = APIRouter(prefix="/api/offers")


@router.post("/recommend")
def recommend_offer(payload: dict) -> dict:
    return {"borrower_id": payload.get("borrower_id"), "recommended_offer": "cash_out_refi"}
