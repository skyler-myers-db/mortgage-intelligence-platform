#!/usr/bin/env python3
"""Verify agent-proxy UC access with metastore-owner and proxy authority."""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_proxy_capability_group_access import (
    inspect_managed_agent_proxy_group,
)
from tools.databricks.agent_runtime_uc_inventory import _text
from tools.databricks.audit_agent_runtime_foreign_uc_access import (
    audit_foreign_uc_access,
)
from tools.databricks.serving_query_group_access import (
    inspect_managed_query_group,
)
from tools.databricks.uc_owner_policy import account_client_from_env
from tools.databricks.uc_target_identity import (
    account_target_identity,
    workspace_target_identity,
)
from tools.databricks.verify_agent_proxy_uc_grants import (
    verify_effective_agent_proxy_uc_boundary,
)

_AMBIENT_AUTH_KEYS = (
    "DATABRICKS_ACCOUNT_CLIENT_ID",
    "DATABRICKS_ACCOUNT_CLIENT_SECRET",
    "DATABRICKS_ACCOUNT_HOST",
    "DATABRICKS_ACCOUNT_ID",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_PASSWORD",
    "DATABRICKS_TOKEN",
    "DATABRICKS_USERNAME",
)


@dataclass(frozen=True)
class ReviewedWorkspaceCapabilityGroup:
    resource_plane: str
    resource_kind: str
    resource_id: str
    group_id: str
    group_name: str
    group_external_id: str
    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReviewedProxyCapabilityAttestation:
    application_id: str
    workspace_principal_scim_id: str
    account_principal_scim_id: str
    groups: tuple[ReviewedWorkspaceCapabilityGroup, ...]

    def allowed_workspace_groups(self) -> dict[str, str]:
        """Return only exact active groups from this frozen attestation."""

        return {
            group.group_id: group.group_name
            for group in self.groups
            if group.member_ids == (self.workspace_principal_scim_id,)
        }


def _reviewed_workspace_capability_groups(
    workspace: Any,
    *,
    account: Any,
    application_id: str,
    supervisor_ids: Iterable[str],
    supervisor_endpoint_ids: Iterable[str],
    genie_space_id: str,
) -> ReviewedProxyCapabilityAttestation:
    """Bind the proxy's only admissible ordinary groups to exact resources."""

    reviewed_supervisor_ids = tuple(value.strip() for value in supervisor_ids)
    reviewed_endpoint_ids = tuple(value.strip() for value in supervisor_endpoint_ids)
    genie_id = genie_space_id.strip()
    if (
        not genie_id
        or not reviewed_supervisor_ids
        or len(reviewed_supervisor_ids) != len(reviewed_endpoint_ids)
        or any(not value for value in (*reviewed_supervisor_ids, *reviewed_endpoint_ids))
        or len(set(reviewed_supervisor_ids)) != len(reviewed_supervisor_ids)
        or len(set(reviewed_endpoint_ids)) != len(reviewed_endpoint_ids)
    ):
        raise ValueError("reviewed Supervisor IDs, endpoint IDs, and Genie space ID are required")
    workspace_principal_id = workspace_target_identity(
        workspace,
        application_id=application_id,
    ).scim_id
    account_principal_id, _account_display_name = account_target_identity(
        account,
        application_id=application_id,
    )
    groups: list[ReviewedWorkspaceCapabilityGroup] = []
    group_ids: set[str] = set()
    group_names: set[str] = set()

    def add(*, resource_kind: str, resource_id: str, state: Any) -> None:
        contract = state.contract
        group_id = contract.id
        group_name = contract.name
        canonical_name = group_name.casefold()
        if (
            not group_id
            or not group_name
            or not contract.external_id
            or group_id in group_ids
            or canonical_name in group_names
        ):
            raise RuntimeError("reviewed agent-proxy capability groups are ambiguous")
        group_ids.add(group_id)
        group_names.add(canonical_name)
        groups.append(
            ReviewedWorkspaceCapabilityGroup(
                resource_plane="workspace_scim",
                resource_kind=resource_kind,
                resource_id=resource_id,
                group_id=group_id,
                group_name=group_name,
                group_external_id=contract.external_id,
                member_ids=state.member_ids,
            )
        )

    for supervisor_id, endpoint_id in zip(
        reviewed_supervisor_ids,
        reviewed_endpoint_ids,
        strict=True,
    ):
        capability = inspect_managed_agent_proxy_group(
            workspace,
            resource_kind="supervisor",
            resource_id=supervisor_id,
            application_id=application_id,
        )
        query = inspect_managed_query_group(
            workspace,
            endpoint_id=endpoint_id,
            application_id=application_id,
            missing_ok=True,
        )
        if capability is None:
            raise RuntimeError("reviewed agent-proxy Supervisor group is missing")
        if capability.member_ids != (workspace_principal_id,):
            raise RuntimeError(
                "reviewed agent-proxy Supervisor group does not have the exact member"
            )
        add(resource_kind="supervisor", resource_id=supervisor_id, state=capability)
        if query is not None:
            if query.member_ids not in {(), (workspace_principal_id,)}:
                raise RuntimeError("reviewed agent-proxy query group has an unrelated member")
            add(resource_kind="serving_endpoint", resource_id=endpoint_id, state=query)
    genie = inspect_managed_agent_proxy_group(
        workspace,
        resource_kind="genie",
        resource_id=genie_id,
        application_id=application_id,
    )
    if genie is None:
        raise RuntimeError("reviewed agent-proxy Genie group is missing")
    if genie.member_ids != (workspace_principal_id,):
        raise RuntimeError("reviewed agent-proxy Genie group does not have the exact member")
    add(resource_kind="genie", resource_id=genie_id, state=genie)
    return ReviewedProxyCapabilityAttestation(
        application_id=application_id,
        workspace_principal_scim_id=workspace_principal_id,
        account_principal_scim_id=account_principal_id,
        groups=tuple(groups),
    )


def _bind_proxy_auth_environment(
    *,
    admin_workspace: Any,
    application_id: str,
) -> None:
    expected_id = application_id.strip()
    configured_id = os.environ.get("DATABRICKS_AGENT_PROXY_CLIENT_ID", "").strip()
    client_secret = os.environ.get(
        "DATABRICKS_AGENT_PROXY_CLIENT_SECRET",
        "",
    ).strip()
    host = _text(getattr(getattr(admin_workspace, "config", None), "host", None))
    if not expected_id or configured_id != expected_id or not client_secret or not host:
        raise RuntimeError(
            "dual-authority UC audit lacks exact agent-proxy OAuth credentials or host"
        )
    for name in _AMBIENT_AUTH_KEYS:
        os.environ.pop(name, None)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_AUTH_TYPE"] = "oauth-m2m"
    os.environ["DATABRICKS_CLIENT_ID"] = configured_id
    os.environ["DATABRICKS_CLIENT_SECRET"] = client_secret
    os.environ["MIP_DISABLE_DOTENV"] = "1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--supervisor-id", action="append", default=[])
    parser.add_argument("--supervisor-endpoint-id", action="append", default=[])
    parser.add_argument("--genie-space-id", default="")
    parser.add_argument(
        "--foreign-catalog-binding-policy-json",
        default=os.environ.get("MIP_UC_FOREIGN_CATALOG_BINDING_POLICY", ""),
    )
    args = parser.parse_args(argv)

    admin_workspace = WorkspaceClient()
    account_client = account_client_from_env()
    reviewed_capability_attestation = _reviewed_workspace_capability_groups(
        admin_workspace,
        account=account_client,
        application_id=args.application_id,
        supervisor_ids=args.supervisor_id,
        supervisor_endpoint_ids=args.supervisor_endpoint_id,
        genie_space_id=args.genie_space_id,
    )
    allowed_workspace_groups = reviewed_capability_attestation.allowed_workspace_groups()
    proof = audit_foreign_uc_access(
        admin_workspace,
        application_id=args.application_id,
        catalog=args.catalog,
        expected_inventory_principal=args.expected_inventory_principal,
        foreign_catalog_binding_policy=args.foreign_catalog_binding_policy_json,
        account_factory=lambda: account_client,
        allowed_workspace_groups=allowed_workspace_groups,
    )
    _bind_proxy_auth_environment(
        admin_workspace=admin_workspace,
        application_id=args.application_id,
    )
    proxy_workspace = WorkspaceClient()
    verify_effective_agent_proxy_uc_boundary(
        proxy_workspace,
        application_id=args.application_id,
        catalog=args.catalog,
        foreign_control_plane_proof=proof,
    )
    final_proof = audit_foreign_uc_access(
        admin_workspace,
        application_id=args.application_id,
        catalog=args.catalog,
        expected_inventory_principal=args.expected_inventory_principal,
        foreign_catalog_binding_policy=args.foreign_catalog_binding_policy_json,
        account_factory=lambda: account_client,
        allowed_workspace_groups=allowed_workspace_groups,
    )
    if (
        _reviewed_workspace_capability_groups(
            admin_workspace,
            account=account_client,
            application_id=args.application_id,
            supervisor_ids=args.supervisor_id,
            supervisor_endpoint_ids=args.supervisor_endpoint_id,
            genie_space_id=args.genie_space_id,
        )
        != reviewed_capability_attestation
    ):
        raise RuntimeError("reviewed agent-proxy capability groups changed during UC audit")
    if final_proof != proof:
        raise RuntimeError("foreign-catalog proof changed during agent-proxy audit")
    print("agent-proxy dual-authority effective UC boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
