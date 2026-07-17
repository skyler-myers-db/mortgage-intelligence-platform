"""Audit one M2M identity across every visible Genie and serving resource."""

from __future__ import annotations

import argparse

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_access import (
    audit_global_genie_access,
    audit_global_no_genie_access,
)
from tools.databricks.serving_endpoint_acl import audit_global_serving_endpoint_access


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument("--serving-endpoint", action="append", required=True)
    parser.add_argument(
        "--expected-serving-permission",
        choices=("CAN_QUERY", "CAN_MANAGE"),
        required=True,
    )
    genie = parser.add_mutually_exclusive_group(required=True)
    genie.add_argument("--genie-space-id")
    genie.add_argument("--forbid-all-genie", action="store_true")
    args = parser.parse_args(argv)

    workspace = WorkspaceClient()
    assert_workspace_admin_inventory_identity(
        workspace,
        expected_principal=args.expected_inventory_principal,
    )
    endpoints = tuple(args.serving_endpoint)
    audit_global_serving_endpoint_access(
        workspace,
        reviewed_endpoint_names=endpoints,
        service_principal=args.application_id,
        expected_permission_level=args.expected_serving_permission,
    )
    if args.genie_space_id:
        audit_global_genie_access(
            workspace,
            reviewed_genie_space_id=args.genie_space_id,
            application_id=args.application_id,
        )
    elif args.forbid_all_genie:
        audit_global_no_genie_access(
            workspace,
            application_id=args.application_id,
        )
    print(
        "[mip-m2m-audit] exact global access verified for "
        f"{args.application_id} across {len(endpoints)} serving endpoint(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
