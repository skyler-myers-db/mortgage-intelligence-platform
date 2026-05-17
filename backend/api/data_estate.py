from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.data_estate import DataEstateResponse
from backend.services.admin_rules import AdminRulesService, get_admin_rules_service
from backend.services.data_estate import build_data_estate_response
from backend.services.health_probes import cached_probe, probe_genie, probe_lakebase

router = APIRouter(prefix="/api/data-estate", tags=["data-estate"])

AdminRulesServiceDep = Annotated[AdminRulesService, Depends(get_admin_rules_service)]


@router.get("", response_model=DataEstateResponse)
def get_data_estate(service: AdminRulesServiceDep) -> DataEstateResponse:
    runtime_statuses: dict[str, bool] = {}
    for name, probe in {"genie": probe_genie, "lakebase": probe_lakebase}.items():
        try:
            runtime_statuses[name] = bool(cached_probe(name, probe))
        except Exception:  # noqa: BLE001 - proof surface degrades; it never fabricates live.
            runtime_statuses[name] = False
    return build_data_estate_response(service.get_sources(), runtime_statuses=runtime_statuses)
