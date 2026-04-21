"""Thin Genie router.

All intent matching and the curated answer catalog live in
``backend.services.genie_answers``. This module stays a thin FastAPI
surface so the API contract is a one-line delegation and
``/ask-genie`` + the floating chat keep a stable wire shape.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services.genie_answers import GenieMessageResponse, respond

router = APIRouter(prefix="/api/genie", tags=["genie"])


class GenieMessageRequest(BaseModel):
    question: str
    conversation_id: str | None = None


@router.post("/start")
def genie_start(payload: dict[str, object] | None = None) -> dict[str, object]:
    _ = payload
    return {
        "conversation_id": "session-conv",
        "trusted_assets": [
            "mip.gold.lead_population",
            "mip.gold.lead_segment_membership",
            "mip.semantics.lead_generation_metric_view",
        ],
    }


@router.post("/message", response_model=GenieMessageResponse)
def genie_message(payload: GenieMessageRequest) -> GenieMessageResponse:
    return respond(payload.question)
