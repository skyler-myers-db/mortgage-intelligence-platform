"""Genie feedback route — thumbs up/down with an audited, PII-safe trail.

Split out of ``backend/api/genie.py`` (2026-07-07) to keep that module under
the file-size gate. Behavior is unchanged and pinned by
``tests/unit/test_genie_feedback_api.py``; the route path stays
``POST /api/genie/feedback`` because both routers share the ``/genie`` prefix.

``POST /api/genie/export-receipt`` (audit 2026-09-21 genie-06) lives here too:
the ``GENIE_ANSWER_EXPORT`` ledger row a Genie CSV download waits for, pinned
by ``tests/unit/test_genie_answer_export_receipt.py``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.schemas.common import validate_public_opaque_id
from backend.schemas.genie_export import GenieAnswerExportReceipt, GenieAnswerExportReceiptRequest
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    AuditMetadataViolation,
    AuditPIIError,
    AuditStore,
    get_audit_store,
    resolve_actor,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_answer_export_receipt import (
    GenieExportCountMismatch,
    GenieExportNotFound,
    write_genie_answer_export_receipt,
)
from backend.services.genie_client import ResilientGenieClient, get_genie_client
from backend.services.genie_feedback import (
    GenieFeedbackConflictError,
    GenieFeedbackDeliveryError,
    GenieFeedbackInProgressError,
    record_genie_feedback,
)
from backend.services.genie_session_guard import assert_genie_message_owned
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.rbac import AuthenticatedActorDep

router = APIRouter(prefix="/genie", tags=["genie"])

LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]
GenieClientDep = Annotated[ResilientGenieClient, Depends(get_genie_client)]
StoreDep = Annotated[AuditStore, Depends(get_audit_store)]

#: Constant details: a refusal never says which part of the declaration failed.
GENIE_EXPORT_NOT_FOUND_DETAIL = "Genie answer not found"
GENIE_EXPORT_COUNT_MISMATCH_DETAIL = "export declaration does not match the Genie answer"
GENIE_EXPORT_REFUSED_DETAIL = "the audit ledger refused this export declaration"


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
    """Reject free text so borrower identity data cannot cross into Genie."""
    if comment is None:
        return None
    stripped = comment.strip()
    if not stripped:
        return None
    raise HTTPException(
        status_code=422,
        detail="Free-text feedback is disabled; use the helpful or not-helpful vote.",
    )


def _validated_request_id(request_id: str | None) -> str | None:
    if request_id is None:
        return None
    try:
        return validate_public_opaque_id(request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be a UUID or governed opaque id",
        ) from exc


@router.post(
    "/feedback", response_model=GenieFeedbackResponse, responses=JSON_CONTENT_TYPE_RESPONSE
)
def genie_feedback(
    payload: GenieFeedbackRequest,
    request: Request,
    genie: GenieClientDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieFeedbackResponse:
    """Record thumbs up/down feedback for a Genie answer.

    The exact actor/conversation/message ownership is checked before a durable
    idempotency intent is claimed. The native rating is sent only after that
    transaction commits; final state and the ``GENIE_FEEDBACK`` audit row are
    then committed together. Free-text feedback is rejected so names or other
    borrower details cannot be forwarded to the native Genie comment system.
    """
    actor = resolve_actor(request)
    safe_comment = _validated_feedback_comment(payload.comment)
    # Read manually rather than as a FastAPI Header dependency: this preserves
    # the existing OpenAPI body/response contract while supporting the standard
    # client idempotency header. Legacy clients get a stable derived key.
    safe_request_id = _validated_request_id(request.headers.get("Idempotency-Key"))
    assert_genie_message_owned(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
    )
    try:
        audit_event_id = record_genie_feedback(
            lakebase,
            genie,
            actor=actor,
            request_id=safe_request_id,
            conversation_id=payload.conversation_id,
            message_id=payload.message_id,
            helpful=payload.helpful,
            comment=safe_comment,
        )
    except GenieFeedbackConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for different feedback",
        ) from exc
    except GenieFeedbackInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail="feedback request is already in progress",
        ) from exc
    except GenieFeedbackDeliveryError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("genie"),
        ) from exc
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    return GenieFeedbackResponse(accepted=True, audit_event_id=audit_event_id)


@router.post(
    "/export-receipt",
    response_model=GenieAnswerExportReceipt,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def create_genie_answer_export_receipt(
    payload: GenieAnswerExportReceiptRequest,
    store: StoreDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
    actor: AuthenticatedActorDep,
) -> GenieAnswerExportReceipt:
    """Write the ``GENIE_ANSWER_EXPORT`` ledger row a Genie CSV download waits for.

    Audit 2026-09-21 genie-06: the answer must be a trusted message of the
    caller's own conversation (404 and no write otherwise), the declared row
    counts must describe it (422 and no write otherwise), and then exactly one
    audit row is written. The path uses only existing route segments
    (``genie``, ``export-receipt``).
    """

    try:
        return write_genie_answer_export_receipt(store, lakebase, actor=actor, payload=payload)
    except GenieExportNotFound as exc:
        raise HTTPException(status_code=404, detail=GENIE_EXPORT_NOT_FOUND_DETAIL) from exc
    except GenieExportCountMismatch as exc:
        raise HTTPException(status_code=422, detail=GENIE_EXPORT_COUNT_MISMATCH_DETAIL) from exc
    except (AuditPIIError, AuditMetadataViolation, AuditMetadataValueViolation) as exc:
        raise HTTPException(status_code=422, detail=GENIE_EXPORT_REFUSED_DETAIL) from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
