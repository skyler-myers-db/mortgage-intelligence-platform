"""Audit API -- list + log audit events.

Slice 5 migrates this router off the in-memory store onto the
Lakebase-backed ``AuditStore`` via ``get_audit_store``. The wire shape
is unchanged so the frontend Activity Log keeps working.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.audit import AuditEvent, AuditEventCreateRequest
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.lakebase import LakebaseError

router = APIRouter(prefix="/api/audit", tags=["audit"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]


@router.get("/events", response_model=list[AuditEvent])
def list_events(store: StoreDep, limit: int = 50) -> list[AuditEvent]:
    try:
        return store.list(limit=limit)
    except LakebaseError as exc:
        # No silent fallback. The operator sees 503 and can decide
        # whether to force a redeploy or let Slice 6's resilience
        # kick in once it lands.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/event", response_model=AuditEvent)
def log_event(
    payload: AuditEventCreateRequest,
    request: Request,
    store: StoreDep,
) -> AuditEvent:
    # If the caller passed an actor (legacy path), respect it; otherwise
    # resolve from the X-Forwarded-Email header Databricks Apps plumbs
    # through. ``resolve_actor`` logs a warning on fallback so we can
    # spot non-authenticated calls in production logs.
    actor = payload.actor or resolve_actor(request)
    try:
        return store.write(
            actor=actor,
            action=payload.action,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            payload_json=payload.payload_json,
            evidence_ids=payload.evidence_ids,
            event_type=payload.event_type,
            subject_clip=payload.subject_clip,
            subject_segment=payload.subject_segment,
            request_id=payload.request_id,
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
