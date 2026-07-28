"""Command-line adapter for signed Databricks App rollback operations."""

from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path

from databricks.sdk import WorkspaceClient
from tools.databricks.app_deployment_rollback_inputs import (
    payload_file,
    reviewed_resources_file,
)
from tools.databricks.app_rollback_signed_contract import SignedLastGoodAppContract


def _write_binding_env(
    path: Path,
    contract: SignedLastGoodAppContract,
    *,
    gateway_endpoint: str,
) -> None:
    retired_ids = ",".join(contract.pending_proxy_credential_retirement_ids)
    gateway_pin_json = json.dumps(
        {
            "name": gateway_endpoint,
            "endpoint_id": contract.gateway_endpoint_id,
            "creator": contract.gateway_endpoint_creator,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    supervisor_pin_json = json.dumps(
        {
            "supervisor_id": contract.supervisor_id,
            "endpoint": contract.supervisor_endpoint,
            "endpoint_id": contract.supervisor_endpoint_id,
            "creator": contract.supervisor_creator,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    values = {
        "MIP_APP_ROLLBACK_RECORD_VERSION": str(contract.record_version),
        "MIP_APP_ROLLBACK_PROXY_MODE": contract.proxy_rollback_mode,
        "MIP_APP_ROLLBACK_DEPLOYMENT_ID": contract.deployment_id,
        "MIP_APP_ROLLBACK_GATEWAY_ENDPOINT": gateway_endpoint,
        "MIP_APP_ROLLBACK_GATEWAY_ENDPOINT_ID": contract.gateway_endpoint_id,
        "MIP_APP_ROLLBACK_GATEWAY_CREATOR": contract.gateway_endpoint_creator,
        "MIP_APP_ROLLBACK_GATEWAY_PIN_JSON": gateway_pin_json,
        "MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX": (
            contract.gateway_inference_table_family
        ),
        "MIP_APP_ROLLBACK_SUPERVISOR_ID": contract.supervisor_id,
        "MIP_APP_ROLLBACK_SUPERVISOR_CREATOR": contract.supervisor_creator,
        "MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT": contract.supervisor_endpoint,
        "MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID": contract.supervisor_endpoint_id,
        "MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON": supervisor_pin_json,
        "MIP_APP_ROLLBACK_RUNTIME_APPLICATION_ID": contract.runtime_application_id,
        "MIP_APP_ROLLBACK_GENIE_SPACE_ID": contract.genie_space_id,
        "MIP_APP_ROLLBACK_PROXY_APPLICATION_ID": contract.proxy_application_id or "",
        "MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS": retired_ids,
    }
    path.write_text(
        "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items()),
        encoding="utf-8",
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
    parser.add_argument("action", choices=("ensure", "inspect", "capture", "restore"))
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--payload")
    parser.add_argument("--expected-git-sha")
    parser.add_argument("--expected-gateway-binding")
    parser.add_argument("--deployment-lease-id")
    parser.add_argument("--deployment-source-git-sha")
    parser.add_argument("--expected-rollback-deployment-id")
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
        if not args.deployment_lease_id:
            parser.error("--deployment-lease-id is required for ensure")
        if not args.deployment_source_git_sha:
            parser.error("--deployment-source-git-sha is required for ensure")
        endpoint = ensure_current(
            **common,
            deployment_lease_id=args.deployment_lease_id,
            deployment_source_git_sha=args.deployment_source_git_sha,
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
        )
        if args.out_env is not None:
            signed_blue = verified_signed_last_good_contract(
                workspace,
                app_name=args.app_name,
                scope=args.scope,
            )
            _write_binding_env(
                args.out_env,
                signed_blue,
                gateway_endpoint=endpoint,
            )
    elif args.action == "inspect":
        if args.out_env is None:
            parser.error("--out-env is required for inspect")
        signed = verified_signed_last_good_contract(
            workspace,
            app_name=args.app_name,
            scope=args.scope,
        )
        _write_binding_env(
            args.out_env,
            signed,
            gateway_endpoint=signed.gateway_endpoint,
        )
    elif args.action == "restore":
        if not args.treatment_warehouse_id:
            parser.error("--treatment-warehouse-id is required for restore")
        if not args.deployment_lease_id:
            parser.error("--deployment-lease-id is required for restore")
        if not args.deployment_source_git_sha:
            parser.error("--deployment-source-git-sha is required for restore")
        restore_last_good(
            **common,
            deployment_lease_id=args.deployment_lease_id,
            deployment_source_git_sha=args.deployment_source_git_sha,
            treatment_warehouse_id=args.treatment_warehouse_id,
            treatment_catalog=args.treatment_catalog,
            revoke_endpoints=tuple(args.revoke_endpoint),
            expected_rollback_deployment_id=args.expected_rollback_deployment_id,
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
            _write_binding_env(
                args.out_env,
                captured,
                gateway_endpoint=captured.gateway_endpoint,
            )
    print(f"App last-good rollback contract {args.action}: PASS")
    return 0
