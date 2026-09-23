"""Genie refusal false-positive report route (hash-only).

``POST /api/genie/refusal-report`` records that a lender believes a governed
refusal was a false positive. The body carries the coarse ``refusal_reason``
and the ``refusal_report_hash`` from the refused turn, never the question:
the schema has no text field, the hash is shape-validated, and the ids must
have a shape the server issues (a Genie id, or one of the app's own synthetic
message ids). The route shares the
``/genie`` prefix so the backpressure classifier gives it the same "genie"
budget as ``/api/genie/feedback`` (audit 2026-09-21 ``genie-05``).
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_refusal_reason import (
    GenieRefusalReason,
    is_refusal_report_hash,
)
from backend.services.genie_refusal_report import record_genie_refusal_report
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client

router = APIRouter(prefix="/genie", tags=["genie"])

LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]

# A closed grammar of the ids the server itself issues on a turn the card can
# report. Genie issues 32-hex conversation and message ids (a UUID is accepted
# for forward compatibility). A policy block keeps the blocked turn's own
# message id, and the app's non-live answers carry synthetic ones:
# ``sales-ops-<UUID>`` (genie_sales_ops) and ``trusted-sql-``, ``guide-`` or
# ``data-gap-`` followed by the 16-hex question hash (databricks_genie_direct*).
# Nothing else fits: these ids are the only client-chosen strings that reach
# the report row and the audit metadata, so a free-form token (hyphen-joined
# prompt words, say) must be refused, not stored. lakebase/schema.sql carries
# the same CHECK.
_GENIE_ID_RE = re.compile(
    r"^(?:[0-9a-f]{32}"
    r"|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    r"|sales-ops-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    r"|(?:trusted-sql|guide|data-gap)-[0-9a-f]{16})$",
    re.IGNORECASE,
)


class GenieRefusalReportRequest(BaseModel):
    """Hash-only report body. There is deliberately no field for prompt text."""

    #: Full SHA-256 of the refused question (the audit ledger's exact bytes),
    #: as returned on the refused turn's ``refusal_report_hash``. Shape-checked
    #: in the route so the rejection message is fixed and never reflects the
    #: value.
    question_hash: str = Field(min_length=1, max_length=128)
    refusal_reason: GenieRefusalReason
    conversation_id: str | None = Field(default=None, max_length=128)
    message_id: str | None = Field(default=None, max_length=128)


class GenieRefusalReportResponse(BaseModel):
    accepted: bool
    #: True when this actor already reported the same question hash under the
    #: same family; the first report (and its audit row) stands.
    duplicate: bool
    report_id: str | None = None
    audit_event_id: str | None = None


def _validated_genie_id(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if _GENIE_ID_RE.fullmatch(stripped) is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a server-issued Genie identifier",
        )
    return stripped


@router.post(
    "/refusal-report",
    response_model=GenieRefusalReportResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def genie_refusal_report(
    payload: GenieRefusalReportRequest,
    request: Request,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieRefusalReportResponse:
    """Record a hash-only false-positive report for a governed refusal.

    The actor comes from the edge identity header. The report row and its
    ``GENIE_REFUSAL_REPORT`` audit event commit together
    (``record_genie_refusal_report``); a replay for the same actor, hash and
    family is acknowledged as a duplicate without a second row.
    """
    actor = resolve_actor(request)
    if not is_refusal_report_hash(payload.question_hash):
        raise HTTPException(
            status_code=422,
            detail="question_hash must be the 64-hex refusal_report_hash of the refused turn",
        )
    conversation_id = _validated_genie_id(payload.conversation_id, field_name="conversation_id")
    message_id = _validated_genie_id(payload.message_id, field_name="message_id")
    try:
        record = record_genie_refusal_report(
            lakebase,
            actor=actor,
            question_hash=payload.question_hash,
            refusal_reason=payload.refusal_reason,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    return GenieRefusalReportResponse(
        accepted=record.accepted,
        duplicate=record.duplicate,
        report_id=record.report_id,
        audit_event_id=record.audit_event_id,
    )
