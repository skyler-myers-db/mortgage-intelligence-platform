"""Converge and audit the least-privilege managed-Supervisor proxy caller."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_proxy_acl_support import (
    field as _field,
)
from tools.databricks.agent_proxy_acl_support import (
    items as _items,
)
from tools.databricks.agent_proxy_acl_support import (
    text as _text,
)
from tools.databricks.agent_proxy_acl_support import (
    wait_exact_capability_projection as _wait_exact_capability_projection,
)
from tools.databricks.agent_proxy_capability_convergence import (
    converge_genie_acl as _converge_genie_acl,
)
from tools.databricks.agent_proxy_capability_convergence import (
    converge_supervisor_agent_acls as _converge_supervisor_agent_acls,
)
from tools.databricks.agent_proxy_capability_denial import (
    revoke_all_genie_acls as _revoke_all_genie_acls,
)
from tools.databricks.agent_proxy_capability_denial import (
    revoke_all_managed_capability_memberships as _revoke_all_managed_capability_memberships,
)
from tools.databricks.agent_proxy_capability_denial import (
    revoke_all_supervisor_agent_acls as _revoke_all_supervisor_agent_acls,
)
from tools.databricks.agent_runtime_access import (
    assert_runtime_creator,
)
from tools.databricks.audit_global_m2m_access import (
    assert_workspace_admin_inventory_identity,
)
from tools.databricks.deployment_lease_authority import held_assertion_from_env
from tools.databricks.legacy_permissions_acl_cleanup import (
    stopped_deployment_app_assertion,
)
from tools.databricks.m2m_access_policy import resolve_effective_groups
from tools.databricks.serving_endpoint_acl import (
    audit_global_serving_endpoint_access,
    converge_exact_direct_can_query,
    inspect_exact_query_access_mode,
    revoke_all_direct_permissions,
)


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
        query: dict[str, int | str] = {"page_size": 100}
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


def _supervisor_binding(
    workspace: Any,
    *,
    supervisor_id: str,
    supervisor_endpoint: str,
    supervisor_endpoint_id: str,
    runtime_application_id: str,
) -> tuple[str, str, str]:
    target_id = supervisor_id.strip()
    target_endpoint = supervisor_endpoint.strip()
    target_endpoint_id = supervisor_endpoint_id.strip()
    details = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(target_id, safe='')}",
    )
    if (
        _text(_field(details, "supervisor_agent_id")) != target_id
        or _text(_field(details, "endpoint_name")) != target_endpoint
    ):
        raise RuntimeError("configured Supervisor ID and endpoint binding drifted")
    assert_runtime_creator(
        _field(details, "creator"),
        application_id=runtime_application_id,
        resource="Supervisor Agent",
    )
    endpoint = workspace.serving_endpoints.get(target_endpoint)
    if (
        _text(_field(endpoint, "name")) != target_endpoint
        or _text(_field(endpoint, "id")) != target_endpoint_id
    ):
        raise RuntimeError("Supervisor serving endpoint immutable identity drifted")
    canonical_task = _text(_field(endpoint, "task")).lower().replace("-", "_").replace("/", "_")
    if canonical_task != "agent_v1_responses":
        raise RuntimeError("Supervisor serving endpoint is not an Agent Responses endpoint")
    assert_runtime_creator(
        _field(endpoint, "creator"),
        application_id=runtime_application_id,
        resource="Supervisor serving endpoint",
    )
    return target_id, target_endpoint, target_endpoint_id


def _reviewed_bindings(
    workspace: Any,
    *,
    supervisor_id: str,
    supervisor_endpoint: str,
    supervisor_endpoint_id: str,
    runtime_application_id: str,
    preserved_supervisor_bindings: tuple[tuple[str, str, str], ...],
) -> tuple[tuple[str, str, str], ...]:
    requested = (
        (
            supervisor_id.strip(),
            supervisor_endpoint.strip(),
            supervisor_endpoint_id.strip(),
        ),
        *(
            (
                candidate_id.strip(),
                candidate_endpoint.strip(),
                candidate_endpoint_id.strip(),
            )
            for candidate_id, candidate_endpoint, candidate_endpoint_id in (
                preserved_supervisor_bindings
            )
        ),
    )
    if any(
        not candidate_id or not candidate_endpoint or not candidate_endpoint_id
        for candidate_id, candidate_endpoint, candidate_endpoint_id in requested
    ):
        raise ValueError("Supervisor IDs and immutable endpoints are required")
    if len({candidate_id for candidate_id, _endpoint, _endpoint_id in requested}) != len(requested):
        raise ValueError("reviewed Supervisor IDs must be distinct")
    if len({endpoint for _candidate_id, endpoint, _endpoint_id in requested}) != len(requested):
        raise ValueError("reviewed Supervisor endpoints must be distinct")
    if len({endpoint_id for _id, _endpoint, endpoint_id in requested}) != len(requested):
        raise ValueError("reviewed Supervisor endpoint IDs must be distinct")
    return tuple(
        _supervisor_binding(
            workspace,
            supervisor_id=candidate_id,
            supervisor_endpoint=endpoint,
            supervisor_endpoint_id=endpoint_id,
            runtime_application_id=runtime_application_id,
        )
        for candidate_id, endpoint, endpoint_id in requested
    )


def grant_and_audit_agent_proxy_access(
    workspace: Any,
    *,
    supervisor_id: str,
    supervisor_endpoint: str,
    supervisor_endpoint_id: str,
    genie_space_id: str,
    application_id: str,
    runtime_application_id: str,
    expected_inventory_principal: str,
    preserved_supervisor_bindings: tuple[tuple[str, str, str], ...] = (),
    legacy_pinned_supervisor_endpoints: tuple[str, ...] = (),
    audit_only: bool = False,
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    """Converge and prove the exact Supervisor, endpoint, and Genie boundary."""

    target = supervisor_id.strip()
    proxy_id = application_id.strip()
    runtime_id = runtime_application_id.strip()
    inventory_principal = expected_inventory_principal.strip()
    if (
        not target
        or not supervisor_endpoint.strip()
        or not supervisor_endpoint_id.strip()
        or not proxy_id
        or not genie_space_id.strip()
        or not runtime_id
        or not inventory_principal
    ):
        raise ValueError(
            "Supervisor, immutable endpoint, Genie, proxy, runtime, and inventory "
            "identities are required"
        )
    assert_workspace_admin_inventory_identity(
        workspace,
        expected_principal=inventory_principal,
    )
    principal_id = _service_principal_id(workspace, application_id=proxy_id)
    group_names = set(resolve_effective_groups(workspace, sp_id=principal_id).values())
    bindings = _reviewed_bindings(
        workspace,
        supervisor_id=target,
        supervisor_endpoint=supervisor_endpoint,
        supervisor_endpoint_id=supervisor_endpoint_id,
        runtime_application_id=runtime_id,
        preserved_supervisor_bindings=preserved_supervisor_bindings,
    )
    reviewed_ids = {candidate_id for candidate_id, _endpoint, _endpoint_id in bindings}
    reviewed_endpoints = {endpoint for _candidate_id, endpoint, _endpoint_id in bindings}
    requested_legacy_pins = {
        str(endpoint).strip()
        for endpoint in legacy_pinned_supervisor_endpoints
        if str(endpoint).strip()
    }
    if (
        len(requested_legacy_pins) != len(legacy_pinned_supervisor_endpoints)
        or not requested_legacy_pins.issubset(reviewed_endpoints)
    ):
        raise ValueError(
            "legacy-pinned Supervisor endpoints must be a distinct reviewed subset"
        )
    observed_legacy_pins = {
        endpoint
        for endpoint in requested_legacy_pins
        if inspect_exact_query_access_mode(
            workspace,
            endpoint_name=endpoint,
            service_principal=proxy_id,
            service_principal_id=principal_id,
            effective_group_names=group_names,
        )
        in {"direct", "mixed"}
    }
    agents = _supervisor_agents(workspace)
    missing = reviewed_ids.difference(agents)
    if missing:
        raise RuntimeError("reviewed Supervisor Agent is absent from global inventory")
    _converge_supervisor_agent_acls(
        workspace,
        agents=agents,
        reviewed_ids=reviewed_ids,
        application_id=proxy_id,
        service_principal_id=principal_id,
        audit_only=audit_only,
        assert_single_writer=assert_single_writer,
        assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
    )
    _converge_genie_acl(
        workspace,
        genie_space_id=genie_space_id,
        application_id=proxy_id,
        service_principal_id=principal_id,
        audit_only=audit_only,
        assert_single_writer=assert_single_writer,
        assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
    )
    group_names = _wait_exact_capability_projection(
        workspace,
        application_id=proxy_id,
        service_principal_id=principal_id,
    )
    if audit_only:
        audit_global_serving_endpoint_access(
            workspace,
            reviewed_endpoint_names=reviewed_endpoints,
            service_principal=proxy_id,
            expected_permission_level="CAN_QUERY",
            service_principal_id=principal_id,
            effective_group_names=group_names,
            legacy_pinned_endpoint_names=observed_legacy_pins,
        )
    else:
        converge_exact_direct_can_query(
            workspace,
            reviewed_endpoint_names=reviewed_endpoints,
            service_principal=proxy_id,
            service_principal_id=principal_id,
            effective_group_names=group_names,
            legacy_pinned_endpoint_names=observed_legacy_pins,
            assert_single_writer=assert_single_writer,
        )


def revoke_and_audit_agent_proxy_access(
    workspace: Any,
    *,
    application_id: str,
    expected_inventory_principal: str,
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    """Revoke all direct proxy capability and prove the global zero boundary."""

    proxy_id = application_id.strip()
    inventory_principal = expected_inventory_principal.strip()
    if not proxy_id or not inventory_principal:
        raise ValueError("agent-proxy and inventory identities are required")
    assert_workspace_admin_inventory_identity(
        workspace,
        expected_principal=inventory_principal,
    )
    failures: dict[str, str] = {}
    direct_operations = (
        (
            "serving endpoint",
            lambda: revoke_all_direct_permissions(
                workspace,
                service_principal=proxy_id,
                effective_group_names=set(),
                assert_single_writer=assert_single_writer,
            ),
        ),
        (
            "Supervisor",
            lambda: _revoke_all_supervisor_agent_acls(
                workspace,
                agents=_supervisor_agents(workspace),
                application_id=proxy_id,
                service_principal_id="",
                effective_group_names=set(),
                assert_single_writer=assert_single_writer,
                assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
            ),
        ),
        (
            "Genie",
            lambda: _revoke_all_genie_acls(
                workspace,
                application_id=proxy_id,
                service_principal_id="",
                effective_group_names=set(),
                assert_single_writer=assert_single_writer,
                assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
            ),
        ),
    )
    for label, operation in direct_operations:
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - direct denial must attempt every axis
            failures[label] = f"{label} direct-first: {type(exc).__name__}: {exc}"
    principal_id: str | None = None
    group_names: set[str] | None = None
    try:
        principal_id = _service_principal_id(workspace, application_id=proxy_id)
        _revoke_all_managed_capability_memberships(
            workspace,
            application_id=proxy_id,
            service_principal_id=principal_id,
            assert_single_writer=assert_single_writer,
        )
        group_names = _wait_exact_capability_projection(
            workspace,
            application_id=proxy_id,
            service_principal_id=principal_id,
        )
    except Exception as exc:  # noqa: BLE001 - report after every direct revoke was attempted
        failures["identity inventory"] = f"identity inventory: {type(exc).__name__}: {exc}"
    if principal_id is not None and group_names is not None:
        effective_operations = (
            (
                "serving endpoint",
                lambda: revoke_all_direct_permissions(
                    workspace,
                    service_principal=proxy_id,
                    service_principal_id=principal_id,
                    effective_group_names=group_names,
                    assert_single_writer=assert_single_writer,
                ),
            ),
            (
                "Supervisor",
                lambda: _revoke_all_supervisor_agent_acls(
                    workspace,
                    agents=_supervisor_agents(workspace),
                    application_id=proxy_id,
                    service_principal_id=principal_id,
                    effective_group_names=group_names,
                    assert_single_writer=assert_single_writer,
                    assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
                ),
            ),
            (
                "Genie",
                lambda: _revoke_all_genie_acls(
                    workspace,
                    application_id=proxy_id,
                    service_principal_id=principal_id,
                    effective_group_names=group_names,
                    assert_single_writer=assert_single_writer,
                    assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
                ),
            ),
        )
        for label, operation in effective_operations:
            try:
                operation()
                failures.pop(label, None)
            except Exception as exc:  # noqa: BLE001 - denial must attempt every axis
                failures[label] = f"{label}: {type(exc).__name__}: {exc}"
    if failures:
        raise RuntimeError(
            "agent-proxy global denial is unproven: " + "; ".join(failures.values())
        )


def _deployment_lease_assertion(workspace: Any) -> Callable[[], None]:
    return held_assertion_from_env(
        workspace,
        operation="agent-proxy ACL mutation",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("converge", "audit", "deny-all"), default="converge")
    parser.add_argument("--supervisor-id")
    parser.add_argument("--supervisor-endpoint")
    parser.add_argument("--supervisor-endpoint-id")
    parser.add_argument("--preserve-supervisor-id")
    parser.add_argument("--preserve-supervisor-endpoint")
    parser.add_argument("--preserve-supervisor-endpoint-id")
    parser.add_argument("--legacy-pinned-supervisor-endpoint", action="append", default=[])
    parser.add_argument("--genie-space-id")
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--runtime-application-id")
    parser.add_argument("--expected-inventory-principal", required=True)
    args = parser.parse_args(argv)
    workspace = WorkspaceClient()
    assert_single_writer = _deployment_lease_assertion(workspace)
    assert_legacy_cleanup_quiesced = stopped_deployment_app_assertion(workspace)
    if args.mode == "deny-all":
        if args.legacy_pinned_supervisor_endpoint:
            parser.error("--legacy-pinned-supervisor-endpoint is invalid with deny-all")
        revoke_and_audit_agent_proxy_access(
            workspace,
            application_id=args.application_id,
            expected_inventory_principal=args.expected_inventory_principal,
            assert_single_writer=assert_single_writer,
            assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
        )
        print("[agent-proxy] global Supervisor, Genie, and serving denial: PASS")
        return 0
    required = (
        args.supervisor_id,
        args.supervisor_endpoint,
        args.supervisor_endpoint_id,
        args.genie_space_id,
        args.runtime_application_id,
    )
    if not all(str(value or "").strip() for value in required):
        parser.error(
            "converge/audit requires --supervisor-id, --supervisor-endpoint, "
            "--supervisor-endpoint-id, --genie-space-id, and --runtime-application-id"
        )
    preserve_values = (
        str(args.preserve_supervisor_id or "").strip(),
        str(args.preserve_supervisor_endpoint or "").strip(),
        str(args.preserve_supervisor_endpoint_id or "").strip(),
    )
    if len({bool(value) for value in preserve_values}) != 1:
        parser.error(
            "--preserve-supervisor-id, --preserve-supervisor-endpoint, and "
            "--preserve-supervisor-endpoint-id are required together"
        )
    grant_and_audit_agent_proxy_access(
        workspace,
        supervisor_id=args.supervisor_id,
        supervisor_endpoint=args.supervisor_endpoint,
        supervisor_endpoint_id=args.supervisor_endpoint_id,
        genie_space_id=args.genie_space_id,
        application_id=args.application_id,
        runtime_application_id=args.runtime_application_id,
        expected_inventory_principal=args.expected_inventory_principal,
        preserved_supervisor_bindings=((preserve_values,) if preserve_values[0] else ()),
        legacy_pinned_supervisor_endpoints=tuple(args.legacy_pinned_supervisor_endpoint),
        audit_only=args.mode == "audit",
        assert_single_writer=assert_single_writer,
        assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
    )
    print("[agent-proxy] exact Supervisor, endpoint, and Genie ACL boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
