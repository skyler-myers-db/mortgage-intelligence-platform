"""Audit one M2M identity across Genie and customer-created serving resources."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Collection
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_access import (
    audit_global_genie_access,
    audit_global_no_genie_access,
)
from tools.databricks.identity_boundary_probes import (
    ManagedWorkspaceGroupBinding,
    managed_workspace_group_binding,
    probe_target_managed_query_group_administration_boundary,
)
from tools.databricks.oauth_credential_boundary import (
    held_deployment_credential_assertion,
)
from tools.databricks.serving_endpoint_acl import (
    audit_global_no_serving_endpoint_access,
    audit_global_serving_endpoint_access,
    is_platform_foundation_endpoint,
)
from tools.databricks.serving_query_group_access import (
    inspect_claimed_managed_query_group,
    inspect_managed_query_group,
    managed_query_group_name,
)
from tools.databricks.serving_query_group_governance import (
    assert_legacy_managed_query_group_administration_isolated,
    assert_managed_query_group_administration_isolated,
)
from tools.databricks.serving_query_group_provenance import (
    MissingClaimedGroupProvenanceError,
)
from tools.databricks.uc_owner_policy import account_client_from_env


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def assert_workspace_admin_inventory_identity(
    workspace: object,
    *,
    expected_principal: str,
) -> None:
    """Fail unless the ambient caller is the reviewed full-inventory admin."""

    expected = expected_principal.strip().casefold()
    actual_principal = workspace_admin_inventory_principal(workspace)
    if not expected or actual_principal.casefold() != expected:
        raise RuntimeError("global M2M inventory audit is running as an unexpected principal")


def workspace_admin_inventory_principal(workspace: object) -> str:
    """Return the ambient principal only when it has full inventory authority."""

    current = workspace.current_user.me()  # type: ignore[attr-defined]
    actual = str(_field(current, "user_name") or "").strip()
    groups = {
        str(_field(group, "display") or "").strip().casefold()
        for group in _items(_field(current, "groups"))
    }
    if not actual:
        raise RuntimeError("global M2M inventory audit could not identify its principal")
    if "admins" not in groups:
        raise RuntimeError("global M2M inventory audit requires a workspace-admin identity")
    return actual


def _service_principal_scim_id(workspace: object, *, application_id: str) -> str:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        principal
        for principal in workspace.service_principals.list(  # type: ignore[attr-defined]
            filter=f'applicationId eq "{escaped}"'
        )
        if str(_field(principal, "application_id") or "") == application_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "managed-group governance requires exactly one service-principal identity"
        )
    principal_id = str(_field(matches[0], "id") or "").strip()
    if not principal_id:
        raise RuntimeError(
            "managed-group governance service principal has no immutable SCIM id"
        )
    return principal_id


def _account_service_principal_scim_id(
    account: object,
    *,
    application_id: str,
    expected_scim_id: str,
) -> str:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        principal
        for principal in account.service_principals.list(  # type: ignore[attr-defined]
            filter=f'applicationId eq "{escaped}"'
        )
        if str(_field(principal, "application_id") or "") == application_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "managed-group governance requires exactly one account service-principal "
            "identity"
        )
    principal_id = str(_field(matches[0], "id") or "").strip()
    if not principal_id or principal_id != expected_scim_id:
        raise RuntimeError(
            "managed-group governance service principal did not resolve identically "
            "across workspace and account SCIM"
        )
    return principal_id


def _audit_managed_query_group_governance(
    workspace: object,
    *,
    app_name: str,
    account_id: str | None,
    application_id: str,
    legacy_pinned_endpoint_names: Collection[str] = (),
    assert_single_writer: Callable[[], None],
    account_factory: Callable[[], Any] = account_client_from_env,
    effective_group_probe: Callable[..., dict[str, str]] = (
        probe_target_managed_query_group_administration_boundary
    ),
) -> tuple[str, dict[str, str]]:
    """Prove one target identity and govern every endpoint-bound group.

    The target credential's own ``/Me`` groups projection is authoritative for
    every downstream serving and Genie reachability audit. This proof therefore
    runs even when no managed query group exists. Managed-group administration
    denial remains an additional proof for every live endpoint-bound group.
    """

    if not (account_id or "").strip():
        raise RuntimeError(
            "target-credential access governance requires the Databricks account id"
        )
    principal_id = _service_principal_scim_id(
        workspace,
        application_id=application_id,
    )
    legacy_pinned = {
        str(name).strip() for name in legacy_pinned_endpoint_names if str(name).strip()
    }
    if len(legacy_pinned) != len(legacy_pinned_endpoint_names):
        raise ValueError("legacy-pinned endpoint names must be non-empty and distinct")
    managed_groups_by_endpoint: dict[str, ManagedWorkspaceGroupBinding] = {}
    legacy_endpoint_ids: set[str] = set()
    seen_names: set[str] = set()
    seen_endpoint_ids: set[str] = set()
    summaries = tuple(workspace.serving_endpoints.list())  # type: ignore[attr-defined]
    if len(summaries) > 1000:
        raise RuntimeError("managed-group serving-endpoint inventory is unbounded")
    for summary in summaries:
        endpoint_name = str(_field(summary, "name") or "").strip()
        if not endpoint_name or endpoint_name in seen_names:
            raise RuntimeError(
                "managed-group serving-endpoint inventory has a missing or duplicate name"
            )
        seen_names.add(endpoint_name)
        endpoint = workspace.serving_endpoints.get(endpoint_name)  # type: ignore[attr-defined]
        endpoint_id = str(_field(endpoint, "id") or "").strip()
        if not endpoint_id:
            if is_platform_foundation_endpoint(endpoint):
                continue
            raise RuntimeError(f"serving endpoint {endpoint_name!r} has no immutable id")
        if (
            str(_field(endpoint, "name") or "").strip() != endpoint_name
            or endpoint_id in seen_endpoint_ids
        ):
            raise RuntimeError(
                "managed-group serving-endpoint immutable identity is ambiguous"
            )
        seen_endpoint_ids.add(endpoint_id)
        group_name = managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        if endpoint_name in legacy_pinned:
            state = inspect_managed_query_group(
                workspace,
                endpoint_id=endpoint_id,
                application_id=application_id,
            )
            legacy_endpoint_ids.add(endpoint_id)
        else:
            try:
                state = inspect_claimed_managed_query_group(
                    workspace,
                    app_name=app_name,
                    endpoint_id=endpoint_id,
                    application_id=application_id,
                    service_principal_id=principal_id,
                    missing_ok=True,
                )
            except MissingClaimedGroupProvenanceError:
                permissions = workspace.serving_endpoints.get_permissions(  # type: ignore[attr-defined]
                    endpoint_id
                )
                acl_matches = [
                    entry
                    for entry in _items(_field(permissions, "access_control_list"))
                    if str(_field(entry, "group_name") or "").strip() == group_name
                ]
                if acl_matches:
                    raise MissingClaimedGroupProvenanceError(
                        "permission-bearing managed serving-query group has no "
                        "signed immutable-ID provenance"
                    ) from None
                matches = [
                    group
                    for group in workspace.groups.list(  # type: ignore[attr-defined]
                        filter=f"displayName eq '{group_name}'"
                    )
                    if str(_field(group, "display_name") or "").strip() == group_name
                ]
                if len(matches) > 1:
                    raise RuntimeError(
                        f"managed serving-query group {group_name!r} is duplicated"
                    ) from None
                if matches:
                    raise
                continue
        if state is not None:
            binding = managed_workspace_group_binding(
                workspace,
                group_id=state.contract.id,
            )
            if (
                binding.name != state.contract.name
                or binding.external_id != state.contract.external_id
            ):
                raise RuntimeError(
                    "managed serving-query group immutable contract drifted"
                )
            if (
                not binding.id
                or binding.id
                in {candidate.id for candidate in managed_groups_by_endpoint.values()}
            ):
                raise RuntimeError(
                    "managed serving-query group immutable identity is ambiguous"
                )
            managed_groups_by_endpoint[endpoint_id] = binding
    missing_legacy = legacy_pinned.difference(seen_names)
    if missing_legacy:
        raise RuntimeError(
            "legacy-pinned managed-group endpoint(s) are absent: "
            + ", ".join(sorted(missing_legacy))
        )
    workspace_host = str(
        _field(_field(workspace, "config"), "host") or ""
    ).strip()
    if not workspace_host:
        raise RuntimeError(
            "target-credential access governance requires the exact workspace host"
        )
    try:
        account = account_factory()
        account_principal_id = _account_service_principal_scim_id(
            account,
            application_id=application_id,
            expected_scim_id=principal_id,
        )
        authoritative_effective_groups = effective_group_probe(
            account,
            account_sp_id=account_principal_id,
            application_id=application_id,
            expected_workspace_scim_id=principal_id,
            workspace_host=workspace_host,
            account_id=str(account_id),
            group_bindings=tuple(
                sorted(
                    managed_groups_by_endpoint.values(),
                    key=lambda binding: binding.id,
                )
            ),
            assert_single_writer=assert_single_writer,
            admin_workspace=workspace,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "target-credential membership proof was inconclusive"
        ) from exc
    if not isinstance(authoritative_effective_groups, dict):
        raise RuntimeError(
            "target-credential membership proof returned malformed evidence"
        )
    group_ids = tuple(authoritative_effective_groups)
    group_names = tuple(authoritative_effective_groups.values())
    if (
        any(
            not isinstance(group_id, str)
            or not group_id
            or group_id != group_id.strip()
            for group_id in group_ids
        )
        or any(
            not isinstance(group_name, str)
            or not group_name
            or group_name != group_name.strip()
            for group_name in group_names
        )
        or len({name.casefold() for name in group_names}) != len(group_names)
    ):
        raise RuntimeError(
            "target-credential membership proof returned ambiguous evidence"
        )
    for endpoint_id in managed_groups_by_endpoint:
        if endpoint_id in legacy_endpoint_ids:
            assert_legacy_managed_query_group_administration_isolated(
                workspace,
                account_id=str(account_id),
                endpoint_id=endpoint_id,
                application_id=application_id,
                service_principal_id=principal_id,
                authoritative_effective_groups=authoritative_effective_groups,
            )
        else:
            assert_managed_query_group_administration_isolated(
                workspace,
                app_name=app_name,
                account_id=str(account_id),
                endpoint_id=endpoint_id,
                application_id=application_id,
                service_principal_id=principal_id,
                authoritative_effective_groups=authoritative_effective_groups,
            )
    return principal_id, authoritative_effective_groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--expected-inventory-principal", required=True)
    serving = parser.add_mutually_exclusive_group(required=True)
    serving.add_argument("--serving-endpoint", action="append")
    serving.add_argument("--forbid-customer-serving", action="store_true")
    parser.add_argument(
        "--legacy-pinned-serving-endpoint",
        action="append",
        default=[],
        help=(
            "Reviewed signed-blue endpoint whose exact legacy query mode is accepted "
            "read-only during cutover; must also be named by --serving-endpoint."
        ),
    )
    parser.add_argument(
        "--expected-serving-permission",
        choices=("CAN_QUERY", "CAN_MANAGE"),
    )
    genie = parser.add_mutually_exclusive_group(required=True)
    genie.add_argument("--genie-space-id")
    genie.add_argument("--forbid-all-genie", action="store_true")
    args = parser.parse_args(argv)
    if args.serving_endpoint and not args.expected_serving_permission:
        parser.error("--serving-endpoint requires --expected-serving-permission")
    if args.forbid_customer_serving and args.expected_serving_permission:
        parser.error("--forbid-customer-serving rejects --expected-serving-permission")
    if args.forbid_customer_serving and args.legacy_pinned_serving_endpoint:
        parser.error(
            "--forbid-customer-serving rejects --legacy-pinned-serving-endpoint"
        )

    workspace = WorkspaceClient()
    assert_workspace_admin_inventory_identity(
        workspace,
        expected_principal=args.expected_inventory_principal,
    )
    credential_lease = held_deployment_credential_assertion(workspace)
    (
        service_principal_id,
        authoritative_effective_groups,
    ) = _audit_managed_query_group_governance(
        workspace,
        account_id=args.account_id,
        application_id=args.application_id,
        app_name=args.app_name,
        legacy_pinned_endpoint_names=tuple(
            args.legacy_pinned_serving_endpoint or ()
        ),
        assert_single_writer=credential_lease,
    )
    effective_group_names = set(authoritative_effective_groups.values())
    endpoints = tuple(args.serving_endpoint or ())
    if args.forbid_customer_serving:
        audit_global_no_serving_endpoint_access(
            workspace,
            service_principal=args.application_id,
            service_principal_id=service_principal_id,
            effective_group_names=effective_group_names,
        )
    else:
        audit_global_serving_endpoint_access(
            workspace,
            app_name=args.app_name,
            reviewed_endpoint_names=endpoints,
            service_principal=args.application_id,
            expected_permission_level=args.expected_serving_permission,
            service_principal_id=service_principal_id,
            effective_group_names=effective_group_names,
            legacy_pinned_endpoint_names=tuple(args.legacy_pinned_serving_endpoint),
        )
    if args.genie_space_id:
        audit_global_genie_access(
            workspace,
            reviewed_genie_space_id=args.genie_space_id,
            application_id=args.application_id,
            service_principal_id=service_principal_id,
            effective_group_names=effective_group_names,
        )
    elif args.forbid_all_genie:
        audit_global_no_genie_access(
            workspace,
            application_id=args.application_id,
            service_principal_id=service_principal_id,
            effective_group_names=effective_group_names,
        )
    print(
        "[mip-m2m-audit] exact reviewed access verified for "
        f"{args.application_id} across customer-created serving endpoints "
        f"({len(endpoints)} approved) and the Genie inventory"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
