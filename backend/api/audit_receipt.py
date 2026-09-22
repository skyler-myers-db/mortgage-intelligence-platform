"""Decision receipt -- read one decision row back from the audit ledger.

``GET /audit/receipt/{audit_event_id}`` is the approver's proof that the
approve / reject they just made is in ``mip_app.action_audit``. It is
actor-or-admin scoped: a caller reads only receipts for rows they wrote,
an admin reads any. The body is the closed ``DecisionReceipt`` allowlist
read from the persisted row, never the request that created it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.schemas.audit_receipt import DecisionReceipt
from backend.services.audit_store import AuditStore, get_audit_store
from backend.services.audit_store_receipt import (
    build_decision_receipt,
    is_valid_audit_event_id,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.rbac import can_access_admin, require_authenticated_actor

router = APIRouter(prefix="/audit", tags=["audit"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]


@router.get("/receipt/{audit_event_id}", response_model=DecisionReceipt)
def read_decision_receipt(
    audit_event_id: str,
    request: Request,
    response: Response,
    store: StoreDep,
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
    if event.actor.strip().lower() != actor.strip().lower() and not can_access_admin(request):
        raise HTTPException(status_code=403, detail="forbidden")
    receipt = build_decision_receipt(event)
    if receipt is None:
        raise HTTPException(status_code=404, detail="audit event is not a decision")
    return receipt
