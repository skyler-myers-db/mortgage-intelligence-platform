"""Attest a signed-cutover Supervisor predecessor retained only for retirement."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from backend.agents.supervisor_contract import (
    supervisor_contract_document,
    supervisor_tool_resource_is_exact,
)
from tools.databricks.cutover_journal_store import read_cutover_journal
from tools.databricks.historical_agent_endpoint_types import SupervisorPin


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _rows(
    workspace: Any,
    supervisor_id: str,
    collection: str,
    *,
    allow_omitted_empty: bool = False,
) -> list[Any]:
    payload = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}/{collection}",
    )
    if allow_omitted_empty and payload == {}:
        return []
    rows = payload if isinstance(payload, list) else _field(payload, collection)
    if not isinstance(rows, list):
        raise RuntimeError(f"historical Supervisor {collection} inventory is malformed")
    return rows


def _assert_reviewed_tool(
    row: dict[str, Any],
    *,
    expected: dict[str, Any],
    supervisor_id: str,
    tool_id: str,
    tool_type: str,
) -> None:
    allowed_fields = {"tool_id", "tool_type", "description", tool_type}
    provider_name = row.get("name")
    if provider_name is not None:
        allowed_fields.add("name")
        if provider_name != f"supervisor-agents/{supervisor_id}/tools/{tool_id}":
            raise RuntimeError(f"historical Supervisor tool {tool_id!r} provider identity drifted")
    if set(row) != allowed_fields:
        raise RuntimeError(f"historical Supervisor tool {tool_id!r} contains unexpected fields")
    if not supervisor_tool_resource_is_exact(
        tool_type,
        row.get(tool_type),
        expected[tool_type],
    ):
        raise RuntimeError(f"historical Supervisor tool {tool_id!r} reviewed body drifted")


def attest_historical_supervisor_retirement_predecessor(
    workspace: Any,
    *,
    direct: Mapping[str, Any],
    endpoint_details: object,
    pin: SupervisorPin,
    canonical_name: str,
    genie_space_id: str,
    catalog: str,
    runtime_application_id: str,
) -> tuple[str, str]:
    """Return canonical evidence for a pinned, non-active cutover predecessor."""

    journal = read_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    signed_tuple = None
    if journal is not None:
        signed_tuple = (
            journal.get("canonical_name"),
            journal.get("old_id"),
            journal.get("old_endpoint"),
            journal.get("old_endpoint_id"),
            journal.get("old_creator"),
            journal.get("old_create_time"),
        )
    if signed_tuple != (
        canonical_name,
        pin.supervisor_id,
        pin.endpoint,
        pin.endpoint_id,
        pin.creator,
        _text(direct.get("create_time")),
    ):
        raise RuntimeError(
            "historical Supervisor retirement tuple is not bound to the signed cutover journal"
        )
    immutable = (
        _text(direct.get("supervisor_agent_id")),
        _text(direct.get("endpoint_name")),
        _text(_field(endpoint_details, "id")),
        _text(direct.get("creator")),
    )
    if (
        immutable
        != (
            pin.supervisor_id,
            pin.endpoint,
            pin.endpoint_id,
            pin.creator,
        )
        or _text(_field(endpoint_details, "creator")) != pin.creator
    ):
        raise RuntimeError("historical Supervisor retirement tuple drifted")
    if (
        _field(endpoint_details, "pending_config") is not None
        or _text(_field(endpoint_details, "task")).lower() != "agent/v1/responses"
        or _text(_field(_field(endpoint_details, "state"), "ready")).upper() != "READY"
        or _text(_field(_field(endpoint_details, "state"), "config_update")).upper()
        != "NOT_UPDATING"
    ):
        raise RuntimeError("historical Supervisor retirement endpoint is not stable")

    reviewed = supervisor_contract_document(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    if (
        direct.get("description") != reviewed["description"]
        or direct.get("instructions") != reviewed["instructions"]
    ):
        raise RuntimeError("historical Supervisor base definition drifted")
    expected_tools = {tool["tool_id"]: tool for tool in reviewed["tools"]}
    actual_tools: dict[str, dict[str, Any]] = {}
    for raw in _rows(workspace, pin.supervisor_id, "tools"):
        if not isinstance(raw, Mapping):
            raise RuntimeError("historical Supervisor tools inventory is malformed")
        row = {str(key): value for key, value in raw.items()}
        tool_id = _text(row.get("tool_id"))
        if not tool_id or tool_id in actual_tools:
            raise RuntimeError("historical Supervisor tool identities are missing or duplicated")
        expected = expected_tools.get(tool_id)
        tool_type = _text(row.get("tool_type"))
        if (
            expected is None
            or row.get("description") != expected["description"]
            or tool_type != expected["tool_type"]
        ):
            raise RuntimeError(
                f"historical Supervisor tool {tool_id!r} is outside the reviewed contract"
            )
        _assert_reviewed_tool(
            row,
            expected=expected,
            supervisor_id=pin.supervisor_id,
            tool_id=tool_id,
            tool_type=tool_type,
        )
        actual_tools[tool_id] = expected
    if not actual_tools:
        raise RuntimeError("historical Supervisor has no reviewed tool evidence")
    if _rows(
        workspace,
        pin.supervisor_id,
        "examples",
        allow_omitted_empty=True,
    ):
        raise RuntimeError("historical Supervisor contains unreviewed examples")

    document = {
        "version": 1,
        "kind": "signed-cutover-retirement-predecessor",
        "canonical_name": canonical_name,
        "catalog": catalog,
        "genie_space_id": genie_space_id,
        "supervisor_id": pin.supervisor_id,
        "display_name": _text(direct.get("display_name")),
        "endpoint": pin.endpoint,
        "endpoint_id": pin.endpoint_id,
        "creator": pin.creator,
        "create_time": _text(direct.get("create_time")),
        "description": reviewed["description"],
        "instructions": reviewed["instructions"],
        "tools": [actual_tools[tool_id] for tool_id in sorted(actual_tools)],
        "examples": [],
    }
    if not document["display_name"] or not document["create_time"]:
        raise RuntimeError("historical Supervisor retirement identity is incomplete")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()
