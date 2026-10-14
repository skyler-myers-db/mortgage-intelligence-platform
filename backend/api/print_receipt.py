"""Audited print: the receipt an Export PDF waits for (audit responsive-08).

``POST /api/v1/print-receipt`` writes exactly one server-owned
``PRINT_EXPORT`` ledger row (services/print_receipt) and answers it; the
page opens the print dialog only after this answer, on the DOM it already
rendered. Nothing is re-read to print: this route makes no warehouse call
and reads no borrower or ledger row.

RBAC follows what is being printed:

* ``borrower_360``: any edge-authenticated actor (the dossier's own reader).
* ``audit_ledger``: an administrator or a configured read-only auditor
  (``require_audit_reader``). Both print only the page already rendered to
  them; the explorer CSV stays admin-only (``/audit/export-receipt``).

Rate limiting: a POST, so the backpressure middleware classes it with the
other Lakebase mutations; no warehouse slot is taken.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.schemas.print_receipt import PrintReceipt, PrintReceiptRequest
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
from backend.services.print_receipt import PrintFingerprintUnavailable, write_print_receipt
from backend.services.rbac import AuthenticatedActorDep, require_audit_reader

router = APIRouter(tags=["print"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]

# Constant body: a refused row never echoes the policy message or a value.
PRINT_RECEIPT_REFUSED_DETAIL = "The print receipt could not be recorded."


@router.post(
    "/print-receipt",
    response_model=PrintReceipt,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def create_print_receipt(
    payload: PrintReceiptRequest,
    request: Request,
    response: Response,
    store: StoreDep,
    _: Annotated[None, Depends(require_json_content_type)],
    actor: AuthenticatedActorDep,
) -> PrintReceipt:
    """Write the ``PRINT_EXPORT`` row the print dialog must wait for."""

    response.headers["Cache-Control"] = "private, no-store"
    if payload.surface == "audit_ledger":
        actor = require_audit_reader(request)
    try:
        return write_print_receipt(store, actor=actor, payload=payload)
    except (AuditPIIError, AuditMetadataViolation, AuditMetadataValueViolation) as exc:
        raise HTTPException(status_code=422, detail=PRINT_RECEIPT_REFUSED_DETAIL) from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    except PrintFingerprintUnavailable as exc:
        # No key to store a keyed fingerprint under: no row, fail closed.
        raise HTTPException(status_code=503, detail=safe_dependency_detail("audit")) from exc
