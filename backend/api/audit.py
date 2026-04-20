from fastapi import APIRouter

router = APIRouter(prefix="/api/audit")


@router.post("/event")
def log_event(payload: dict) -> dict:
    return {"status": "logged", "event_type": payload.get("event_type", "unknown")}
