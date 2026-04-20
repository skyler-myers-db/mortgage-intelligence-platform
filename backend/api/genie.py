from fastapi import APIRouter

router = APIRouter(prefix="/api/genie")


@router.post("/start")
def start_genie(payload: dict) -> dict:
    return {"conversation_id": "demo-conversation", "context": payload.get("context", {})}


@router.post("/message")
def genie_message(payload: dict) -> dict:
    return {"conversation_id": payload.get("conversation_id", "demo-conversation"), "answer": "Demo genie response"}
