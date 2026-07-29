#!/usr/bin/env python3
"""CLI for exact Gateway model archival and lifecycle audit."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from mlflow import MlflowClient

from databricks.sdk import WorkspaceClient
from tools.databricks.gateway_model_archival import (
    GatewayModelArchiveScope,
    archive_gateway_model,
    delta_version_resolver,
)
from tools.databricks.gateway_model_archival_reconcile import (
    archive_unprotected_gateway_models,
)
from tools.databricks.gateway_model_lifecycle_audit import (
    audit_gateway_model_lifecycle,
    authenticate_gateway_inventory_principal,
)
from tools.databricks.gateway_model_retirement_record import record_sha256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("archive", "archive-unprotected", "audit"))
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--source-git-sha", required=True)
    parser.add_argument("--runtime-application-id", required=True)
    parser.add_argument("--app-application-id", required=True)
    parser.add_argument("--proxy-application-id", required=True)
    parser.add_argument("--verifier-application-id", required=True)
    parser.add_argument("--archive-owner", required=True)
    parser.add_argument("--governance-group", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--model-family", required=True)
    parser.add_argument("--model-name")
    parser.add_argument("--experiment-base", required=True)
    parser.add_argument("--inference-schema", required=True)
    parser.add_argument("--inference-table-prefix", required=True)
    parser.add_argument("--rollback-scope", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument("--expected-candidate-model")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    scope = GatewayModelArchiveScope(
        app_name=args.app_name,
        lease_id=args.lease_id,
        source_git_sha=args.source_git_sha,
        runtime_application_id=args.runtime_application_id,
        app_application_id=args.app_application_id,
        proxy_application_id=args.proxy_application_id,
        verifier_application_id=args.verifier_application_id,
        archive_owner=args.archive_owner,
        governance_group=args.governance_group,
        catalog=args.catalog,
        model_family=args.model_family,
        experiment_base=args.experiment_base,
        inference_schema=args.inference_schema,
        inference_table_prefix=args.inference_table_prefix,
        rollback_scope=args.rollback_scope,
        expected_lakebase_instance=args.lakebase_instance,
        warehouse_id=args.warehouse_id,
    )
    workspace = WorkspaceClient()
    authenticate_gateway_inventory_principal(
        workspace,
        expected_inventory_principal=args.expected_inventory_principal,
        expected_archive_owner=args.archive_owner,
    )
    registry = MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    tracking = MlflowClient(tracking_uri="databricks")
    resolver = delta_version_resolver(workspace, warehouse_id=args.warehouse_id)
    if args.mode == "archive":
        model_name = str(args.model_name or "").strip()
        if not model_name:
            raise ValueError("archive mode requires --model-name")
        completion = archive_gateway_model(
            workspace,
            registry,
            tracking,
            scope=scope,
            model_name=model_name,
            resolve_delta_version=resolver,
        )
        print(
            json.dumps(
                {
                    "model_name": completion["model_name"],
                    "retirement_record_sha256": record_sha256(completion),
                    "status": "archived",
                },
                sort_keys=True,
            )
        )
        return 0
    if args.mode == "archive-unprotected":
        completions = archive_unprotected_gateway_models(
            workspace,
            registry,
            tracking,
            scope=scope,
            resolve_delta_version=resolver,
        )
        print(
            json.dumps(
                {
                    "archived_models": sorted(
                        str(completion["model_name"]) for completion in completions
                    ),
                    "status": "converged",
                },
                sort_keys=True,
            )
        )
        return 0
    candidate_model = str(args.expected_candidate_model or "").strip()
    if not candidate_model:
        raise ValueError("audit mode requires --expected-candidate-model")
    proof = audit_gateway_model_lifecycle(
        workspace,
        registry,
        tracking,
        scope=scope,
        resolve_delta_version=resolver,
        expected_inventory_principal=args.expected_inventory_principal,
        expected_candidate_model=candidate_model,
    )
    print(
        json.dumps(
            {
                "candidate_model": proof.candidate_model,
                "models": len(proof.states),
                "status": "verified",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
