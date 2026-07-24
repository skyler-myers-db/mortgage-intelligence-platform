"""Command-line adapter for signed Databricks App rollback operations."""

from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path

from databricks.sdk import WorkspaceClient
from tools.databricks.app_deployment_rollback_inputs import (
    payload_file,
    reviewed_resources_file,
)


def main(argv: list[str] | None = None) -> int:
    from tools.databricks.app_deployment_rollback import (
        DEFAULT_SCOPE,
        capture_current,
        ensure_current,
        restore_last_good,
        verified_signed_last_good_contract,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "capture", "restore"))
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--payload")
    parser.add_argument("--expected-git-sha")
    parser.add_argument("--expected-gateway-binding")
    parser.add_argument("--deployment-lease-id")
    parser.add_argument("--genie-space-id")
    parser.add_argument("--bundle-summary")
    parser.add_argument("--app-resource-payload")
    parser.add_argument("--revoke-endpoint", action="append", default=[])
    parser.add_argument("--treatment-warehouse-id")
    parser.add_argument("--treatment-catalog", default="mip")
    parser.add_argument("--out-env", type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        parser.error(f"{args.token_env} is empty")
    workspace = WorkspaceClient()
    common = {
        "workspace": workspace,
        "app_name": args.app_name,
        "scope": args.scope,
        "base_url": args.base_url,
        "bearer_token": token,
    }
    if args.action == "ensure":
        if not args.treatment_warehouse_id:
            parser.error("--treatment-warehouse-id is required for ensure")
        endpoint = ensure_current(
            **common,
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
        )
        if args.out_env is not None:
            signed_blue = verified_signed_last_good_contract(
                workspace,
                app_name=args.app_name,
                scope=args.scope,
            )
            retired_ids = ",".join(
                signed_blue.pending_proxy_credential_retirement_ids
            )
            args.out_env.write_text(
                (
                    f"MIP_APP_ROLLBACK_GATEWAY_ENDPOINT={shlex.quote(endpoint)}\n"
                    "MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS="
                    f"{shlex.quote(retired_ids)}\n"
                ),
                encoding="utf-8",
            )
    elif args.action == "restore":
        if not args.treatment_warehouse_id:
            parser.error("--treatment-warehouse-id is required for restore")
        restore_last_good(
            **common,
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
            revoke_endpoints=tuple(args.revoke_endpoint),
        )
    else:
        if not args.payload:
            parser.error("--payload is required for capture")
        payload = payload_file(args.payload)
        if not args.expected_git_sha:
            parser.error("--expected-git-sha is required for capture")
        if not args.genie_space_id:
            parser.error("--genie-space-id is required for capture")
        if not args.deployment_lease_id:
            parser.error("--deployment-lease-id is required for capture")
        resource_contract_path = args.app_resource_payload or args.bundle_summary
        if not resource_contract_path:
            parser.error("--app-resource-payload is required for capture")
        if not args.treatment_warehouse_id:
            parser.error("--treatment-warehouse-id is required for capture")
        capture_current(
            **common,
            payload=payload,
            expected_git_sha=args.expected_git_sha,
            expected_gateway_binding=args.expected_gateway_binding,
            expected_deployment_lease_id=args.deployment_lease_id,
            genie_space_id=args.genie_space_id,
            expected_app_resources=reviewed_resources_file(resource_contract_path),
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
        )
        if args.out_env is not None:
            captured = verified_signed_last_good_contract(
                workspace,
                app_name=args.app_name,
                scope=args.scope,
            )
            retired_ids = ",".join(
                captured.pending_proxy_credential_retirement_ids
            )
            args.out_env.write_text(
                "MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS="
                f"{shlex.quote(retired_ids)}\n",
                encoding="utf-8",
            )
    print(f"App last-good rollback contract {args.action}: PASS")
    return 0
