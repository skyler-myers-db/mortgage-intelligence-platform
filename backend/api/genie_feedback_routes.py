"""Genie feedback route — thumbs up/down with an audited, PII-safe trail.

Split out of ``backend/api/genie.py`` (2026-07-07) to keep that module under
the file-size gate. Behavior is unchanged and pinned by
``tests/unit/test_genie_feedback_api.py``; the route path stays
``POST /api/genie/feedback`` because both routers share the ``/genie`` prefix.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.schemas.common import validate_public_free_comment
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_client import ResilientGenieClient, get_genie_client
from backend.services.genie_feedback import record_genie_feedback
from backend.services.genie_session_guard import assert_genie_conversation_owned
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client

router = APIRouter(prefix="/genie", tags=["genie"])

LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]
GenieClientDep = Annotated[ResilientGenieClient, Depends(get_genie_client)]


class GenieFeedbackRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)
    helpful: bool
    # NOTE: no Pydantic length/format validation on ``comment``. Pydantic
    # validation errors surface as a 422 whose ``input`` field echoes the raw
    # value -- unacceptable for a field that may contain PII. The comment is
    # validated in the route via ``validate_public_free_comment`` which raises
    # an HTTPException with a fixed, non-echoing message.
    comment: str | None = None


class GenieFeedbackResponse(BaseModel):
    accepted: bool
    audit_event_id: str


def _validated_feedback_comment(comment: str | None) -> str | None:
    """Return a public-safe comment or raise 422 without echoing the input."""
    if comment is None:
        return None
    stripped = comment.strip()
    if not stripped:
        return None
    try:
        return validate_public_free_comment(stripped, max_len=280)
    except ValueError as exc:
        # Do NOT include the offending text: reflecting it would echo PII.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/feedback", response_model=GenieFeedbackResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def genie_feedback(
    payload: GenieFeedbackRequest,
    request: Request,
    genie: GenieClientDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieFeedbackResponse:
    """Record thumbs up/down feedback for a Genie answer.

    Guards: JSON content type, the genie rate-limit lane (applied by the
    backpressure middleware for ``/api/genie/*``), and a conversation
    ownership check. Side effects: a best-effort Genie message comment (a
    failure here does not fail the request) and a required ``GENIE_FEEDBACK``
    audit row written in a Lakebase transaction. The caller comment is
    validated public-safe (422 on PII) and never stored verbatim.
    """
    actor = resolve_actor(request)
    safe_comment = _validated_feedback_comment(payload.comment)
    assert_genie_conversation_owned(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
    )
    try:
        audit_event_id = record_genie_feedback(
            lakebase,
            genie,
            actor=actor,
            conversation_id=payload.conversation_id,
            message_id=payload.message_id,
            helpful=payload.helpful,
            comment=safe_comment,
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    return GenieFeedbackResponse(accepted=True, audit_event_id=audit_event_id)
