"""Asset freshness for every authenticated user (audit 2026-09-21 ``critic-03``).

Decision ``D-audit-reads-c1``: freshness is part of the evidence, not an
administrator detail, so the evidence drawer shows the same freshness band,
business refresh and readiness basis to every role. Schema internals (DDL,
columns, tags, properties, size, observed lineage) stay on the AdminDep
``/admin/assets/{key}/metadata`` read and the asset detail route.

AUDIT EXEMPT: an aggregate source-readiness read with no borrower data; the
drawer issues it only while it is open on a mapped asset (never on hover,
prefetch or poll), and it writes no audit row.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.schemas.assets import AssetFreshnessResponse
from backend.services.asset_metadata import (
    AssetMetadataService,
    AssetNotFoundError,
    get_asset_metadata_service,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.rbac import AuthenticatedActorDep
from backend.services.resilience import DependencyDownError

router = APIRouter(prefix="/assets", tags=["assets"])

ServiceDep = Annotated[AssetMetadataService, Depends(get_asset_metadata_service)]


@router.get("/{asset_key}/freshness", response_model=AssetFreshnessResponse)
def get_asset_freshness(
    asset_key: str,
    response: Response,
    service: ServiceDep,
    _actor: AuthenticatedActorDep,
) -> AssetFreshnessResponse:
    """Freshness band, last refresh and readiness basis of a registered asset.

    Reads one reviewed source-readiness row; an asset outside the fixed
    registry is a 404 before any SQL runs. A warehouse failure is a 503 and
    is never cached, so the next open asks again.
    """

    response.headers["Cache-Control"] = "private, no-store"
    try:
        return service.get_freshness(asset_key)
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="asset not found") from exc
    except DependencyDownError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail(exc.dependency),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - every SQL failure is a warehouse 503
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("warehouse"),
        ) from exc
