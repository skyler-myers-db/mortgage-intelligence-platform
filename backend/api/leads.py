from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/leads")
def get_leads(portfolio_id: str, segment: str) -> dict:
    return {"portfolio_id": portfolio_id, "segment": segment, "leads": []}
