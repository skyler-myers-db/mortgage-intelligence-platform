"""Admin-gated governed asset metadata endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.assets import AssetMetadataResponse
from backend.services.asset_metadata import (
    AssetMetadataService,
    AssetNotFoundError,
    get_asset_metadata_service,
)
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.rbac import AdminDep
from backend.services.resilience import DependencyDownError

router = APIRouter(prefix="/admin/assets", tags=["admin"])

ServiceDep = Annotated[AssetMetadataService, Depends(get_asset_metadata_service)]


@router.get("/{asset_key}/metadata", response_model=AssetMetadataResponse)
def get_asset_metadata(
    asset_key: str,
    service: ServiceDep,
    _actor: AdminDep,
) -> AssetMetadataResponse:
    """Return sanitized metadata for a registered Module 0 UC asset.

    This route is intentionally admin-gated: it may include schema, DDL
    contract, tags, and observed lineage. The service itself rejects any
    asset outside the fixed Module 0 registry before interpolating an
    identifier into SQL.
    """

    try:
        return service.get_asset(asset_key)
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="asset not found") from exc
    except DependencyDownError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail(exc.dependency),
        ) from exc
    except DatabricksSqlError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("warehouse"),
        ) from exc
