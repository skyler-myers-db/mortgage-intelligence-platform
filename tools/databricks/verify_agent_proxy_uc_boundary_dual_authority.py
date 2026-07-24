#!/usr/bin/env python3
"""Verify agent-proxy UC access with metastore-owner and proxy authority."""

from __future__ import annotations

import argparse
import os
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_uc_inventory import _text
from tools.databricks.audit_agent_runtime_foreign_uc_access import (
    audit_foreign_uc_access,
)
from tools.databricks.uc_owner_policy import account_client_from_env
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
    parser.add_argument(
        "--foreign-catalog-binding-policy-json",
        default=os.environ.get("MIP_UC_FOREIGN_CATALOG_BINDING_POLICY", ""),
    )
    args = parser.parse_args(argv)

    admin_workspace = WorkspaceClient()
    account_client = account_client_from_env()
    proof = audit_foreign_uc_access(
        admin_workspace,
        application_id=args.application_id,
        catalog=args.catalog,
        expected_inventory_principal=args.expected_inventory_principal,
        foreign_catalog_binding_policy=args.foreign_catalog_binding_policy_json,
        account_factory=lambda: account_client,
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
    )
    if final_proof != proof:
        raise RuntimeError("foreign-catalog proof changed during agent-proxy audit")
    print("agent-proxy dual-authority effective UC boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
