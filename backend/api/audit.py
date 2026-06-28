"""Audit API -- list + log audit events.

Slice 5 migrates this router off the in-memory store onto the
Lakebase-backed ``AuditStore`` via ``get_audit_store``. The wire shape
is unchanged so the frontend Activity Log keeps working.
"""
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.audit import AuditEvent, AuditEventCreateRequest, AuditRollupResponse
from backend.schemas.common import validate_public_borrower_id
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    AuditMetadataViolation,
    AuditPIIError,
    AuditStore,
    get_audit_store,
    resolve_actor,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.observability import is_safe_correlation_id
from backend.services.rbac import AdminDep

router = APIRouter(prefix="/audit", tags=["audit"])

StoreDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]

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
        "CAMPAIGN_STATUS_UPDATE",
        "DRAFT_OUTREACH",
        "OUTREACH_APPROVE",
        "OUTREACH_REJECT",
        "PORTFOLIO_CREATE",
        "RECOMMEND_OFFER",
        "RUN_GENIE",
        "SAVE_DRAFT",
        "SAVE_LEAD",
        "UNSAVE_LEAD",
        "DELETE_DRAFT",
        "CALL_DISPOSITION",
        "LEAD_ASSIGN",
        "LEAD_DISTRIBUTE",
        "LEAD_OUTCOME",
        "VIEW_BORROWER",
        "VIEW_LEADS",
    }
)
def _event_type_for_payload(payload: AuditEventCreateRequest) -> str:
    return (payload.event_type or payload.action).replace(".", "_").replace("-", "_").upper()


def _validate_correlation_filter(value: str) -> str:
    if not is_safe_correlation_id(value):
        raise ValueError("correlation_id must be a non-PII request correlation id")
    return value


@router.get("/events", response_model=list[AuditEvent])
def list_events(
    store: StoreDep,
    _actor: AdminDep,
    limit: Annotated[int, Query(ge=1, le=MAX_AUDIT_LIMIT)] = DEFAULT_AUDIT_LIMIT,
    actor: Annotated[str | None, Query(max_length=256)] = None,
    action: Annotated[str | None, Query(max_length=128)] = None,
    entity_id: Annotated[str | None, Query(max_length=256)] = None,
    borrower_id: Annotated[str | None, Query(max_length=64)] = None,
    subject_clip: Annotated[str | None, Query(max_length=128)] = None,
    event_type: Annotated[str | None, Query(max_length=128)] = None,
    correlation_id: Annotated[str | None, Query(max_length=128)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditEvent]:
    if borrower_id is not None:
        try:
            validate_public_borrower_id(borrower_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid borrower_id") from exc
    if correlation_id is not None:
        try:
            correlation_id = _validate_correlation_filter(correlation_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid correlation_id") from exc
    try:
        return store.list(
            limit=limit,
            actor=actor,
            action=action,
            entity_id=entity_id,
            borrower_id=borrower_id,
            subject_clip=subject_clip,
            event_type=event_type,
            correlation_id=correlation_id,
            since=since,
            until=until,
        )
    except LakebaseError as exc:
        # No silent fallback. The operator sees 503 and can decide
        # whether to force a redeploy or let Slice 6's resilience
        # kick in once it lands.
        # R5-03: constant body string; full ``str(exc)`` stays in the
        # LakebaseError WARNING + ``from exc`` chaining for ops.
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc


@router.get("/rollups", response_model=list[AuditRollupResponse])
def audit_rollups(
    _actor: AdminDep,
    lakebase: LakebaseDep,
    period: Annotated[Literal["day", "week", "month"], Query()] = "week",
    group_by: Annotated[
        Literal["event_type", "actor", "action"],
        Query(alias="groupBy"),
    ] = "event_type",
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditRollupResponse]:
    """Approval/rejection counts by period for committee review.

    The endpoint reads the governed Lakebase audit ledger directly and
    returns only aggregate counts. It does not invent outreach state or
    pull borrower PII into the response.
    """
    clauses = [
        "event_type IN ("
        "'APPROVE', 'OUTREACH_APPROVE', 'OUTREACH_REJECT', "
        "'CALL_DISPOSITION', 'LEAD_ASSIGN', 'LEAD_DISTRIBUTE', "
        "'LEAD_OUTCOME'"
        ")"
    ]
    params: dict[str, object] = {"period": period}
    if since is not None:
        clauses.append("event_at >= %(since)s")
        params["since"] = since
    if until is not None:
        clauses.append("event_at <= %(until)s")
        params["until"] = until
    group_expr = {
        "event_type": "event_type",
        "actor": "actor_email",
        "action": "metadata->>'action'",
    }[group_by]
    sql = f"""
    SELECT
      DATE_TRUNC(%(period)s, event_at) AS bucket_start,
      {group_expr} AS group_key,
      COUNT(*) AS event_count
    FROM mip_app.action_audit
    WHERE {' AND '.join(clauses)}
    GROUP BY 1, 2
    ORDER BY 1 DESC, 2
    LIMIT 104
    """
    try:
        rows = lakebase.fetchall(sql, params, limit=104)
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
    return [
        AuditRollupResponse(
            bucket_start=(
                row["bucket_start"].isoformat()
                if hasattr(row.get("bucket_start"), "isoformat")
                else str(row.get("bucket_start"))
            ),
            event_type=str(row.get("group_key")) if group_by == "event_type" else None,
            group_by=group_by,
            group_key=str(row.get("group_key")) if row.get("group_key") is not None else None,
            event_count=int(row.get("event_count") or 0),
        )
        for row in rows
    ]


@router.post("/event", response_model=AuditEvent, responses=JSON_CONTENT_TYPE_RESPONSE)
def log_event(
    payload: AuditEventCreateRequest,
    request: Request,
    store: StoreDep,
    _: Annotated[None, Depends(require_json_content_type)],
    _actor: AdminDep,
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
    except (AuditPIIError, AuditMetadataViolation, AuditMetadataValueViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
