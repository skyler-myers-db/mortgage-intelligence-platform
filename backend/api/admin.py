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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from backend.config.settings import settings
from backend.services.admin_rules import AdminRulesService, get_admin_rules_service
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.rbac import AdminDep
from backend.services.resilience import DependencyDownError

router = APIRouter(prefix="/api/admin", tags=["admin"])

ServiceDep = Annotated[AdminRulesService, Depends(get_admin_rules_service)]


@router.get("/rules")
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


@router.put("/rules")
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


@router.get("/sources")
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


@router.get("/settings")
def get_settings(_actor: AdminDep) -> dict[str, object]:
    return {
        "app_env": settings.app_env,
        "lender_name": settings.mip_lender_name,
        "catalog": settings.mip_default_catalog,
        "gold_schema": settings.mip_default_schema,
        "lakebase_schema": settings.mip_lakebase_schema,
        "warehouse_id": settings.databricks_warehouse_id,
    }
