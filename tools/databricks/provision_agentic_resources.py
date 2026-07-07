#!/usr/bin/env python3
"""Provision MIP-owned Databricks agentic resources.

This helper intentionally provisions only resources owned by the Mortgage
Intelligence Platform. It refuses to reuse unrelated workspace AI Gateway or
Supervisor Agent demos because doing so would make the app claim governance it
does not control.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.database import (
    NewPipelineSpec,
    SyncedDatabaseTable,
    SyncedTableSchedulingPolicy,
    SyncedTableSpec,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SYNC_TABLES = (
    ("source_readiness", "source_readiness", ("source_name",)),
    ("segment_population", "segment_population", ("segment_code", "state")),
    (
        "funnel_snapshot_daily",
        "funnel_snapshot_daily",
        ("snapshot_date", "state", "segment_code"),
    ),
)


@dataclass(frozen=True)
class ProvisionedResources:
    lakebase_sync_catalog: str
    lakebase_sync_schema: str
    lakebase_sync_tables: tuple[str, ...]
    agent_supervisor_id: str | None = None
    agent_supervisor_name: str | None = None
    agent_serving_endpoint: str | None = None
    ai_gateway_endpoint: str | None = None
    ai_gateway_inference_table: str | None = None

    def env_lines(self) -> list[str]:
        def assignment(key: str, value: str) -> str:
            return f"{key}={shlex.quote(value)}"

        rows = [
            assignment("MIP_LAKEBASE_SYNC", "1"),
            assignment("MIP_LAKEBASE_SYNC_CATALOG", self.lakebase_sync_catalog),
            assignment("MIP_LAKEBASE_SYNC_SCHEMA", self.lakebase_sync_schema),
            assignment("MIP_LAKEBASE_SYNC_TABLES", ",".join(self.lakebase_sync_tables)),
        ]
        if self.agent_supervisor_id and self.agent_serving_endpoint:
            rows.extend(
                [
                    assignment("MIP_AGENT_ORCHESTRATOR", "1"),
                    assignment("MIP_AGENT_SUPERVISOR_ID", self.agent_supervisor_id),
                    assignment("MIP_AGENT_SUPERVISOR_NAME", self.agent_supervisor_name or ""),
                    assignment("MIP_AGENT_SERVING_ENDPOINT", self.agent_serving_endpoint),
                ]
            )
        if self.ai_gateway_endpoint and self.ai_gateway_inference_table:
            rows.extend(
                [
                    assignment("MIP_AI_GATEWAY", "1"),
                    assignment("MIP_AI_GATEWAY_ENDPOINT", self.ai_gateway_endpoint),
                    assignment("MIP_AI_GATEWAY_INFERENCE_TABLE", self.ai_gateway_inference_table),
                ]
            )
        return rows


def _run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
    cmd = ["databricks", *args, "-o", "json"]
    if input_json is not None:
        payload = json.dumps(input_json)
        cmd = ["databricks", *args, "--json", payload, "-o", "json"]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout or "{}")


def _run_no_json(args: list[str], *, input_json: dict[str, Any] | None = None) -> str:
    cmd = ["databricks", *args]
    if input_json is not None:
        cmd = ["databricks", *args, "--json", json.dumps(input_json)]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _target_table(catalog: str, schema: str, table: str) -> str:
    return f"{catalog}.{schema}.{table}"


def _source_gold_table(catalog: str, table: str) -> str:
    return f"{catalog}.gold.{table}"


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


def _synced_table_is_ready(state: str) -> bool:
    normalized = state.upper()
    return "ONLINE" in normalized or normalized.endswith("NO_PENDING_UPDATE")


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _validate_existing_synced_table(
    table: object,
    *,
    name: str,
    source: str,
    keys: tuple[str, ...],
    storage_catalog: str,
    storage_schema: str,
) -> None:
    spec = _field(table, "spec")
    source_table = _field(spec, "source_table_full_name") if spec is not None else None
    primary_keys = _field(spec, "primary_key_columns") if spec is not None else None
    scheduling_policy = _enum_value(_field(spec, "scheduling_policy") if spec is not None else "")
    new_pipeline_spec = _field(spec, "new_pipeline_spec") if spec is not None else None

    if source_table != source:
        raise RuntimeError(
            f"{name} exists but syncs from {source_table or '<unknown>'}; expected {source}. "
            "Drop and recreate the synced table before claiming agentic Lakebase Sync."
        )
    if list(primary_keys or []) != list(keys):
        raise RuntimeError(
            f"{name} exists with primary keys {list(primary_keys or [])}; expected {list(keys)}."
        )
    if scheduling_policy != SyncedTableSchedulingPolicy.SNAPSHOT.value:
        raise RuntimeError(
            f"{name} exists with scheduling policy {scheduling_policy or '<unknown>'}; "
            f"expected {SyncedTableSchedulingPolicy.SNAPSHOT.value}."
        )
    # Databricks currently omits new_pipeline_spec on get_synced_database_table
    # responses for existing tables. Validate it when present, and keep the
    # create path pinned to the configured storage catalog/schema below.
    if new_pipeline_spec is not None:
        existing_storage_catalog = _field(new_pipeline_spec, "storage_catalog")
        existing_storage_schema = _field(new_pipeline_spec, "storage_schema")
        if (existing_storage_catalog, existing_storage_schema) != (storage_catalog, storage_schema):
            raise RuntimeError(
                f"{name} exists with pipeline storage "
                f"{existing_storage_catalog or '<unknown>'}.{existing_storage_schema or '<unknown>'}; "
                f"expected {storage_catalog}.{storage_schema}."
            )


def ensure_synced_tables(
    workspace: WorkspaceClient,
    *,
    source_catalog: str,
    catalog: str,
    schema: str,
    database_instance: str,
    logical_database: str,
    storage_catalog: str,
    storage_schema: str,
    timeout_s: int,
) -> tuple[str, ...]:
    synced: list[str] = []
    for table, source_table, keys in DEFAULT_SYNC_TABLES:
        name = _target_table(catalog, schema, table)
        source = _source_gold_table(source_catalog, source_table)
        try:
            existing = workspace.database.get_synced_database_table(name)
            _validate_existing_synced_table(
                existing,
                name=name,
                source=source,
                keys=keys,
                storage_catalog=storage_catalog,
                storage_schema=storage_schema,
            )
            print(f"[agentic] synced table exists: {name}")
        except (NotFound, ResourceDoesNotExist):
            print(f"[agentic] creating synced table: {name} <- {source}")
            workspace.database.create_synced_database_table(
                SyncedDatabaseTable(
                    name=name,
                    database_instance_name=database_instance,
                    logical_database_name=logical_database,
                    spec=SyncedTableSpec(
                        source_table_full_name=source,
                        primary_key_columns=list(keys),
                        scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
                        create_database_objects_if_missing=True,
                        new_pipeline_spec=NewPipelineSpec(
                            storage_catalog=storage_catalog,
                            storage_schema=storage_schema,
                        ),
                    ),
                )
            )
        _wait_synced_table_online(workspace, name, timeout_s=timeout_s)
        synced.append(table)
    return tuple(synced)


def _wait_synced_table_online(workspace: WorkspaceClient, name: str, *, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    last_state = ""
    last_message = ""
    while time.monotonic() < deadline:
        table = workspace.database.get_synced_database_table(name)
        status = table.data_synchronization_status
        last_state = _enum_value(getattr(status, "detailed_state", ""))
        last_message = str(getattr(status, "message", "") or "")
        if _synced_table_is_ready(last_state):
            print(f"[agentic] synced table online: {name} ({last_state})")
            return
        print(f"[agentic] waiting for {name}: {last_state or 'unknown'}")
        time.sleep(10)
    raise TimeoutError(f"{name} did not become online in {timeout_s}s: {last_state} {last_message}")


# Databricks rejects per-endpoint AI Gateway config for endpoint types outside
# its eligibility list (observed 2026-07-07 on the managed Supervisor Agent
# endpoint, which accepted the same PUT on 2026-07-02 — the platform tightened
# classification and wiped the prior config). This phrase is the stable part
# of that platform error; matching it lets provisioning degrade honestly
# (warn + skip) instead of aborting the whole deploy. The capability probe's
# own pre-checks then report AI Gateway as non-claimable, which is the truth.
_AI_GATEWAY_INELIGIBLE_MARKER = "AI Gateway is currently only supported for"


def ensure_ai_gateway_on_endpoint(
    *,
    endpoint: str,
    catalog: str,
    schema: str,
    table_prefix: str,
    per_user_calls_per_minute: int,
    timeout: str,
) -> str | None:
    print(f"[agentic] configuring AI Gateway on serving endpoint: {endpoint}")
    if per_user_calls_per_minute <= 0:
        raise ValueError("AI Gateway per-user rate limit must be positive")
    try:
        _run(
            ["serving-endpoints", "put-ai-gateway", endpoint],
            input_json={
                "inference_table_config": {
                    "enabled": True,
                    "catalog_name": catalog,
                    "schema_name": schema,
                    "table_name_prefix": table_prefix,
                },
                "rate_limits": [
                    {
                        "key": "user",
                        "calls": per_user_calls_per_minute,
                        "renewal_period": "minute",
                    }
                ],
            },
        )
    except RuntimeError as exc:
        if _AI_GATEWAY_INELIGIBLE_MARKER not in str(exc):
            raise
        print(
            "[agentic] WARNING: the workspace rejects AI Gateway config for this "
            f"endpoint type ({endpoint}). Skipping gateway provisioning; the "
            "ai_gateway capability will honestly report non-claimable until an "
            "eligible endpoint is configured. Platform message: "
            f"{str(exc)[-200:]}"
        )
        return None
    _wait_serving_endpoint_ready(endpoint, timeout=timeout)
    return ".".join([catalog, schema, table_prefix])


def _wait_serving_endpoint_ready(endpoint: str, *, timeout: str) -> None:
    # The CLI create call may already wait, but get/poll keeps the path
    # idempotent when the endpoint existed in an updating state.
    _ = timeout
    deadline = time.monotonic() + 20 * 60
    while time.monotonic() < deadline:
        details = _run(["serving-endpoints", "get", endpoint])
        ready = ((details.get("state") or {}).get("ready") or "").upper()
        updating = ((details.get("state") or {}).get("config_update") or "").upper()
        if ready == "READY" and updating == "NOT_UPDATING":
            print(f"[agentic] serving endpoint ready: {endpoint}")
            return
        print(f"[agentic] waiting for endpoint {endpoint}: ready={ready} update={updating}")
        time.sleep(15)
    raise TimeoutError(f"serving endpoint {endpoint} did not become ready")


def _serving_endpoint_id(endpoint: str) -> str | None:
    endpoints = _run(["serving-endpoints", "list"])
    rows = endpoints if isinstance(endpoints, list) else endpoints.get("endpoints", [])
    for row in rows:
        if row.get("name") == endpoint:
            return row.get("id")
    return None


def _grant_app_can_query_serving_endpoint(*, endpoint: str, app_name: str) -> None:
    app = _run(["apps", "get", app_name])
    service_principal = app.get("service_principal_client_id")
    endpoint_id = _serving_endpoint_id(endpoint)
    if not service_principal:
        print(f"[agentic] app service principal not found for {app_name}; skipping endpoint ACL")
        return
    if not endpoint_id:
        print(f"[agentic] serving endpoint id not found for {endpoint}; skipping endpoint ACL")
        return
    _run(
        ["serving-endpoints", "update-permissions", endpoint_id],
        input_json={
            "access_control_list": [
                {
                    "service_principal_name": service_principal,
                    "permission_level": "CAN_QUERY",
                }
            ]
        },
    )
    print(f"[agentic] granted CAN_QUERY on {endpoint} to app service principal {service_principal}")


def ensure_supervisor_agent(*, display_name: str, genie_space_id: str, catalog: str) -> tuple[str, str]:
    agents = _run(["supervisor-agents", "list-supervisor-agents"])
    for agent in agents if isinstance(agents, list) else agents.get("supervisor_agents", []):
        if agent.get("display_name") == display_name:
            supervisor_id = agent["supervisor_agent_id"]
            endpoint = agent.get("endpoint_name") or ""
            print(f"[agentic] supervisor agent exists: {display_name} ({supervisor_id})")
            _ensure_supervisor_tools(supervisor_id, genie_space_id=genie_space_id, catalog=catalog)
            return supervisor_id, endpoint

    print(f"[agentic] creating supervisor agent: {display_name}")
    created = _run(
        ["supervisor-agents", "create-supervisor-agent"],
        input_json={
            "display_name": display_name,
            "description": "Governed mortgage-growth supervisor for Module 0 lead generation.",
            "instructions": (
                "Route borrower, segment, and source-readiness questions to the Mortgage Lead "
                "Intelligence Genie Space and reviewed Unity Catalog functions. Never expose raw "
                "PII, never send outreach, and always return a human-review handoff for action."
            ),
        },
    )
    supervisor_id = created["supervisor_agent_id"]
    _ensure_supervisor_tools(supervisor_id, genie_space_id=genie_space_id, catalog=catalog)
    endpoint = created.get("endpoint_name") or ""
    if not endpoint:
        refreshed = _run(["supervisor-agents", "get-supervisor-agent", f"supervisor-agents/{supervisor_id}"])
        endpoint = refreshed.get("endpoint_name") or ""
    return supervisor_id, endpoint


def _ensure_supervisor_tools(supervisor_id: str, *, genie_space_id: str, catalog: str) -> None:
    parent = f"supervisor-agents/{supervisor_id}"
    current = _run(["supervisor-agents", "list-tools", parent])
    existing = {
        row.get("tool_id")
        for row in (current if isinstance(current, list) else current.get("tools", []))
    }
    tool_specs: list[tuple[str, str, str, dict[str, Any]]] = [
        (
            "mortgage_data_analyst",
            "genie_space",
            "Answers governed data questions over the Mortgage Lead Intelligence Genie Space.",
            {"genie_space": {"id": genie_space_id}},
        ),
        (
            "build_cohort",
            "uc_function",
            "Counts broad borrower cohorts from reviewed Module 0 UC function logic.",
            {"uc_function": {"name": f"{catalog}.gold.fn_build_cohort"}},
        ),
        (
            "segment_counts",
            "uc_function",
            "Reconciles broad cohorts to eligible Lead Queue counts.",
            {"uc_function": {"name": f"{catalog}.gold.fn_segment_counts"}},
        ),
        (
            "lead_queue_url",
            "uc_function",
            "Creates governed Lead Queue handoff URLs for human review.",
            {"uc_function": {"name": f"{catalog}.gold.fn_lead_queue_url"}},
        ),
    ]
    for tool_id, tool_type, description, body in tool_specs:
        if tool_id in existing:
            if tool_type == "uc_function":
                print(f"[agentic] refreshing supervisor tool: {tool_id}")
                _run_no_json(["supervisor-agents", "delete-tool", f"{parent}/tools/{tool_id}"])
            else:
                continue
        print(f"[agentic] creating supervisor tool: {tool_id}")
        payload = {
            "tool_type": tool_type,
            "description": description,
            **body,
        }
        _run(
            ["supervisor-agents", "create-tool", parent, tool_id],
            input_json=payload,
        )


def _write_env(path: Path, resources: ProvisionedResources) -> None:
    text = "\n".join(resources.env_lines()) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"[agentic] wrote env file: {path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=os.environ.get("MIP_DEFAULT_CATALOG", "mip"))
    parser.add_argument("--lakebase-catalog", default=os.environ.get("MIP_LAKEBASE_SYNC_CATALOG", "mip_app_state"))
    parser.add_argument("--lakebase-schema", default=os.environ.get("MIP_LAKEBASE_SYNC_SCHEMA", "mip_sync"))
    parser.add_argument("--database-instance", default=os.environ.get("MIP_LAKEBASE_INSTANCE", "mip-app-state"))
    parser.add_argument("--logical-database", default=os.environ.get("MIP_LAKEBASE_DATABASE_NAME", "mip_app_state"))
    parser.add_argument("--storage-schema", default=os.environ.get("MIP_LAKEBASE_SYNC_STORAGE_SCHEMA", "app"))
    parser.add_argument(
        "--gateway-endpoint",
        default=None,
        help=(
            "Serving endpoint to govern with AI Gateway. Defaults to the Supervisor Agent endpoint "
            "created/reused in this run so real product traffic is logged."
        ),
    )
    parser.add_argument("--gateway-schema", default=os.environ.get("MIP_AI_GATEWAY_SCHEMA", "audit"))
    parser.add_argument(
        "--gateway-table-prefix",
        default=os.environ.get("MIP_AI_GATEWAY_TABLE_PREFIX", "mip_agent_gateway_sonnet"),
    )
    parser.add_argument(
        "--gateway-per-user-calls-per-minute",
        type=int,
        default=int(os.environ.get("MIP_AI_GATEWAY_PER_USER_CALLS_PER_MINUTE", "60")),
    )
    parser.add_argument("--supervisor-name", default=os.environ.get("MIP_AGENT_SUPERVISOR_NAME", "Mortgage Growth Agent"))
    parser.add_argument("--app-name", default=os.environ.get("MIP_APP_NAME", "mip-app"))
    parser.add_argument("--genie-space-id", default=os.environ.get("GENIE_SPACE_ID", ""))
    parser.add_argument("--skip-gateway", action="store_true")
    parser.add_argument("--skip-supervisor", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=900)
    parser.add_argument("--out-env", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = WorkspaceClient()
    tables = ensure_synced_tables(
        workspace,
        source_catalog=args.catalog,
        catalog=args.lakebase_catalog,
        schema=args.lakebase_schema,
        database_instance=args.database_instance,
        logical_database=args.logical_database,
        storage_catalog=args.catalog,
        storage_schema=args.storage_schema,
        timeout_s=args.timeout_s,
    )
    supervisor_id: str | None = None
    supervisor_endpoint: str | None = None
    if not args.skip_supervisor:
        if not args.genie_space_id:
            raise ValueError("GENIE_SPACE_ID is required before provisioning the supervisor agent")
        supervisor_id, supervisor_endpoint = ensure_supervisor_agent(
            display_name=args.supervisor_name,
            genie_space_id=args.genie_space_id,
            catalog=args.catalog,
        )
        if supervisor_endpoint:
            _wait_serving_endpoint_ready(supervisor_endpoint, timeout=f"{args.timeout_s}s")
            _grant_app_can_query_serving_endpoint(endpoint=supervisor_endpoint, app_name=args.app_name)
    gateway_endpoint: str | None = None
    gateway_table: str | None = None
    if not args.skip_gateway:
        gateway_endpoint = args.gateway_endpoint or supervisor_endpoint
        if not gateway_endpoint:
            raise ValueError(
                "AI Gateway provisioning needs --gateway-endpoint or a Supervisor Agent endpoint"
            )
        gateway_table = ensure_ai_gateway_on_endpoint(
            endpoint=gateway_endpoint,
            catalog=args.catalog,
            schema=args.gateway_schema,
            table_prefix=args.gateway_table_prefix,
            per_user_calls_per_minute=args.gateway_per_user_calls_per_minute,
            timeout=f"{args.timeout_s}s",
        )
    resources = ProvisionedResources(
        lakebase_sync_catalog=args.lakebase_catalog,
        lakebase_sync_schema=args.lakebase_schema,
        lakebase_sync_tables=tables,
        agent_supervisor_id=supervisor_id,
        agent_supervisor_name=args.supervisor_name if supervisor_id else None,
        agent_serving_endpoint=supervisor_endpoint,
        ai_gateway_endpoint=gateway_endpoint,
        ai_gateway_inference_table=gateway_table,
    )
    for line in resources.env_lines():
        print(line)
    if args.out_env:
        _write_env(args.out_env, resources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
