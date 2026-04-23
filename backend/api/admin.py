"""Admin API -- governed configuration + data source readiness.

Two read endpoints (both UC-backed as of slice13-accuracy follow-up):

* ``GET /api/admin/rules``    -- reads ``mip.ref.offer_rules_config``.
* ``GET /api/admin/sources``  -- reads per-table metadata via
  ``DESCRIBE DETAIL`` + ``SELECT COUNT(*)`` for the eight source-of-
  record tables that back the product.

Both paths surface warehouse failures as HTTP 503 (same contract as the
audit and outreach routers). The admin frontend shows a muted "data
source readiness temporarily unavailable" banner on 503 rather than
silently substituting literal values -- that is the CLAUDE.md
"no silent fallback" rule for the admin surface.

The legacy ``PUT /api/admin/rules`` is retained for backwards
compatibility with ``tests/unit/test_api_routes.py`` -- it is a
stored-in-memory knob surfaced to the admin UI as a hint banner and
does not write to Unity Catalog. The real ruleset vocabulary is the
Delta table; a future slice can promote PUT to an analyst-grade
governed write through a stored procedure.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.config.settings import settings
from backend.services.admin_rules import AdminRulesService, get_admin_rules_service
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.resilience import DependencyDownError

router = APIRouter(prefix="/api/admin", tags=["admin"])

ServiceDep = Annotated[AdminRulesService, Depends(get_admin_rules_service)]

# Legacy in-memory override kept for the PUT shim (see module docstring).
_RULES_OVERRIDE: dict[str, str] = {}


@router.get("/rules")
def get_rules(service: ServiceDep) -> dict[str, Any]:
    """Return the active offer-rule threshold vocabulary.

    Shape:
        {
          "offer_rules_version": "itm_<12-hex>",  // SHA256(hash of (key,value) pairs)
          "rules_edited_at":     "2026-04-22 17:02:11", // max(last_updated) ISO
          "thresholds": [ {key, value, unit, label, description, sort_order, last_updated}, ... ],
          // legacy fields for older clients:
          "legacy_override":     {...},            // mirror of PUT body
        }
    """
    try:
        payload = service.get_rules().to_dict()
    except DependencyDownError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # Legacy shim: expose any PUT-set override + preserve the v1 wire
    # shape expected by older clients (the frontend reads
    # ``offer_rules_version`` to label the panel chip).
    payload["legacy_override"] = dict(_RULES_OVERRIDE)
    return payload


class AdminRulesUpdateRequest(BaseModel):
    """Request shape for the legacy PUT /rules in-memory override.

    Locking the shape (`overrides: dict[str, str]`) means a stray
    `{"x": "y"}` PUT gets a 422 instead of silently landing in the
    override dict — the prior contract allowed arbitrary top-level keys.
    Round-3 hole-finder #17.
    """

    overrides: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


@router.put("/rules")
def put_rules(payload: AdminRulesUpdateRequest) -> dict[str, object]:
    """Legacy in-memory knob.

    Retained so existing ops tooling can push a hint value without a UC
    deploy. Writes are NOT persisted to Unity Catalog; the real ruleset
    is seeded via ``sql/ref/offer_rules_config_seed.sql`` and is the
    source of truth for GET. Schema now requires an explicit ``overrides``
    key — arbitrary top-level JSON is rejected with 422.
    """
    _RULES_OVERRIDE.update(payload.overrides)
    return {"status": "updated", "rules": dict(_RULES_OVERRIDE)}


@router.get("/sources")
def get_sources(service: ServiceDep) -> list[dict[str, Any]]:
    """Return per-source readiness rows.

    Shape:
        [
          {
            "name":         "Cotality Public Records",
            "status":       "live" | "roadmap",
            "rows":         12345 | null,
            "last_updated": "2026-04-22T17:02:11Z" | null,
            "note":         "Delta Share · nightly"
          },
          ...
        ]

    ``rows`` and ``last_updated`` are null for roadmap sources (MLS,
    Building Permits). ``status='live'`` means the underlying UC table
    responded to ``DESCRIBE DETAIL`` -- a zero row count is still LIVE,
    just empty.
    """
    try:
        rows = service.get_sources()
    except DependencyDownError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DatabricksSqlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [r.to_dict() for r in rows]


@router.get("/settings")
def get_settings() -> dict[str, object]:
    return {
        "app_env": settings.app_env,
        "lender_name": settings.mip_lender_name,
        "catalog": settings.mip_default_catalog,
        "gold_schema": settings.mip_default_schema,
        "lakebase_schema": settings.mip_lakebase_schema,
        "warehouse_id": settings.databricks_warehouse_id,
    }
