"""Genie refusal reports for administrators and auditors (D-audit-reads-d).

* ``GET /audit/refusal-reports``: a newest-first page of "This was
  legitimate" reports with per-family counts for the window. Report metadata
  only (never question text); every served page writes one background,
  fail-open ``VIEW_AUDIT_LEDGER`` row (surface ``refusal_reports``).
* ``GET /audit/refusal-reports/{report_id}/question``: one consented question
  text. The ``VIEW_REFUSAL_REPORT_TEXT`` audit row is written synchronously
  BEFORE the text is returned; if that write fails, the answer is a 503 with
  no text (fail-closed).

Both are gated by ``AuditReaderDep`` (administrators and configured
auditors) and answer ``Cache-Control: private, no-store``. Each first
runs the bounded expiry sweep as system retention (its own short
transaction, no audit row, fail-open). The SQL lives in
``backend/services/genie_refusal_report_reads``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from backend.services.audit_ledger_reads import record_ledger_read
from backend.services.audit_pagination import audit_filter_fingerprint
from backend.services.audit_store import AuditStore, get_audit_store
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_refusal_reason import GENIE_REFUSAL_AUDIT_CODES, GenieRefusalReason
from backend.services.genie_refusal_report_reads import (
    MAX_CURSOR_LENGTH,
    InvalidRefusalReportCursor,
    read_refusal_question,
    read_refusal_report_page,
    sweep_refusal_texts_fail_open,
)
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.rbac import AuditReaderDep

router = APIRouter(prefix="/audit", tags=["audit"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]

REFUSAL_REPORT_WINDOW = timedelta(days=90)
INVALID_CURSOR = "invalid refusal report cursor"
QUESTION_NOT_FOUND = "refusal question not found"
VIEW_REFUSAL_REPORT_TEXT = "VIEW_REFUSAL_REPORT_TEXT"
_REPORT_ID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)


class RefusalReportItem(BaseModel):
    report_id: str
    reported_at: datetime
    refusal_reason: GenieRefusalReason
    reporter: str = Field(description="The reporting staff member's identity (actor_email).")
    conversation_id: str | None = None
    message_id: str | None = None
    has_text: bool = Field(
        description="A consented question text is held (unpurged and unexpired)."
    )
    text_expires_at: datetime | None = None
    audit_event_id: str | None = None


class RefusalReportFamilyCount(BaseModel):
    refusal_reason: GenieRefusalReason
    count: int = Field(ge=0)


class RefusalReportListResponse(BaseModel):
    items: list[RefusalReportItem]
    family_counts: list[RefusalReportFamilyCount] = Field(
        description="Reports per refusal family for the whole window (every family filter)."
    )
    next_cursor: str | None = None


class RefusalReportQuestionResponse(BaseModel):
    question_text: str
    redacted: bool = Field(description="Contact details were masked before the question was kept.")
    captured_at: datetime
    expires_at: datetime


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _item(row: dict[str, Any]) -> RefusalReportItem:
    return RefusalReportItem(
        report_id=str(row["report_id"]),
        reported_at=row["reported_at"],
        refusal_reason=row["refusal_reason"],
        reporter=str(row["actor_email"]),
        conversation_id=row.get("conversation_id"),
        message_id=row.get("message_id"),
        has_text=bool(row.get("has_text")),
        text_expires_at=row.get("text_expires_at"),
        audit_event_id=str(row["audit_event_id"]) if row.get("audit_event_id") else None,
    )


@router.get("/refusal-reports", response_model=RefusalReportListResponse)
def list_refusal_reports(
    actor: AuditReaderDep,
    lakebase: LakebaseDep,
    store: StoreDep,
    background: BackgroundTasks,
    response: Response,
    since: datetime | None = None,
    family: GenieRefusalReason | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
) -> RefusalReportListResponse:
    _no_store(response)
    sweep_refusal_texts_fail_open(lakebase)
    fingerprint = audit_filter_fingerprint({"since": since, "family": family, "limit": limit})
    try:
        page = read_refusal_report_page(
            lakebase,
            since=since or datetime.now(UTC) - REFUSAL_REPORT_WINDOW,
            family=family,
            limit=limit,
            cursor=cursor,
            filter_fingerprint=fingerprint,
        )
    except InvalidRefusalReportCursor as exc:
        raise HTTPException(status_code=422, detail=INVALID_CURSOR) from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    items = [_item(row) for row in page.rows]
    record_ledger_read(
        background,
        store,
        actor=actor,
        surface="refusal_reports",
        filter_fingerprint=fingerprint,
        has_cursor=cursor is not None,
        returned_row_count=len(items),
    )
    return RefusalReportListResponse(
        items=items,
        family_counts=[
            RefusalReportFamilyCount(
                refusal_reason=row["refusal_reason"], count=int(row["report_count"])
            )
            for row in page.family_counts
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/refusal-reports/{report_id}/question",
    response_model=RefusalReportQuestionResponse,
)
def read_refusal_report_question(
    report_id: str,
    actor: AuditReaderDep,
    lakebase: LakebaseDep,
    store: StoreDep,
    background: BackgroundTasks,
    response: Response,
) -> RefusalReportQuestionResponse:
    _no_store(response)
    if _REPORT_ID_RE.fullmatch(report_id) is None:
        raise HTTPException(status_code=404, detail=QUESTION_NOT_FOUND)
    sweep_refusal_texts_fail_open(lakebase)
    try:
        row = read_refusal_question(lakebase, report_id)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=QUESTION_NOT_FOUND)
    payload: dict[str, Any] = {"question_hash": str(row["question_hash"])[:16]}
    audit_code = GENIE_REFUSAL_AUDIT_CODES.get(str(row["refusal_reason"]))
    if audit_code is not None:
        payload["refusal_reason"] = audit_code
    try:
        # Fail-closed and synchronous: the read is recorded before the text
        # leaves, or the text does not leave.
        store.write(
            actor=actor,
            action="view_refusal_report_text",
            entity_type="genie_refusal_report",
            entity_id=report_id.lower(),
            payload_json=payload,
            event_type=VIEW_REFUSAL_REPORT_TEXT,
        )
    except Exception as exc:  # noqa: BLE001 - any failed write withholds the text
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return RefusalReportQuestionResponse(
        question_text=str(row["question_text"]),
        redacted=bool(row["redacted"]),
        captured_at=row["captured_at"],
        expires_at=row["expires_at"],
    )
