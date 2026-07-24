"""Converge and audit the least-privilege managed-Supervisor proxy caller."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_access import (
    audit_global_genie_access,
    grant_and_verify_genie_can_run,
)
from tools.databricks.m2m_access_policy import resolve_effective_groups
from tools.databricks.serving_endpoint_acl import (
    audit_global_no_serving_endpoint_access,
)


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _items(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("Supervisor Agent ACL inventory is malformed")
    return value


def _levels(entry: object, *, direct_only: bool = False) -> set[str]:
    return {
        _text(_field(permission, "permission_level")).upper()
        for permission in _items(_field(entry, "all_permissions"))
        if not direct_only or _field(permission, "inherited") is not True
    }


def _principal_entry(permissions: object, application_id: str) -> object | None:
    for entry in _items(_field(permissions, "access_control_list")):
        if _text(_field(entry, "service_principal_name")) == application_id:
            return entry
    return None


def _service_principal_id(workspace: Any, *, application_id: str) -> str:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        item
        for item in workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if _text(_field(item, "application_id")) == application_id
    ]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one agent-proxy service principal")
    principal_id = _text(_field(matches[0], "id"))
    if not principal_id:
        raise RuntimeError("agent-proxy service principal has no immutable SCIM id")
    return principal_id


def _supervisor_agents(workspace: Any) -> dict[str, str]:
    agents: dict[str, str] = {}
    page_token: str | None = None
    seen: set[str] = set()
    while True:
        query = {"page_size": 100}
        if page_token:
            query["page_token"] = page_token
        response = workspace.api_client.do(
            "GET",
            "/api/2.1/supervisor-agents",
            query=query,
        )
        if not isinstance(response, Mapping):
            raise RuntimeError("Supervisor Agent inventory is malformed")
        for item in _items(response.get("supervisor_agents")):
            agent_id = _text(_field(item, "supervisor_agent_id"))
            display_name = _text(_field(item, "display_name"))
            if not agent_id or agent_id in agents:
                raise RuntimeError("Supervisor Agent inventory has an invalid identity")
            agents[agent_id] = display_name
        page_token = _text(response.get("next_page_token")) or None
        if page_token is None:
            return agents
        if page_token in seen:
            raise RuntimeError("Supervisor Agent inventory pagination cycled")
        seen.add(page_token)


def _assert_agent_acl(
    permissions: object,
    *,
    application_id: str,
    effective_group_names: set[str],
    expect_query: bool,
) -> None:
    entry = _principal_entry(permissions, application_id)
    direct = _levels(entry or {}, direct_only=True)
    effective = _levels(entry or {})
    expected = {"CAN_QUERY"} if expect_query else set()
    if direct != expected or effective != expected:
        raise RuntimeError("agent-proxy Supervisor Agent ACL postflight failed")
    inherited_groups = {
        _text(_field(candidate, "group_name"))
        for candidate in _items(_field(permissions, "access_control_list"))
        if _text(_field(candidate, "group_name")) in effective_group_names and _levels(candidate)
    }
    if inherited_groups:
        raise RuntimeError("agent-proxy has inherited Supervisor Agent access")


def grant_and_audit_agent_proxy_access(
    workspace: Any,
    *,
    supervisor_id: str,
    genie_space_id: str,
    application_id: str,
) -> None:
    """Grant the reviewed agent/Genie boundary and deny every serving endpoint."""

    target = supervisor_id.strip()
    proxy_id = application_id.strip()
    if not target or not proxy_id or not genie_space_id.strip():
        raise ValueError("Supervisor, Genie, and agent-proxy identities are required")
    principal_id = _service_principal_id(workspace, application_id=proxy_id)
    group_names = set(resolve_effective_groups(workspace, sp_id=principal_id).values())
    agents = _supervisor_agents(workspace)
    if target not in agents:
        raise RuntimeError("reviewed Supervisor Agent is absent from global inventory")
    for agent_id in sorted(set(agents) - {target}):
        path = f"/api/2.0/permissions/supervisor-agents/{quote(agent_id, safe='')}"
        permissions = workspace.api_client.do("GET", path)
        entry = _principal_entry(permissions, proxy_id)
        if _levels(entry or {}, direct_only=True):
            workspace.api_client.do(
                "PATCH",
                path,
                body={
                    "access_control_list": [
                        {
                            "service_principal_name": proxy_id,
                            "permission_level": "NO_PERMISSIONS",
                        }
                    ]
                },
            )

    path = f"/api/2.0/permissions/supervisor-agents/{quote(target, safe='')}"
    workspace.api_client.do(
        "PATCH",
        path,
        body={
            "access_control_list": [
                {
                    "service_principal_name": proxy_id,
                    "permission_level": "CAN_QUERY",
                }
            ]
        },
    )
    grant_and_verify_genie_can_run(
        workspace,
        genie_space_id=genie_space_id,
        application_id=proxy_id,
        service_principal_id=principal_id,
        effective_group_names=group_names,
    )

    for agent_id in sorted(agents):
        permissions = workspace.api_client.do(
            "GET",
            f"/api/2.0/permissions/supervisor-agents/{quote(agent_id, safe='')}",
        )
        _assert_agent_acl(
            permissions,
            application_id=proxy_id,
            effective_group_names=group_names,
            expect_query=agent_id == target,
        )
    audit_global_genie_access(
        workspace,
        reviewed_genie_space_id=genie_space_id,
        application_id=proxy_id,
        service_principal_id=principal_id,
        effective_group_names=group_names,
    )
    audit_global_no_serving_endpoint_access(
        workspace,
        service_principal=proxy_id,
        service_principal_id=principal_id,
        effective_group_names=group_names,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supervisor-id", required=True)
    parser.add_argument("--genie-space-id", required=True)
    parser.add_argument("--application-id", required=True)
    args = parser.parse_args(argv)
    grant_and_audit_agent_proxy_access(
        WorkspaceClient(),
        supervisor_id=args.supervisor_id,
        genie_space_id=args.genie_space_id,
        application_id=args.application_id,
    )
    print("[agent-proxy] exact Supervisor, Genie, and serving ACL boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
