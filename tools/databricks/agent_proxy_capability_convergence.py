"""Converge exact managed Supervisor and Genie capability-group access."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from tools.databricks.agent_proxy_acl_support import (
    assert_managed_capability_acl,
    group_entry,
    levels,
    migrate_legacy_direct_acl_if_unmanaged,
    wait_exact_capability_projection,
)
from tools.databricks.agent_proxy_capability_group_access import (
    assert_managed_agent_proxy_members,
    ensure_managed_agent_proxy_group,
    inspect_managed_agent_proxy_group,
    managed_agent_proxy_group_name,
    set_managed_agent_proxy_membership,
)
from tools.databricks.agent_runtime_access import _genie_spaces


def converge_supervisor_agent_acls(
    workspace: Any,
    *,
    agents: dict[str, str],
    reviewed_ids: set[str],
    application_id: str,
    service_principal_id: str,
    audit_only: bool,
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    for agent_id in sorted(agents):
        path = f"/api/2.0/permissions/supervisor-agents/{quote(agent_id, safe='')}"
        group_name = managed_agent_proxy_group_name(
            resource_kind="supervisor",
            resource_id=agent_id,
            application_id=application_id,
        )
        permissions = workspace.api_client.do("GET", path)
        expect_query = agent_id in reviewed_ids
        if audit_only:
            continue
        migrate_legacy_direct_acl_if_unmanaged(
            workspace,
            path=path,
            permissions=permissions,
            resource_kind="supervisor",
            resource_id=agent_id,
            application_id=application_id,
            assert_single_writer=assert_single_writer,
            assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
        )
        permissions = workspace.api_client.do("GET", path)
        if expect_query:
            ensure_managed_agent_proxy_group(
                workspace,
                resource_kind="supervisor",
                resource_id=agent_id,
                application_id=application_id,
                service_principal_id=service_principal_id,
                assert_single_writer=assert_single_writer,
            )
            managed_entry = group_entry(permissions, group_name)
            if (
                levels(managed_entry or {}, direct_only=True) != {"CAN_QUERY"}
                or levels(managed_entry or {}) != {"CAN_QUERY"}
            ):
                assert_single_writer()
                workspace.api_client.do(
                    "PATCH",
                    path,
                    body={
                        "access_control_list": [
                            {
                                "group_name": group_name,
                                "permission_level": "CAN_QUERY",
                            }
                        ]
                    },
                )
            set_managed_agent_proxy_membership(
                workspace,
                resource_kind="supervisor",
                resource_id=agent_id,
                application_id=application_id,
                service_principal_id=service_principal_id,
                active=True,
                assert_single_writer=assert_single_writer,
            )
        else:
            set_managed_agent_proxy_membership(
                workspace,
                resource_kind="supervisor",
                resource_id=agent_id,
                application_id=application_id,
                service_principal_id=service_principal_id,
                active=False,
                assert_single_writer=assert_single_writer,
            )
    effective_group_names = wait_exact_capability_projection(
        workspace,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    for agent_id in sorted(agents):
        group_name = managed_agent_proxy_group_name(
            resource_kind="supervisor",
            resource_id=agent_id,
            application_id=application_id,
        )
        state = inspect_managed_agent_proxy_group(
            workspace,
            resource_kind="supervisor",
            resource_id=agent_id,
            application_id=application_id,
            missing_ok=agent_id not in reviewed_ids,
        )
        if state is not None:
            assert_managed_agent_proxy_members(
                state,
                expected_member_ids=(
                    (service_principal_id,)
                    if agent_id in reviewed_ids
                    else ()
                ),
            )
        permissions = workspace.api_client.do(
            "GET",
            f"/api/2.0/permissions/supervisor-agents/{quote(agent_id, safe='')}",
        )
        assert_managed_capability_acl(
            permissions,
            application_id=application_id,
            effective_group_names=effective_group_names,
            managed_group_name=group_name,
            expect_active=agent_id in reviewed_ids,
            expected_level="CAN_QUERY",
            resource=f"Supervisor {agent_id}",
        )


def converge_genie_acl(
    workspace: Any,
    *,
    genie_space_id: str,
    application_id: str,
    service_principal_id: str,
    audit_only: bool,
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    spaces = _genie_spaces(workspace)
    if genie_space_id not in spaces:
        raise RuntimeError("reviewed Genie space is absent from global inventory")
    for space_id in sorted(spaces):
        path = f"/api/2.0/permissions/genie/{quote(space_id, safe='')}"
        group_name = managed_agent_proxy_group_name(
            resource_kind="genie",
            resource_id=space_id,
            application_id=application_id,
        )
        permissions = workspace.api_client.do("GET", path)
        if audit_only:
            continue
        migrate_legacy_direct_acl_if_unmanaged(
            workspace,
            path=path,
            permissions=permissions,
            resource_kind="genie",
            resource_id=space_id,
            application_id=application_id,
            assert_single_writer=assert_single_writer,
            assert_legacy_cleanup_quiesced=assert_legacy_cleanup_quiesced,
        )
        permissions = workspace.api_client.do("GET", path)
        if space_id == genie_space_id:
            ensure_managed_agent_proxy_group(
                workspace,
                resource_kind="genie",
                resource_id=space_id,
                application_id=application_id,
                service_principal_id=service_principal_id,
                assert_single_writer=assert_single_writer,
            )
            managed_entry = group_entry(permissions, group_name)
            if (
                levels(managed_entry or {}, direct_only=True) != {"CAN_RUN"}
                or levels(managed_entry or {}) != {"CAN_RUN"}
            ):
                assert_single_writer()
                workspace.api_client.do(
                    "PATCH",
                    path,
                    body={
                        "access_control_list": [
                            {
                                "group_name": group_name,
                                "permission_level": "CAN_RUN",
                            }
                        ]
                    },
                )
            set_managed_agent_proxy_membership(
                workspace,
                resource_kind="genie",
                resource_id=space_id,
                application_id=application_id,
                service_principal_id=service_principal_id,
                active=True,
                assert_single_writer=assert_single_writer,
            )
        else:
            set_managed_agent_proxy_membership(
                workspace,
                resource_kind="genie",
                resource_id=space_id,
                application_id=application_id,
                service_principal_id=service_principal_id,
                active=False,
                assert_single_writer=assert_single_writer,
            )
    effective_group_names = wait_exact_capability_projection(
        workspace,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    for space_id in sorted(spaces):
        group_name = managed_agent_proxy_group_name(
            resource_kind="genie",
            resource_id=space_id,
            application_id=application_id,
        )
        state = inspect_managed_agent_proxy_group(
            workspace,
            resource_kind="genie",
            resource_id=space_id,
            application_id=application_id,
            missing_ok=space_id != genie_space_id,
        )
        if state is not None:
            assert_managed_agent_proxy_members(
                state,
                expected_member_ids=(
                    (service_principal_id,)
                    if space_id == genie_space_id
                    else ()
                ),
            )
        permissions = workspace.api_client.do(
            "GET",
            f"/api/2.0/permissions/genie/{quote(space_id, safe='')}",
        )
        assert_managed_capability_acl(
            permissions,
            application_id=application_id,
            effective_group_names=effective_group_names,
            managed_group_name=group_name,
            expect_active=space_id == genie_space_id,
            expected_level="CAN_RUN",
            resource=f"Genie {space_id}",
        )
