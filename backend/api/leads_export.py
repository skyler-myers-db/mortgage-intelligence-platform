"""Audited Lead Queue CSV export: the receipt the download waits for.

``POST /api/v1/leads/export-receipt`` is the approver/actor path that
``POST /api/audit/event`` deliberately is not: that route is admin-only and
refuses server-owned event types, so a client cannot self-report an export.
This route takes any edge-authenticated actor (401 without one), verifies the
client's declaration against the borrower-id list it sends, and writes exactly
one ``LEAD_EXPORT`` row through the governed audit store before answering.
The client blocks the download until this answer arrives.

Rate limiting: every POST is classified as a ``mutation`` by the backpressure
middleware (``BackpressureController.classify``), so this write shares the
120/min budget with approvals and assignments; ``tests/unit/
test_leads_export_receipt.py`` pins that classification.

Not here, by owner decision recorded in the register (tables-08 step 2): a
server-streamed full-cohort export. The bytes stay client-side, drawn from
the ``/api/leads`` payload the operator already saw.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.lead_export import LeadExportReceipt, LeadExportReceiptRequest
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    AuditMetadataViolation,
    AuditPIIError,
    AuditStore,
    get_audit_store,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseError
from backend.services.lead_export_receipt import (
    LeadExportDigestMismatch,
    write_lead_export_receipt,
)
from backend.services.rbac import AuthenticatedActorDep

router = APIRouter(tags=["leads"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]

EXPORT_DIGEST_MISMATCH_DETAIL = "export declaration does not match the borrower id list"


@router.post(
    "/leads/export-receipt",
    response_model=LeadExportReceipt,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def create_lead_export_receipt(
    payload: LeadExportReceiptRequest,
    store: StoreDep,
    _: Annotated[None, Depends(require_json_content_type)],
    actor: AuthenticatedActorDep,
) -> LeadExportReceipt:
    """Write the ``LEAD_EXPORT`` ledger row a CSV download must wait for.

    The request correlation id is recorded by the audit store itself; no
    body or header value is copied into the row's identifier columns.
    """

    try:
        return write_lead_export_receipt(store, actor=actor, payload=payload)
    except LeadExportDigestMismatch as exc:
        # Constant body: the mismatch reason names which field disagreed in
        # the exception, but the client only needs to know the receipt was
        # refused and must not download.
        raise HTTPException(status_code=422, detail=EXPORT_DIGEST_MISMATCH_DETAIL) from exc
    except (AuditPIIError, AuditMetadataViolation, AuditMetadataValueViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
