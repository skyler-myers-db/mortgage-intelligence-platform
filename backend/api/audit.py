from fastapi import APIRouter

from backend.schemas.audit import AuditEvent, AuditEventCreateRequest
from backend.services.audit_store import audit_store

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/events", response_model=list[AuditEvent])
def list_events(limit: int = 50) -> list[AuditEvent]:
    return audit_store.list(limit=limit)


@router.post("/event", response_model=AuditEvent)
def log_event(payload: AuditEventCreateRequest) -> AuditEvent:
    return audit_store.write(
        actor=payload.actor,
        action=payload.action,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        payload_json=payload.payload_json,
        evidence_ids=payload.evidence_ids,
    )
