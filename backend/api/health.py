from fastapi import APIRouter

from backend.config.settings import settings

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, object]:
    # Slice-4 invariant: the app always runs in live mode against the
    # real Unity Catalog warehouse. There is no mock runtime path.
    # Missing creds fail fast at process start (see settings.py), so if
    # we're here the warehouse id has been resolved.
    return {
        "status": "ok",
        "mode": "live",
        "app_env": settings.app_env,
        "warehouse_id": settings.databricks_warehouse_id,
    }
