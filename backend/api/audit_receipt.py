"""Decision receipt -- read one decision row back from the audit ledger.

``GET /audit/receipt/{audit_event_id}`` is the approver's proof that the
approve / reject they just made is in ``mip_app.action_audit``. It is
actor, admin or auditor scoped: a caller reads only receipts for rows they
wrote, an administrator or a configured read-only auditor reads any
(D-audit-reads-c3). A served read of ANOTHER actor's row writes one
background, fail-open ``VIEW_AUDIT_LEDGER`` row (surface ``receipt``); an
own receipt stays audit-free. The body is the closed ``DecisionReceipt``
allowlist read from the persisted row, never the request that created it.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from backend.schemas.audit_receipt import DecisionReceipt
from backend.services.audit_ledger_reads import record_ledger_read
from backend.services.audit_store import AuditStore, get_audit_store
from backend.services.audit_store_receipt import (
    build_decision_receipt,
    is_valid_audit_event_id,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.rbac import can_access_admin, can_read_audit, require_authenticated_actor

router = APIRouter(prefix="/audit", tags=["audit"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]


@router.get("/receipt/{audit_event_id}", response_model=DecisionReceipt)
def read_decision_receipt(
    audit_event_id: str,
    request: Request,
    response: Response,
    store: StoreDep,
    background: BackgroundTasks,
) -> DecisionReceipt:
    """Return the receipt for one decision row the caller may see."""

    actor = require_authenticated_actor(request)
    response.headers["Cache-Control"] = "private, no-store"
    if not is_valid_audit_event_id(audit_event_id):
        raise HTTPException(status_code=404, detail="audit event not found")
    try:
        rows = store.list(limit=1, event_id=audit_event_id)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="audit event not found")
    event = rows[0]
    cross_actor = event.actor.strip().lower() != actor.strip().lower()
    if cross_actor and not (can_access_admin(request) or can_read_audit(request)):
        raise HTTPException(status_code=403, detail="forbidden")
    receipt = build_decision_receipt(event)
    if receipt is None:
        raise HTTPException(status_code=404, detail="audit event is not a decision")
    if cross_actor:
        record_ledger_read(
            background,
            store,
            actor=actor,
            surface="receipt",
            filter_fingerprint=None,
            has_cursor=False,
            returned_row_count=1,
            read_audit_event_id=audit_event_id,
        )
    return receipt
