"""CLI adapter for the agent-runtime effective Unity Catalog audit."""

from __future__ import annotations

import argparse

from backend.agents.gateway_contract import DEFAULT_GATEWAY_AGENT_EXPERIMENT
from databricks.sdk import WorkspaceClient
from tools.databricks.verify_agent_runtime_uc_grants import (
    verify_effective_uc_boundary,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--supervisor-id", required=True)
    parser.add_argument("--supervisor-endpoint-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--gateway-model", required=True)
    parser.add_argument("--gateway-model-family")
    parser.add_argument(
        "--gateway-experiment-base",
        default=DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    )
    parser.add_argument("--genie-space-id", required=True)
    parser.add_argument("--inference-table-prefix", required=True)
    parser.add_argument("--proxy-caller-application-id", required=True)
    parser.add_argument("--proxy-caller-credential-id", required=True)
    parser.add_argument("--proxy-caller-secret-reference", required=True)
    args = parser.parse_args(argv)
    verify_effective_uc_boundary(
        WorkspaceClient(),
        application_id=args.application_id,
        supervisor_id=args.supervisor_id,
        supervisor_endpoint_id=args.supervisor_endpoint_id,
        catalog=args.catalog,
        gateway_model=args.gateway_model,
        gateway_model_family=args.gateway_model_family,
        gateway_experiment_base=args.gateway_experiment_base,
        genie_space_id=args.genie_space_id,
        inference_table_prefix=args.inference_table_prefix,
        proxy_caller_application_id=args.proxy_caller_application_id,
        proxy_caller_credential_id=args.proxy_caller_credential_id,
        proxy_caller_secret_reference=args.proxy_caller_secret_reference,
    )
    print("agent-runtime effective MIP catalog privilege boundary: PASS")
    return 0
