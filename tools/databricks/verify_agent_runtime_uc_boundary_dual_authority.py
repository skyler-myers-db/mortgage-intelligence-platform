#!/usr/bin/env python3
"""Verify runtime UC access with metastore-owner and runtime authority in one process."""

from __future__ import annotations

import argparse
import os
from typing import Any

from mlflow import MlflowClient

from backend.agents.gateway_contract import DEFAULT_GATEWAY_AGENT_EXPERIMENT
from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_uc_inventory import _text
from tools.databricks.audit_agent_runtime_foreign_uc_access import (
    audit_foreign_uc_access,
)
from tools.databricks.gateway_model_archival import (
    GatewayModelArchiveScope,
    delta_version_resolver,
)
from tools.databricks.gateway_model_lifecycle_audit import (
    audit_gateway_model_lifecycle,
)
from tools.databricks.uc_owner_policy import account_client_from_env
from tools.databricks.verify_agent_runtime_uc_grants import verify_effective_uc_boundary

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


def _bind_runtime_auth_environment(*, admin_workspace: Any, application_id: str) -> None:
    """Replace ambient deployer auth only after its control-plane proof passes."""

    expected_id = application_id.strip()
    configured_id = os.environ.get("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "").strip()
    host = _text(getattr(getattr(admin_workspace, "config", None), "host", None))
    if not expected_id or configured_id != expected_id or not client_secret or not host:
        raise RuntimeError(
            "dual-authority UC audit lacks exact agent-runtime OAuth credentials or host"
        )
    for name in _AMBIENT_AUTH_KEYS:
        os.environ.pop(name, None)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_AUTH_TYPE"] = "oauth-m2m"
    os.environ["DATABRICKS_CLIENT_ID"] = configured_id
    os.environ["DATABRICKS_CLIENT_SECRET"] = client_secret
    os.environ["MIP_DISABLE_DOTENV"] = "1"


def _lifecycle_snapshot(proof: Any) -> tuple[Any, ...]:
    return (
        proof.application_id,
        proof.inventory_principal,
        proof.catalog,
        proof.metastore_id,
        proof.workspace_id,
        proof.model_family,
        proof.candidate_model,
        proof.states,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument("--supervisor-id", required=True)
    parser.add_argument("--supervisor-endpoint-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--gateway-model", required=True)
    parser.add_argument("--gateway-model-family")
    parser.add_argument("--gateway-experiment-base", default=DEFAULT_GATEWAY_AGENT_EXPERIMENT)
    parser.add_argument("--genie-space-id", required=True)
    parser.add_argument("--inference-table-prefix", required=True)
    parser.add_argument("--proxy-caller-application-id", required=True)
    parser.add_argument("--proxy-caller-credential-id", required=True)
    parser.add_argument("--proxy-caller-secret-reference", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--deployment-lease-id", required=True)
    parser.add_argument("--deployment-source-git-sha", required=True)
    parser.add_argument("--app-application-id", required=True)
    parser.add_argument("--verifier-application-id", required=True)
    parser.add_argument("--archive-owner", required=True)
    parser.add_argument("--governance-group", required=True)
    parser.add_argument("--inference-schema", default="audit")
    parser.add_argument("--rollback-scope", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--warehouse-id", required=True)
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
    model_family = (args.gateway_model_family or args.gateway_model).strip()
    archive_scope = GatewayModelArchiveScope(
        app_name=args.app_name,
        lease_id=args.deployment_lease_id,
        source_git_sha=args.deployment_source_git_sha,
        runtime_application_id=args.application_id,
        app_application_id=args.app_application_id,
        proxy_application_id=args.proxy_caller_application_id,
        verifier_application_id=args.verifier_application_id,
        archive_owner=args.archive_owner,
        governance_group=args.governance_group,
        catalog=args.catalog,
        model_family=model_family,
        experiment_base=args.gateway_experiment_base,
        inference_schema=args.inference_schema,
        inference_table_prefix=args.inference_table_prefix,
        rollback_scope=args.rollback_scope,
        expected_lakebase_instance=args.lakebase_instance,
        warehouse_id=args.warehouse_id,
    )
    model_registry = MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    tracking_client = MlflowClient(tracking_uri="databricks")
    resolve_delta_version = delta_version_resolver(
        admin_workspace,
        warehouse_id=args.warehouse_id,
    )
    lifecycle_proof = audit_gateway_model_lifecycle(
        admin_workspace,
        model_registry,
        tracking_client,
        scope=archive_scope,
        resolve_delta_version=resolve_delta_version,
        expected_inventory_principal=args.expected_inventory_principal,
        expected_candidate_model=args.gateway_model,
    )
    lifecycle_snapshot = _lifecycle_snapshot(lifecycle_proof)
    _bind_runtime_auth_environment(
        admin_workspace=admin_workspace,
        application_id=args.application_id,
    )
    runtime_workspace = WorkspaceClient()
    verify_effective_uc_boundary(
        runtime_workspace,
        application_id=args.application_id,
        supervisor_id=args.supervisor_id,
        supervisor_endpoint_id=args.supervisor_endpoint_id,
        catalog=args.catalog,
        gateway_model=args.gateway_model,
        gateway_model_family=args.gateway_model_family,
        gateway_experiment_base=args.gateway_experiment_base,
        genie_space_id=args.genie_space_id,
        inference_table_prefix=args.inference_table_prefix,
        foreign_control_plane_proof=proof,
        gateway_model_lifecycle_proof=lifecycle_proof,
        expected_inventory_principal=args.expected_inventory_principal,
        proxy_caller_application_id=args.proxy_caller_application_id,
        proxy_caller_credential_id=args.proxy_caller_credential_id,
        proxy_caller_secret_reference=args.proxy_caller_secret_reference,
    )
    post_runtime_proof = audit_foreign_uc_access(
        admin_workspace,
        application_id=args.application_id,
        catalog=args.catalog,
        expected_inventory_principal=args.expected_inventory_principal,
        foreign_catalog_binding_policy=args.foreign_catalog_binding_policy_json,
        account_factory=lambda: account_client,
    )
    if post_runtime_proof != proof:
        raise RuntimeError("foreign-catalog control-plane proof changed during runtime audit")
    post_runtime_lifecycle = audit_gateway_model_lifecycle(
        admin_workspace,
        model_registry,
        tracking_client,
        scope=archive_scope,
        resolve_delta_version=resolve_delta_version,
        expected_inventory_principal=args.expected_inventory_principal,
        expected_candidate_model=args.gateway_model,
    )
    if _lifecycle_snapshot(post_runtime_lifecycle) != lifecycle_snapshot:
        raise RuntimeError("Gateway model lifecycle proof changed during runtime audit")
    print("agent-runtime dual-authority effective UC boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
