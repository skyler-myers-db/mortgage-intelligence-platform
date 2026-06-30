"""Admin API -- governed configuration + data source readiness.

Two read endpoints (both UC-backed as of slice13-accuracy follow-up):

* ``GET /api/admin/rules``    -- reads ``mip.ref.offer_rules_config`` plus
  the operating market rate from ``mip.gold.borrower_360``.
* ``GET /api/admin/sources``  -- reads per-table metadata via
  ``DESCRIBE DETAIL`` + ``SELECT COUNT(*)`` for the eight source-of-
  record tables that back the product.

Both paths surface warehouse failures as HTTP 503 (same contract as the
audit and outreach routers). The admin frontend shows a muted "data
source readiness temporarily unavailable" banner on 503 rather than
silently substituting literal values -- that is the CLAUDE.md
"no silent fallback" rule for the admin surface.

Rules are read-only from the app surface. Operating values come from
``mip.ref.offer_rules_config`` plus the live market-rate source. An app
click must not imply scoring changed unless the governed Unity Catalog
configuration and gold refresh path actually changed.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.config.settings import settings
from backend.schemas.admin import (
    AdminCapabilitiesResponse,
    AdminOperationRunRequest,
    AdminOperationRunResponse,
    AdminOperationsResponse,
    AdminRulesResponse,
    AdminRulesUpdateResponse,
    AdminSettingsResponse,
    AdminSourceResponse,
)
from backend.services.admin_rules import AdminRulesService, get_admin_rules_service
from backend.services.audit_store import AuditStore, get_audit_store
from backend.services.capabilities import probe_capabilities
from backend.services.capability_request import collect_request_live_capability_statuses
from backend.services.databricks_jobs import (
    DatabricksJobOperations,
    JobAlreadyRunningError,
    JobLaunch,
    JobOperationError,
    ManagedJobRun,
    ManagedJobStatus,
    get_job_operations,
)
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.forced_degraded import (
    FORCED_DEGRADED_COOKIE_NAME,
    FORCED_DEGRADED_COOKIE_PATH,
    build_forced_degraded_cookie,
)
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseError
from backend.services.observability import emit
from backend.services.rbac import AdminDep
from backend.services.resilience import DependencyDownError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

ServiceDep = Annotated[AdminRulesService, Depends(get_admin_rules_service)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]
JobsDep = Annotated[DatabricksJobOperations, Depends(get_job_operations)]

_JOB_COOLDOWN_SECONDS: dict[str, int] = {
    "fred_rates": 15 * 60,
    "silver_refresh": 60 * 60,
    "gold_refresh": 30 * 60,
    "lifecycle_sync": 5 * 60,
}


class ForceDegradedRequest(BaseModel):
    state: Literal["on", "off"]
    dependency: Literal["warehouse", "lakebase", "genie", "all"] = "warehouse"
    ttl_s: int = Field(default=60, ge=1, le=300)


class ForceDegradedResponse(BaseModel):
    forced: bool
    dependency: Literal["warehouse", "lakebase", "genie", "all"]
    expires_in_s: int


def _admin_audit_store(request: Request) -> AuditStore:
    try:
        factory = request.app.dependency_overrides.get(get_audit_store, get_audit_store)
        return factory()
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


AdminAuditDep = Annotated[AuditStore, Depends(_admin_audit_store)]


def _run_to_dict(run: ManagedJobRun | None) -> dict[str, object] | None:
    if run is None:
        return None
    return {
        "run_id": run.run_id,
        "life_cycle_state": run.life_cycle_state,
        "result_state": run.result_state,
        "state_message": run.state_message,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "run_page_url": run.run_page_url,
        "active": run.active,
    }


def _status_to_dict(status: ManagedJobStatus, *, cooldown_remaining_s: int = 0) -> dict[str, object]:
    return {
        "key": status.key,
        "label": status.label,
        "job_name": status.job_name,
        "job_id": status.job_id,
        "configured": status.configured,
        "description": status.description,
        "run_order": status.run_order,
        "cooldown_remaining_s": cooldown_remaining_s,
        "latest_run": _run_to_dict(status.latest_run),
        "recent_runs": [_run_to_dict(run) for run in status.recent_runs],
    }


def _parse_audit_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cooldown_remaining_s(audit: AuditStore, job_key: str) -> int:
    cooldown_s = _JOB_COOLDOWN_SECONDS.get(job_key, 0)
    if cooldown_s <= 0:
        return 0
    rows = audit.list(
        limit=1,
        action="admin.operation.run",
        entity_id=job_key,
    )
    if not rows:
        return 0
    created_at = _parse_audit_created_at(rows[0].created_at)
    if created_at is None:
        return 0
    age_s = (datetime.now(UTC) - created_at.astimezone(UTC)).total_seconds()
    remaining = cooldown_s - int(age_s)
    return max(0, remaining)


def _operation_payload(
    payload: AdminOperationRunRequest,
    **extra: object,
) -> dict[str, object]:
    return {
        "job_key": payload.job_key,
        "reason": payload.reason or "operator_refresh",
        **{key: value for key, value in extra.items() if value is not None},
    }


def _write_operation_audit(
    audit: AuditStore,
    *,
    actor: str,
    payload: AdminOperationRunRequest,
    action: str,
    event_type: str,
    extra: dict[str, object] | None = None,
) -> Any:
    return audit.write(
        actor=actor,
        action=action,
        entity_type="databricks_job",
        entity_id=payload.job_key,
        event_type=event_type,
        request_id=payload.request_id,
        payload_json=_operation_payload(payload, **(extra or {})),
    )


@router.get("/rules", response_model=AdminRulesResponse)
def get_rules(service: ServiceDep, _actor: AdminDep) -> dict[str, Any]:
    """Return the active offer-rule threshold vocabulary.

    Shape:
        {
          "offer_rules_version": "itm_<12-hex>",  // SHA256(hash of (key,value) pairs)
          "rules_edited_at":     "2026-04-22 17:02:11", // max(last_updated) ISO
          "thresholds": [ {key, value, unit, label, description, sort_order, last_updated}, ... ],
        }
    """
    try:
        payload = service.get_rules().to_dict()
    except DependencyDownError as exc:
        # R5-03: constant per-dependency detail; full ``str(exc)`` stays
        # in the structured log via ``from exc`` chaining.
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail(exc.dependency)
        ) from exc
    except DatabricksSqlError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("warehouse")
        ) from exc
    return payload


@router.put("/rules", response_model=AdminRulesUpdateResponse)
def put_rules(
    _actor: AdminDep,
) -> dict[str, object]:
    """Reject app-local threshold edits.

    Module 0's scoring contract is data-governed: rules must be changed
    in ``mip.ref.offer_rules_config`` and then propagated through a gold
    refresh. The former process-local override made the Admin surface look
    editable without changing a single borrower score, which is worse
    than no edit affordance at all.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "Offer rules are governed in mip.ref.offer_rules_config. "
            "Update the Unity Catalog rules seed or governed job, then refresh gold."
        ),
    )


@router.get("/sources", response_model=list[AdminSourceResponse])
def get_sources(service: ServiceDep, _actor: AdminDep) -> list[dict[str, Any]]:
    """Return per-source readiness rows.

    Shape:
        [
          {
            "name":         "Cotality Public Records",
            "status":       "live" | "demo_synthetic" | "configured_empty"
                            | "not_configured" | "roadmap"
                            | "permission_denied" | "error",
            "rows":         12345 | null,
            "last_updated": "2026-04-22T17:02:11Z" | null,
            "checked_at":   "2026-04-22T17:04:11Z" | null,
            "note":         "Delta Share · nightly"
          },
          ...
        ]

    ``rows`` and ``last_updated`` are null for roadmap sources such as
    Building Permits. MLS/Listings is live when
    ``mip.gold.source_readiness`` reports rows for that feed. Preferred production path reads
    ``mip.gold.source_readiness`` so the running app principal does not
    need direct silver grants.
    """
    try:
        rows = service.get_sources()
    except DependencyDownError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail(exc.dependency)
        ) from exc
    except DatabricksSqlError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("warehouse")
        ) from exc
    return [r.to_dict() for r in rows]


@router.get("/operations", response_model=AdminOperationsResponse)
def get_operations(_actor: AdminDep, jobs: JobsDep, audit: AdminAuditDep) -> dict[str, object]:
    """Return status for the allowlisted Databricks refresh jobs.

    This is intentionally separate from ``/admin/sources``. Source readiness
    answers "is data fresh enough to use?"; this endpoint answers "what
    operator jobs are configured and what did they last do?".
    """

    try:
        statuses = jobs.list_statuses()
    except JobOperationError as exc:
        emit(
            log,
            "admin_operations_status_error",
            level=logging.WARNING,
            exc_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("jobs"),
        ) from exc
    try:
        return {
            "jobs": [
                _status_to_dict(
                    status,
                    cooldown_remaining_s=_cooldown_remaining_s(audit, status.key),
                )
                for status in statuses
            ]
        }
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


@router.post(
    "/operations/run",
    response_model=AdminOperationRunResponse,
    status_code=202,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def post_operation_run(
    payload: AdminOperationRunRequest,
    _actor: AdminDep,
    _: Annotated[None, Depends(require_json_content_type)],
    audit: AdminAuditDep,
    jobs: JobsDep,
) -> dict[str, object]:
    """Trigger one allowlisted Databricks refresh job.

    The endpoint is admin-only, requires an explicit confirmation bit, and
    writes Lakebase audit before touching Databricks Jobs. If audit is down,
    the job is not launched.
    """

    if not payload.confirm:
        raise HTTPException(status_code=400, detail="confirmation required")

    try:
        cooldown_s = _cooldown_remaining_s(audit, payload.job_key)
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if cooldown_s > 0:
        try:
            _write_operation_audit(
                audit,
                actor=_actor,
                payload=payload,
                action="admin.operation.cooldown",
                event_type="ADMIN_OPERATION_COOLDOWN",
                extra={"cooldown_seconds": cooldown_s},
            )
        except LakebaseError as exc:
            raise HTTPException(
                status_code=503,
                detail=safe_dependency_detail("lakebase"),
            ) from exc
        raise HTTPException(
            status_code=429,
            detail="job cooldown active",
            headers={"Retry-After": str(cooldown_s)},
        )

    try:
        request_event = _write_operation_audit(
            audit,
            actor=_actor,
            payload=payload,
            action="admin.operation.requested",
            event_type="ADMIN_OPERATION_REQUESTED",
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc

    try:
        if payload.job_key == "lifecycle_sync":
            from backend.services.lifecycle_sync import sync_lifecycle_state_via_warehouse

            result = sync_lifecycle_state_via_warehouse()
            launch = JobLaunch(
                key="lifecycle_sync",
                label="Sync workflow state",
                job_name="warehouse_lifecycle_sync",
                job_id=0,
                run_id=None,
                run_page_url=None,
            )
            lifecycle_extra: dict[str, object] = {
                "lifecycle_sync_mode": "warehouse",
                "lakebase_row_count": result.lakebase_rows,
                "mirrored_row_count": result.mirrored_rows,
                "funnel_snapshot_row_count": result.funnel_snapshot_rows,
            }
        else:
            launch = jobs.run_now(payload.job_key)
            lifecycle_extra = {}
    except JobAlreadyRunningError as exc:
        try:
            _write_operation_audit(
                audit,
                actor=_actor,
                payload=payload,
                action="admin.operation.conflict",
                event_type="ADMIN_OPERATION_CONFLICT",
                extra={"run_id": exc.run_id},
            )
        except LakebaseError:
            emit(
                log,
                "admin_operation_conflict_audit_dropped",
                level=logging.WARNING,
                job_key=payload.job_key,
            )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "job already running",
                "job_key": exc.job_key,
                "run_id": exc.run_id,
            },
        ) from exc
    except (LakebaseError, DatabricksSqlError, DependencyDownError) as exc:
        try:
            _write_operation_audit(
                audit,
                actor=_actor,
                payload=payload,
                action="admin.operation.failed",
                event_type="ADMIN_OPERATION_FAILED",
            )
        except LakebaseError:
            emit(
                log,
                "admin_operation_failure_audit_dropped",
                level=logging.WARNING,
                job_key=payload.job_key,
            )
        dependency = "lakebase" if isinstance(exc, LakebaseError) else "warehouse"
        emit(
            log,
            "admin_operation_lifecycle_sync_error",
            level=logging.WARNING,
            job_key=payload.job_key,
            dependency=dependency,
            exc_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail(dependency),
        ) from exc
    except JobOperationError as exc:
        try:
            _write_operation_audit(
                audit,
                actor=_actor,
                payload=payload,
                action="admin.operation.failed",
                event_type="ADMIN_OPERATION_FAILED",
            )
        except LakebaseError:
            emit(
                log,
                "admin_operation_failure_audit_dropped",
                level=logging.WARNING,
                job_key=payload.job_key,
            )
        emit(
            log,
            "admin_operation_run_error",
            level=logging.WARNING,
            job_key=payload.job_key,
            exc_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("jobs"),
        ) from exc

    try:
        accepted_event = _write_operation_audit(
            audit,
            actor=_actor,
            payload=payload,
            action="admin.operation.run",
            event_type="ADMIN_OPERATION_RUN",
            extra={
                "job_name": launch.job_name,
                "job_id": launch.job_id,
                "run_id": launch.run_id,
                **lifecycle_extra,
            },
        )
    except LakebaseError:
        emit(
            log,
            "admin_operation_success_audit_dropped",
            level=logging.WARNING,
            job_key=payload.job_key,
            job_id=launch.job_id,
            run_id=launch.run_id,
        )
        accepted_event = request_event

    return {
        "accepted": True,
        "key": launch.key,
        "label": launch.label,
        "job_name": launch.job_name,
        "job_id": launch.job_id,
        "run_id": launch.run_id,
        "run_page_url": launch.run_page_url,
        "audit_event_id": accepted_event.event_id,
    }


@router.get("/capabilities", response_model=AdminCapabilitiesResponse)
def get_capabilities(_actor: AdminDep, request: Request, live: bool = False) -> dict[str, object]:
    """Return the honest DAIS-2026 capability snapshot for this workspace.

    Best-effort live probes may upgrade configured rows to ``available``. Probe
    failures never 503 this route; they leave the row non-claimable with a
    probe-failed detail. Each row's ``status``/``claimable`` remains the
    enforcement point for the no-overclaim posture.
    """
    live_statuses = collect_request_live_capability_statuses(request) if live else None
    return {"capabilities": [cap.to_dict() for cap in probe_capabilities(live_statuses=live_statuses)]}


@router.get("/settings", response_model=AdminSettingsResponse)
def get_settings(_actor: AdminDep) -> dict[str, object]:
    return {
        "app_env": settings.app_env,
        "lender_name": settings.mip_lender_name,
        "catalog": settings.mip_default_catalog,
        "gold_schema": settings.mip_default_schema,
        "lakebase_schema": settings.mip_lakebase_schema,
        "warehouse_id": settings.databricks_warehouse_id,
    }


@router.post(
    "/force-degraded",
    response_model=ForceDegradedResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def post_force_degraded(
    payload: ForceDegradedRequest,
    request: Request,
    response: Response,
    _actor: AdminDep,
    _: Annotated[None, Depends(require_json_content_type)],
    audit: AuditDep,
) -> ForceDegradedResponse:
    """Issue or clear a browser-local degraded-banner proof cookie.

    This endpoint is an admin actuator and therefore writes Lakebase audit
    before changing the browser-visible state. It does not mutate global health
    or stop infrastructure.
    """

    ttl_s = payload.ttl_s if payload.state == "on" else 0
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_s)
    try:
        audit.write(
            actor=_actor,
            action="admin.force_degraded",
            entity_type="system",
            entity_id="force-degraded",
            event_type="FORCE_DEGRADED",
            payload_json={
                "forced_state": payload.state,
                "forced_dependency": payload.dependency,
                "ttl_s": ttl_s,
                "expires_at": expires_at.isoformat(),
                "proof_scope": "browser_cookie",
                "route": "/api/admin/force-degraded",
            },
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc

    secure_cookie = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() == "https"
    )

    if payload.state == "off":
        response.delete_cookie(
            FORCED_DEGRADED_COOKIE_NAME,
            path=FORCED_DEGRADED_COOKIE_PATH,
            secure=secure_cookie,
            samesite="lax",
        )
        emit(
            log,
            "forced_degraded_cookie_cleared",
            dependency=payload.dependency,
            proof_scope="browser_cookie",
        )
        return ForceDegradedResponse(
            forced=False,
            dependency=payload.dependency,
            expires_in_s=0,
        )

    cookie_value, snapshot = build_forced_degraded_cookie(
        dependency=payload.dependency,
        ttl_s=payload.ttl_s,
    )
    response.set_cookie(
        FORCED_DEGRADED_COOKIE_NAME,
        cookie_value,
        max_age=snapshot.expires_in_s,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path=FORCED_DEGRADED_COOKIE_PATH,
    )
    emit(
        log,
        "forced_degraded_cookie_issued",
        dependency=snapshot.dependency,
        expires_in_s=snapshot.expires_in_s,
        proof_scope="browser_cookie",
    )
    return ForceDegradedResponse(
        forced=snapshot.active,
        dependency=snapshot.dependency,
        expires_in_s=snapshot.expires_in_s,
    )
