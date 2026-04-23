"""Thin Genie router.

Slice-7 posture: `/api/genie/message` delegates to
``DatabricksGenieRepository`` which wraps ``ResilientGenieClient`` with
a safe-corpus fallback gated on the ``genie`` circuit breaker. Happy
path always queries the live Databricks Genie space; the curated catalog
in ``backend.services.genie_answers`` is consulted only when the
breaker is OPEN or the live call raised a ``DependencyDownError``.

Prior to the 2026-04-22 real-data walkthrough this router called
``backend.services.genie_answers.respond(question)`` directly --
shortcutting the live Genie path entirely and returning the hardcoded
canonical corpus (e.g. "12,840 borrowers" for any in-the-money
question, even though the live gold table carries 147,742). That was
a silent-mock-fallback regression and has been corrected here.
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import GenieMessageResponse
from backend.services.repositories import GenieAnswerRepository
from backend.services.repositories.factory import get_genie_answer_repository

router = APIRouter(prefix="/api/genie", tags=["genie"])

# Annotated[...] variant of Depends so ruff's B008 stays quiet (Depends
# is not a default *value*; it's FastAPI's dependency marker).
RepoDep = Annotated[GenieAnswerRepository, Depends(get_genie_answer_repository)]


class GenieMessageRequest(BaseModel):
    question: str
    conversation_id: str | None = None


@router.post("/start")
def genie_start(payload: dict[str, object] | None = None) -> dict[str, object]:
    _ = payload
    return {
        "conversation_id": "session-conv",
        "trusted_assets": [
            qualify("gold", "lead_population"),
            qualify("gold", "lead_segment_membership"),
            qualify("semantics", "lead_generation_metric_view"),
        ],
    }


@router.post("/message", response_model=GenieMessageResponse)
def genie_message(payload: GenieMessageRequest, repo: RepoDep) -> GenieMessageResponse:
    # repo.respond() returns a GenieMessageResponse by contract; the
    # protocol annotates `object` only to dodge a forward-import cycle.
    result = repo.respond(payload.question)
    return result  # type: ignore[return-value]
