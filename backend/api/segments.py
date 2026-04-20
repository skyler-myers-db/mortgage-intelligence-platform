from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/segments")
def get_segments(portfolio_id: str) -> dict:
    return {"portfolio_id": portfolio_id, "segments": ["equity_rich", "rate_sensitive", "investor"]}
