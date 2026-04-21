from fastapi import APIRouter, HTTPException

from backend.schemas.offer import OfferRecommendation, OfferRecommendRequest
from backend.services import mock_data

router = APIRouter(prefix="/api/offers", tags=["offers"])


_OFFER_TYPE = {
    "Refinance + HELOC": "refi",
    "HELOC": "heloc",
    "Purchase Mortgage": "purchase",
    "Cash-out Refi": "cash_out",
}


@router.post("/recommend", response_model=OfferRecommendation)
def recommend_offer(payload: OfferRecommendRequest) -> OfferRecommendation:
    for b in mock_data.BORROWERS:
        if b.borrower_id == payload.borrower_id:
            return OfferRecommendation(
                borrower_id=b.borrower_id,
                offer_code=f"OFFER-{b.borrower_id}",
                offer_type=_OFFER_TYPE.get(b.recommended_offer, "refi"),
                product_label=b.recommended_offer,
                confidence=b.confidence,
                rationale=b.why_now,
                evidence_ids=b.evidence_ids,
            )
    raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
