"""Governed lineage manifest endpoint.

Serves the repo-committed lineage manifest resolved for this deployment
(default catalog + workspace deep links). Static product truth — no
warehouse or Lakebase read, no PII. Because the payload embeds Catalog
Explorer deep-link URLs for the configured workspace host, the route
requires an authenticated workspace identity at the app layer instead of
relying only on the Databricks Apps edge — a non-Apps deploy
(docs/security/GRANTS.md §11a) must not serve workspace URLs to
anonymous callers.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.schemas.lineage import LineageManifestResponse
from backend.services.lineage_manifest import get_lineage_manifest_response
from backend.services.rbac import AuthenticatedActorDep

router = APIRouter(prefix="/lineage", tags=["lineage"])


@router.get("/manifest", response_model=LineageManifestResponse)
def get_lineage_manifest(_actor: AuthenticatedActorDep) -> LineageManifestResponse:
    return get_lineage_manifest_response()
