"""Global managed-capability denial for the dedicated agent-proxy identity."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from tools.databricks.agent_proxy_acl_support import (
    assert_managed_capability_acl,
    levels,
    migrate_legacy_direct_acl_if_unmanaged,
    principal_entries,
)
from tools.databricks.agent_proxy_capability_group_access import (
    MANAGED_AGENT_PROXY_GROUP_PREFIX,
    assert_managed_agent_proxy_members,
    inspect_managed_agent_proxy_group,
    managed_agent_proxy_group_name,
    managed_agent_proxy_groups_for_application,
    remove_managed_agent_proxy_membership,
)
from tools.databricks.agent_runtime_access import (
    _genie_spaces,
    audit_global_no_genie_access,
)
from tools.databricks.m2m_access_policy import resolve_effective_groups

_DENIAL_DEADLINE_SECONDS = 120.0
_DENIAL_POLL_SECONDS = 2.0
_STABLE_ZERO_DENIAL_OBSERVATIONS = 3


def revoke_all_managed_capability_memberships(
    workspace: Any,
    *,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None],
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = _DENIAL_DEADLINE_SECONDS,
) -> None:
    if deadline_seconds <= 0:
        raise ValueError("managed capability denial deadline must be positive")
    deadline = clock() + deadline_seconds
    stable_zero = 0
    while True:
        states = managed_agent_proxy_groups_for_application(
            workspace,
            application_id=application_id,
            service_principal_id=service_principal_id,
        )
        saw_active = any(state.member_ids for state in states)
        errors: list[str] = []
        for state in states:
            try:
                remove_managed_agent_proxy_membership(
                    workspace,
                    state=state,
                    service_principal_id=service_principal_id,
                    assert_single_writer=assert_single_writer,
                )
            except Exception as exc:  # noqa: BLE001 - attempt every independent revoke
                errors.append(f"{state.contract.id}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError(
                "managed agent-proxy membership denial did not converge: "
                + "; ".join(errors)
            )
        effective_managed_names = {
            name
            for name in resolve_effective_groups(
                workspace,
                sp_id=service_principal_id,
            ).values()
            if name.startswith(MANAGED_AGENT_PROXY_GROUP_PREFIX)
        }
        if not saw_active and not effective_managed_names:
            stable_zero += 1
            if stable_zero >= _STABLE_ZERO_DENIAL_OBSERVATIONS:
                return
        else:
            stable_zero = 0
        if clock() >= deadline:
            raise RuntimeError(
                "managed agent-proxy membership denial did not converge"
            )
        sleep(_DENIAL_POLL_SECONDS)


def revoke_all_supervisor_agent_acls(
    workspace: Any,
    *,
    agents: dict[str, str],
    application_id: str,
    service_principal_id: str,
    effective_group_names: set[str],
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    errors: list[str] = []
    for agent_id in sorted(agents):
        path = f"/api/2.0/permissions/supervisor-agents/{quote(agent_id, safe='')}"
        try:
            permissions = workspace.api_client.do("GET", path)
            entries = principal_entries(permissions, application_id)
            if any(levels(entry, direct_only=True) for entry in entries):
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
        except Exception as exc:  # noqa: BLE001 - attempt every independent revoke
            errors.append(f"Supervisor {agent_id}: {type(exc).__name__}: {exc}")
    for agent_id in sorted(agents):
        try:
            state = inspect_managed_agent_proxy_group(
                workspace,
                resource_kind="supervisor",
                resource_id=agent_id,
                application_id=application_id,
                missing_ok=True,
            )
            if state is not None and service_principal_id:
                assert_managed_agent_proxy_members(
                    state,
                    expected_member_ids=(),
                )
            permissions = workspace.api_client.do(
                "GET",
                f"/api/2.0/permissions/supervisor-agents/{quote(agent_id, safe='')}",
            )
            assert_managed_capability_acl(
                permissions,
                application_id=application_id,
                effective_group_names=effective_group_names,
                managed_group_name=managed_agent_proxy_group_name(
                    resource_kind="supervisor",
                    resource_id=agent_id,
                    application_id=application_id,
                ),
                expect_active=False,
                expected_level="CAN_QUERY",
                resource=f"Supervisor {agent_id}",
            )
        except Exception as exc:  # noqa: BLE001 - collect the complete postflight
            errors.append(
                f"Supervisor postflight {agent_id}: {type(exc).__name__}: {exc}"
            )
    if errors:
        raise RuntimeError(
            "Supervisor deny-all did not converge: " + "; ".join(errors)
        )


def revoke_all_genie_acls(
    workspace: Any,
    *,
    application_id: str,
    service_principal_id: str,
    effective_group_names: set[str],
    assert_single_writer: Callable[[], None],
    assert_legacy_cleanup_quiesced: Callable[[], None],
) -> None:
    errors: list[str] = []
    spaces = _genie_spaces(workspace)
    for space_id in sorted(spaces):
        path = f"/api/2.0/permissions/genie/{quote(space_id, safe='')}"
        try:
            permissions = workspace.api_client.do("GET", path)
            entries = principal_entries(permissions, application_id)
            if any(levels(entry, direct_only=True) for entry in entries):
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
        except Exception as exc:  # noqa: BLE001 - attempt every independent revoke
            errors.append(f"Genie {space_id}: {type(exc).__name__}: {exc}")
    for space_id in sorted(spaces):
        try:
            permissions = workspace.api_client.do(
                "GET",
                f"/api/2.0/permissions/genie/{quote(space_id, safe='')}",
            )
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
                missing_ok=True,
            )
            if state is not None and service_principal_id:
                assert_managed_agent_proxy_members(
                    state,
                    expected_member_ids=(),
                )
            assert_managed_capability_acl(
                permissions,
                application_id=application_id,
                effective_group_names=effective_group_names,
                managed_group_name=group_name,
                expect_active=False,
                expected_level="CAN_RUN",
                resource=f"Genie {space_id}",
            )
        except Exception as exc:  # noqa: BLE001 - collect the complete postflight
            errors.append(
                f"Genie duplicate postflight {space_id}: {type(exc).__name__}: {exc}"
            )
    try:
        audit_global_no_genie_access(
            workspace,
            application_id=application_id,
            service_principal_id=service_principal_id,
            effective_group_names=effective_group_names,
        )
    except Exception as exc:  # noqa: BLE001 - include postflight with mutation errors
        errors.append(f"Genie postflight: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError("Genie deny-all did not converge: " + "; ".join(errors))
