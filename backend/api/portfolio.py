from fastapi import APIRouter

router = APIRouter(prefix="/api/portfolio")


@router.post("/preview")
def preview_portfolio(payload: dict) -> dict:
    criteria = payload.get("criteria", {})
    return {"portfolio_size_estimate": 42, "criteria": criteria}


@router.post("/create")
def create_portfolio(payload: dict) -> dict:
    return {"portfolio_id": "demo-portfolio", "name": payload.get("name", "Demo Portfolio")}


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: str) -> dict:
    return {"portfolio_id": portfolio_id, "status": "ready"}
