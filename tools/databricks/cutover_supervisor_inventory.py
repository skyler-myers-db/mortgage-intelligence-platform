"""Direct, complete Supervisor inventory for destructive cutover proof."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from databricks.sdk.errors import NotFound, ResourceDoesNotExist

MAX_CUTOVER_SUPERVISORS = 10_000


def supervisor_by_id_direct(
    workspace: Any,
    supervisor_id: str,
) -> dict[str, Any] | None:
    """Read one immutable Supervisor directly, without a lossy list projection."""

    normalized = supervisor_id.strip()
    if not normalized:
        raise ValueError("Supervisor immutable ID is required")
    try:
        payload = workspace.api_client.do(
            "GET",
            f"/api/2.1/supervisor-agents/{quote(normalized, safe='')}",
        )
    except (NotFound, ResourceDoesNotExist):
        return None
    if not isinstance(payload, Mapping):
        raise RuntimeError("direct Supervisor metadata is malformed")
    row = {str(key): value for key, value in payload.items()}
    if str(row.get("supervisor_agent_id") or "").strip() != normalized:
        raise RuntimeError("direct Supervisor immutable identity drifted")
    for field in ("display_name", "endpoint_name", "creator", "create_time"):
        if not str(row.get(field) or "").strip():
            raise RuntimeError("direct Supervisor metadata is incomplete")
    return row


def supervisor_inventory_direct(
    workspace: Any,
) -> tuple[Mapping[str, Any], ...]:
    """Read every Supervisor page and hydrate each exact immutable record."""

    rows: list[Mapping[str, Any]] = []
    ids: set[str] = set()
    endpoints: set[str] = set()
    seen_tokens: set[str] = set()
    token: str | None = None
    while True:
        query: dict[str, object] = {"page_size": 100}
        if token is not None:
            query["page_token"] = token
        payload = workspace.api_client.do(
            "GET",
            "/api/2.1/supervisor-agents",
            query=query,
        )
        if not isinstance(payload, Mapping) or "supervisor_agents" not in payload:
            raise RuntimeError("complete Supervisor inventory is malformed")
        page = payload["supervisor_agents"]
        if not isinstance(page, list):
            raise RuntimeError("complete Supervisor inventory is malformed")
        for raw in page:
            if len(rows) >= MAX_CUTOVER_SUPERVISORS:
                raise RuntimeError("complete Supervisor inventory exceeds the reviewed bound")
            if not isinstance(raw, Mapping):
                raise RuntimeError("complete Supervisor inventory is malformed")
            listed = {str(key): value for key, value in raw.items()}
            supervisor_id = str(listed.get("supervisor_agent_id") or "").strip()
            endpoint = str(listed.get("endpoint_name") or "").strip()
            if not supervisor_id or not endpoint:
                raise RuntimeError("complete Supervisor inventory has a missing identity")
            if supervisor_id in ids or endpoint in endpoints:
                raise RuntimeError("complete Supervisor inventory has a duplicate identity")
            hydrated = supervisor_by_id_direct(workspace, supervisor_id)
            if hydrated is None:
                raise RuntimeError("Supervisor disappeared during complete inventory")
            identity_fields = (
                "supervisor_agent_id",
                "display_name",
                "endpoint_name",
                "creator",
                "create_time",
            )
            if tuple(
                str(listed.get(field) or "").strip() for field in identity_fields
            ) != tuple(
                str(hydrated.get(field) or "").strip() for field in identity_fields
            ):
                raise RuntimeError("complete Supervisor inventory drifted during hydration")
            ids.add(supervisor_id)
            endpoints.add(endpoint)
            rows.append(hydrated)
        raw_next = payload.get("next_page_token")
        if raw_next in {None, ""}:
            return tuple(rows)
        if (
            not isinstance(raw_next, str)
            or not raw_next
            or raw_next != raw_next.strip()
            or raw_next in seen_tokens
        ):
            raise RuntimeError("complete Supervisor inventory page token is malformed")
        seen_tokens.add(raw_next)
        token = raw_next
