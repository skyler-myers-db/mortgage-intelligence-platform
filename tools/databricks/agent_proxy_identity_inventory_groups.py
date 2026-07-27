"""Exact managed workspace-group inventory for agent-proxy identity proofs."""

from __future__ import annotations

from typing import Any

from tools.databricks.agent_proxy_capability_group_access import (
    MANAGED_AGENT_PROXY_GROUP_EXTERNAL_ID_PREFIX,
    MANAGED_AGENT_PROXY_GROUP_PREFIX,
    ManagedAgentProxyResourceKind,
    assert_managed_agent_proxy_group_binding_contract,
    managed_agent_proxy_group_external_id,
    managed_agent_proxy_group_name,
)
from tools.databricks.identity_boundary_probes import (
    ManagedWorkspaceGroupBinding,
    collect_managed_workspace_group_bindings,
)
from tools.databricks.serving_query_group_access import (
    MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX,
    MANAGED_QUERY_GROUP_PREFIX,
)


def collect_managed_proxy_workspace_groups(
    workspace: Any,
) -> tuple[ManagedWorkspaceGroupBinding, ...]:
    """Bind serving and agent-capability groups on the workspace SCIM plane."""

    bindings = collect_managed_workspace_group_bindings(
        workspace,
        prefix_contracts=(
            (
                MANAGED_QUERY_GROUP_PREFIX,
                MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX,
            ),
            (
                MANAGED_AGENT_PROXY_GROUP_PREFIX,
                MANAGED_AGENT_PROXY_GROUP_EXTERNAL_ID_PREFIX,
            ),
        ),
    )
    for binding in bindings:
        if binding.name.startswith(MANAGED_AGENT_PROXY_GROUP_PREFIX):
            assert_managed_agent_proxy_group_binding_contract(
                name=binding.name,
                external_id=binding.external_id,
            )
    return bindings


def reviewed_agent_proxy_capability_group_bindings(
    managed_groups: tuple[ManagedWorkspaceGroupBinding, ...],
    *,
    reviewed_supervisor_bindings: tuple[tuple[str, str, str], ...],
    genie_space_id: str,
    expected_application_id: str,
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Bind exact Supervisor and Genie capability groups for positive proof."""

    resources: tuple[tuple[ManagedAgentProxyResourceKind, str], ...] = (
        *(
            ("supervisor", candidate_id)
            for candidate_id, _name, _id in reviewed_supervisor_bindings
        ),
        ("genie", genie_space_id),
    )
    reviewed: list[tuple[str, str, str, str, str]] = []
    for resource_kind, resource_id in resources:
        expected_name = managed_agent_proxy_group_name(
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=expected_application_id,
        )
        expected_external_id = managed_agent_proxy_group_external_id(
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=expected_application_id,
        )
        matches = tuple(
            group
            for group in managed_groups
            if group.name == expected_name
            and group.external_id == expected_external_id
        )
        if len(matches) != 1:
            raise RuntimeError(
                "reviewed managed agent-proxy capability group contract drifted"
            )
        reviewed.append(
            (
                resource_kind,
                resource_id,
                matches[0].name,
                matches[0].id,
                matches[0].external_id,
            )
        )
    return tuple(reviewed)
