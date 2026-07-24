"""CLI contract for governed Databricks agentic resource provisioning."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    DEFAULT_GATEWAY_AGENT_MODEL,
    DEFAULT_GATEWAY_ENDPOINT,
)


def build_parser(*, default_sync_tables: tuple[str, ...]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=os.environ.get("MIP_DEFAULT_CATALOG", "mip"))
    parser.add_argument(
        "--lakebase-catalog", default=os.environ.get("MIP_LAKEBASE_SYNC_CATALOG", "mip_app_state")
    )
    parser.add_argument(
        "--lakebase-schema", default=os.environ.get("MIP_LAKEBASE_SYNC_SCHEMA", "mip_sync")
    )
    parser.add_argument(
        "--lakebase-sync-tables",
        default=os.environ.get("MIP_LAKEBASE_SYNC_TABLES", ",".join(default_sync_tables)),
        help=(
            "Comma-separated synced table names to preserve when --skip-sync runs "
            "under the isolated agent-runtime identity."
        ),
    )
    parser.add_argument(
        "--database-instance", default=os.environ.get("MIP_LAKEBASE_INSTANCE", "mip-app-state")
    )
    parser.add_argument(
        "--logical-database", default=os.environ.get("MIP_LAKEBASE_DATABASE_NAME", "mip_app_state")
    )
    parser.add_argument(
        "--storage-schema", default=os.environ.get("MIP_LAKEBASE_SYNC_STORAGE_SCHEMA", "app")
    )
    parser.add_argument(
        "--gateway-endpoint",
        default=os.environ.get("MIP_AI_GATEWAY_ENDPOINT", DEFAULT_GATEWAY_ENDPOINT),
        help=(
            "MIP-owned ResponsesAgent endpoint that delegates to the managed Supervisor "
            "and accepts per-endpoint AI Gateway governance."
        ),
    )
    parser.add_argument(
        "--gateway-endpoint-prefix",
        default=DEFAULT_GATEWAY_ENDPOINT,
        help="Stable prefix for deterministic contract-versioned green endpoints.",
    )
    parser.add_argument(
        "--gateway-schema", default=os.environ.get("MIP_AI_GATEWAY_SCHEMA", "audit")
    )
    parser.add_argument(
        "--gateway-table-prefix",
        default=os.environ.get("MIP_AI_GATEWAY_TABLE_PREFIX", "mip_agent_gateway_growth_agent"),
    )
    parser.add_argument(
        "--gateway-agent-model",
        default=os.environ.get(
            "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY",
            DEFAULT_GATEWAY_AGENT_MODEL,
        ),
    )
    parser.add_argument(
        "--gateway-agent-experiment",
        default=os.environ.get(
            "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE",
            DEFAULT_GATEWAY_AGENT_EXPERIMENT,
        ),
    )
    parser.add_argument(
        "--supervisor-name",
        default=os.environ.get("MIP_AGENT_SUPERVISOR_NAME", "Mortgage Growth Agent"),
    )
    parser.add_argument(
        "--supervisor-id",
        default=os.environ.get("MIP_AGENT_SUPERVISOR_ID", ""),
    )
    parser.add_argument(
        "--supervisor-endpoint",
        default=os.environ.get("MIP_AGENT_SUPERVISOR_ENDPOINT", ""),
    )
    parser.add_argument("--app-name", default=os.environ.get("MIP_APP_NAME", "mip-app"))
    parser.add_argument("--genie-space-id", default=os.environ.get("GENIE_SPACE_ID", ""))
    parser.add_argument(
        "--expected-runtime-application-id",
        default=os.environ.get("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", ""),
    )
    parser.add_argument(
        "--proxy-caller-application-id",
        default=os.environ.get("DATABRICKS_AGENT_PROXY_CLIENT_ID", ""),
    )
    parser.add_argument(
        "--proxy-caller-credential-id",
        default=os.environ.get("DATABRICKS_AGENT_PROXY_CREDENTIAL_ID", ""),
    )
    parser.add_argument(
        "--proxy-caller-secret-reference",
        default=os.environ.get("MIP_AGENT_PROXY_SECRET_REFERENCE", ""),
    )
    parser.add_argument(
        "--deployment-lease-id",
        default=os.environ.get("MIP_APP_DEPLOYMENT_LEASE_ID", ""),
    )
    parser.add_argument(
        "--deployment-source-git-sha",
        default=os.environ.get("MIP_GIT_SHA", ""),
    )
    parser.add_argument(
        "--reviewed-function-owner",
        default=os.environ.get("MIP_REVIEWED_FUNCTION_OWNER", ""),
    )
    parser.add_argument("--capture-reviewed-function-owner", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-gateway", action="store_true")
    parser.add_argument("--skip-supervisor", action="store_true")
    parser.add_argument("--skip-app-permissions", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=900)
    parser.add_argument("--out-env", type=Path)
    parser.add_argument(
        "--merge-out-env",
        action="store_true",
        help=(
            "Merge this provisioning phase into an existing strict env file, "
            "preserving replacement metadata emitted by an earlier phase."
        ),
    )
    return parser
