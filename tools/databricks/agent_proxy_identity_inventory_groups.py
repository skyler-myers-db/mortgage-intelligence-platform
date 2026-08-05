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
from tools.databricks.serving_endpoint_legacy_query import (
    inspect_legacy_pre_provenance_group,
)
from tools.databricks.serving_query_group_access import (
    MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX,
    MANAGED_QUERY_GROUP_PREFIX,
    inspect_claimed_managed_query_group,
)
from tools.databricks.serving_query_group_provenance import (
    INTENT_EXTERNAL_ID_PREFIX,
    MissingClaimedGroupProvenanceError,
)
from tools.databricks.uc_target_identity import workspace_target_identity


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
                MANAGED_QUERY_GROUP_PREFIX,
                INTENT_EXTERNAL_ID_PREFIX,
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


def reviewed_agent_proxy_query_group_bindings(
    workspace: Any,
    *,
    app_name: str,
    managed_groups: tuple[ManagedWorkspaceGroupBinding, ...],
    reviewed_supervisor_bindings: tuple[tuple[str, str, str], ...],
    expected_application_id: str,
) -> tuple[tuple[str, str, str, str], ...]:
    """Bind active signed and explicitly preserved v1 query groups exactly."""

    proxy_scim_id = workspace_target_identity(
        workspace,
        application_id=expected_application_id,
    ).scim_id
    reviewed: list[tuple[str, str, str, str]] = []
    for index, (_candidate_id, _endpoint_name, endpoint_id) in enumerate(
        reviewed_supervisor_bindings
    ):
        try:
            state = inspect_claimed_managed_query_group(
                workspace,
                app_name=app_name,
                endpoint_id=endpoint_id,
                application_id=expected_application_id,
                service_principal_id=proxy_scim_id,
            )
        except MissingClaimedGroupProvenanceError:
            if index == 0:
                raise
            state = inspect_legacy_pre_provenance_group(
                workspace,
                endpoint_id=endpoint_id,
                application_id=expected_application_id,
                service_principal_id=proxy_scim_id,
            )
        assert state is not None
        if state.member_ids != (proxy_scim_id,):
            raise RuntimeError(
                "reviewed managed serving-query group lacks its exact proxy member"
            )
        matches = tuple(
            group
            for group in managed_groups
            if (
                group.id,
                group.name,
                group.external_id,
            )
            == (
                state.contract.id,
                state.contract.name,
                state.contract.external_id,
            )
        )
        if len(matches) != 1:
            raise RuntimeError(
                "reviewed managed serving-query group contract drifted"
            )
        matched = matches[0]
        reviewed.append(
            (endpoint_id, matched.name, matched.id, matched.external_id)
        )
    return tuple(reviewed)
