from fastapi import APIRouter

from backend.config.settings import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])

_RULES: dict[str, str] = {"offer_rules_version": "demo-v1"}


@router.get("/rules")
def get_rules() -> dict[str, str]:
    return _RULES


@router.put("/rules")
def put_rules(payload: dict[str, str]) -> dict[str, object]:
    _RULES.update(payload)
    return {"status": "updated", "rules": dict(_RULES)}


@router.get("/settings")
def get_settings() -> dict[str, object]:
    return {
        "app_env": settings.app_env,
        "mock_mode": settings.mip_mock_mode,
        "demo_lender": settings.mip_demo_lender,
        "catalog": settings.mip_default_catalog,
        "gold_schema": settings.mip_default_schema,
        "lakebase_schema": settings.mip_lakebase_schema,
    }
