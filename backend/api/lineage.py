"""Governed lineage manifest endpoint.

Serves the repo-committed lineage manifest resolved for this deployment
(default catalog + workspace deep links). Static product truth — no
warehouse or Lakebase read, no PII, so the route is open to any
authenticated workspace user like the other read surfaces.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.schemas.lineage import LineageManifestResponse
from backend.services.lineage_manifest import get_lineage_manifest_response

router = APIRouter(prefix="/lineage", tags=["lineage"])


@router.get("/manifest", response_model=LineageManifestResponse)
def get_lineage_manifest() -> LineageManifestResponse:
    return get_lineage_manifest_response()
