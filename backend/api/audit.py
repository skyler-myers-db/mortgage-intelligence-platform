"""Audit API -- list + log audit events.

Slice 5 migrates this router off the in-memory store onto the
Lakebase-backed ``AuditStore`` via ``get_audit_store``. The wire shape
is unchanged so the frontend Activity Log keeps working.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.audit import AuditEvent, AuditEventCreateRequest
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError

router = APIRouter(prefix="/api/audit", tags=["audit"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]

# R5-14: clamp the caller-supplied ``limit`` for GET /events. The
# ``mip_app.action_audit`` ledger grows unbounded; a pathological caller
# passing ``?limit=999999999`` would force a full scan + page every row
# over the wire. 500 mirrors the ``DatabricksLeadRepository.MAX_LIMIT``
# pattern and sits well above the activity-log drawer's visible window
# (~50) so operator deep-dives still have headroom.
DEFAULT_AUDIT_LIMIT: int = 50
MAX_AUDIT_LIMIT: int = 500
_ROUTER_OWNED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "APPROVE",
        "DRAFT_OUTREACH",
        "OUTREACH_APPROVE",
        "OUTREACH_REJECT",
        "RECOMMEND_OFFER",
        "RUN_GENIE",
        "SAVE_DRAFT",
        "SAVE_LEAD",
        "UNSAVE_LEAD",
        "DELETE_DRAFT",
    }
)


def _event_type_for_payload(payload: AuditEventCreateRequest) -> str:
    return (payload.event_type or payload.action).replace(".", "_").replace("-", "_").upper()


@router.get("/events", response_model=list[AuditEvent])
def list_events(
    store: StoreDep,
    limit: Annotated[int, Query(ge=1, le=MAX_AUDIT_LIMIT)] = DEFAULT_AUDIT_LIMIT,
) -> list[AuditEvent]:
    try:
        return store.list(limit=limit)
    except LakebaseError as exc:
        # No silent fallback. The operator sees 503 and can decide
        # whether to force a redeploy or let Slice 6's resilience
        # kick in once it lands.
        # R5-03: constant body string; full ``str(exc)`` stays in the
        # LakebaseError WARNING + ``from exc`` chaining for ops.
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc


@router.post("/event", response_model=AuditEvent)
def log_event(
    payload: AuditEventCreateRequest,
    request: Request,
    store: StoreDep,
) -> AuditEvent:
    event_type = _event_type_for_payload(payload)
    if event_type in _ROUTER_OWNED_EVENT_TYPES or event_type.startswith("GENIE_ACTION_"):
        raise HTTPException(
            status_code=400,
            detail="event type is owned by a governed server route",
        )
    # R6 actor-spoof fix: never trust `payload.actor` from the request
    # body. The audit ledger must reflect the edge-authenticated identity,
    # not a client-supplied string — a caller passing `actor: "ceo@..."`
    # would otherwise have every row attributed to them. `resolve_actor`
    # reads `X-Forwarded-Email` (which the Databricks Apps edge strips +
    # re-injects from the bearer) and logs a WARNING on fallback so ops
    # sees non-authenticated calls.
    actor = resolve_actor(request)
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
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
