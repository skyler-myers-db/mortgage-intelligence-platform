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
    AdminRulesResponse,
    AdminRulesUpdateResponse,
    AdminSettingsResponse,
    AdminSourceResponse,
)
from backend.services.admin_rules import AdminRulesService, get_admin_rules_service
from backend.services.audit_store import AuditStore, get_audit_store
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.forced_degraded import (
    FORCED_DEGRADED_COOKIE_NAME,
    FORCED_DEGRADED_COOKIE_PATH,
    build_forced_degraded_cookie,
)
from backend.services.lakebase import LakebaseError
from backend.services.observability import emit
from backend.services.rbac import AdminDep
from backend.services.resilience import DependencyDownError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

ServiceDep = Annotated[AdminRulesService, Depends(get_admin_rules_service)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]


class ForceDegradedRequest(BaseModel):
    state: Literal["on", "off"]
    dependency: Literal["warehouse", "lakebase", "genie", "all"] = "warehouse"
    ttl_s: int = Field(default=60, ge=1, le=300)


class ForceDegradedResponse(BaseModel):
    forced: bool
    dependency: Literal["warehouse", "lakebase", "genie", "all"]
    expires_in_s: int


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

    ``rows`` and ``last_updated`` are null for roadmap sources (MLS,
    Building Permits). Preferred production path reads
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


@router.post("/force-degraded", response_model=ForceDegradedResponse)
def post_force_degraded(
    payload: ForceDegradedRequest,
    request: Request,
    response: Response,
    _actor: AdminDep,
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
