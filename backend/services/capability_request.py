"""Request-scoped live capability probe helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request

from backend.config.settings import get_settings
from backend.services.capabilities import LiveCapabilityStatus, collect_live_capability_statuses
from backend.services.databricks_sql import get_sql_client
from backend.services.genie_client import get_genie_client
from backend.services.lakebase import get_lakebase_client


def collect_request_live_capability_statuses(
    request: Request,
    *,
    include_lakebase: bool = True,
) -> dict[str, LiveCapabilityStatus]:
    """Best-effort live evidence for capability rows.

    A failed dependency construction or probe must not make the admin/Growth
    Agent overview fail. The capability row simply remains non-claimable with a
    probe-failed detail.
    """

    sql_client, sql_error = _resolve_dependency(request, get_sql_client)
    genie_client, genie_error = _resolve_dependency(request, get_genie_client)
    lakebase, lakebase_error = (
        _resolve_dependency(request, get_lakebase_client) if include_lakebase else (None, None)
    )
    workspace_client, workspace_error = _workspace_client()
    statuses: dict[str, LiveCapabilityStatus] = {}
    if sql_error:
        statuses["certified_metric_views"] = LiveCapabilityStatus(False, sql_error)
        statuses["uc_function_tools"] = LiveCapabilityStatus(False, sql_error)
    if genie_error:
        statuses["genie_conversation_api"] = LiveCapabilityStatus(False, genie_error)
    if lakebase_error:
        statuses["lakebase_sync"] = LiveCapabilityStatus(False, lakebase_error)
    for key in ("agent_eval", "agent_orchestrator", "ai_gateway"):
        if workspace_error:
            statuses[key] = LiveCapabilityStatus(False, workspace_error)
    statuses.update(
        collect_live_capability_statuses(
            settings=get_settings(),
            sql_client=sql_client,
            genie_client=genie_client,
            lakebase=lakebase,
            workspace_client=workspace_client,
        )
    )
    return statuses


def _resolve_dependency(
    request: Request, factory: Callable[[], Any]
) -> tuple[Any | None, str | None]:
    try:
        provider = request.app.dependency_overrides.get(factory, factory)
        return provider(), None
    except Exception as exc:  # noqa: BLE001 - reflected as a non-claimable capability row
        return None, f"{factory.__name__} dependency unavailable ({type(exc).__name__})."


def _workspace_client() -> tuple[Any | None, str | None]:
    try:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient(), None
    except Exception as exc:  # noqa: BLE001 - reflected as a non-claimable capability row
        return None, f"WorkspaceClient dependency unavailable ({type(exc).__name__})."
