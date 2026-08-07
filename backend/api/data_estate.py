"""Data-estate readiness endpoint for source and dependency proof.

The payload embeds Catalog Explorer deep-link URLs for the configured
workspace host, so the route requires an authenticated workspace identity
at the app layer instead of relying only on the Databricks Apps edge —
a non-Apps deploy (docs/security/GRANTS.md §11a) must not serve workspace
URLs to anonymous callers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.data_estate import DataEstateResponse
from backend.services.admin_rules import AdminRulesService, get_admin_rules_service
from backend.services.data_estate import build_data_estate_response
from backend.services.health_probes import cached_probe, probe_genie, probe_lakebase
from backend.services.rbac import AuthenticatedActorDep

router = APIRouter(prefix="/data-estate", tags=["data-estate"])

# Plain read-service factory (source-readiness rows sourced by the admin
# rules service), NOT an authorization gate — auth for this route is
# ``AuthenticatedActorDep``.
SourceReadinessDep = Annotated[AdminRulesService, Depends(get_admin_rules_service)]


@router.get("", response_model=DataEstateResponse)
def get_data_estate(
    service: SourceReadinessDep,
    _actor: AuthenticatedActorDep,
) -> DataEstateResponse:
    runtime_statuses: dict[str, bool] = {}
    for name, probe in {"genie": probe_genie, "lakebase": probe_lakebase}.items():
        try:
            runtime_statuses[name] = bool(cached_probe(name, probe))
        except Exception:  # noqa: BLE001 - proof surface degrades; it never fabricates live.
            runtime_statuses[name] = False
    return build_data_estate_response(service.get_sources(), runtime_statuses=runtime_statuses)
