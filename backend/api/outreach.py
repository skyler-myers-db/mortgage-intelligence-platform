from fastapi import APIRouter

router = APIRouter(prefix="/api/outreach")


@router.post("/draft")
def draft_outreach(payload: dict) -> dict:
    return {"channel": payload.get("channel", "email"), "draft": "Hello from MIP."}


@router.post("/approve")
def approve_outreach(payload: dict) -> dict:
    return {"message_id": payload.get("message_id", "demo-message"), "status": "approved"}
