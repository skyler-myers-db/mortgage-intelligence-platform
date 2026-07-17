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
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.agents.gateway_contract import (  # noqa: E402
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    DEFAULT_GATEWAY_AGENT_MODEL,
    DEFAULT_GATEWAY_ENDPOINT,
    LEGACY_GATEWAY_ENDPOINT,
)
from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.errors import NotFound, ResourceDoesNotExist  # noqa: E402
from databricks.sdk.service.database import (  # noqa: E402
    NewPipelineSpec,
    SyncedDatabaseTable,
    SyncedTableSchedulingPolicy,
    SyncedTableSpec,
)
from tools.databricks.agent_runtime_access import (  # noqa: E402
    assert_current_runtime_identity,
    assert_runtime_creator,
)
from tools.databricks.agentic_resource_contract import (  # noqa: E402
    ProvisionedResources,
    SupervisorAgentBinding,
)
from tools.databricks.gateway_runtime_resource_binding import (  # noqa: E402
    bind_gateway_runtime_resource_contract,
)
from tools.databricks.provision_gateway_responses_agent import (  # noqa: E402
    ensure_gateway_responses_agent,
    verify_gateway_responses_agent,
)
from tools.databricks.serving_endpoint_acl import (  # noqa: E402
    grant_direct_can_query,
    revoke_direct_permissions,
)
from tools.databricks.supervisor_agent_contract import (  # noqa: E402
    RUNTIME_REPLACEMENT_SUFFIX,
    SUPERVISOR_DESCRIPTION,
    SUPERVISOR_INSTRUCTIONS,
    SupervisorContractDrift,
    supervisor_contract_document,
    supervisor_replacement_name,
)
from tools.databricks.supervisor_agent_contract import (  # noqa: E402
    supervisor_tool_specs as _supervisor_tool_specs,
)

DEFAULT_SYNC_TABLES = (
    ("source_readiness", "source_readiness", ("source_name",)),
    ("segment_population", "segment_population", ("segment_code", "state")),
    (
        "funnel_snapshot_daily",
        "funnel_snapshot_daily",
        ("snapshot_date", "state", "segment_code"),
    ),
)


def _run(
    args: list[str], *, input_json: dict[str, Any] | None = None
) -> dict[str, Any] | list[Any]:
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
    existing_primary_keys = list(primary_keys) if isinstance(primary_keys, list | tuple) else []
    scheduling_policy = _enum_value(_field(spec, "scheduling_policy") if spec is not None else "")
    new_pipeline_spec = _field(spec, "new_pipeline_spec") if spec is not None else None

    if source_table != source:
        raise RuntimeError(
            f"{name} exists but syncs from {source_table or '<unknown>'}; expected {source}. "
            "Drop and recreate the synced table before claiming agentic Lakebase Sync."
        )
    if existing_primary_keys != list(keys):
        raise RuntimeError(
            f"{name} exists with primary keys {existing_primary_keys}; expected {list(keys)}."
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


def _wait_serving_endpoint_ready(endpoint: str, *, timeout: str) -> None:
    # The CLI create call may already wait, but get/poll keeps the path
    # idempotent when the endpoint existed in an updating state.
    _ = timeout
    deadline = time.monotonic() + 20 * 60
    while time.monotonic() < deadline:
        details = _run(["serving-endpoints", "get", endpoint])
        if not isinstance(details, dict):
            raise RuntimeError(f"serving endpoint {endpoint} returned an invalid payload")
        ready = ((details.get("state") or {}).get("ready") or "").upper()
        updating = ((details.get("state") or {}).get("config_update") or "").upper()
        if ready == "READY" and updating == "NOT_UPDATING":
            print(f"[agentic] serving endpoint ready: {endpoint}")
            return
        print(f"[agentic] waiting for endpoint {endpoint}: ready={ready} update={updating}")
        time.sleep(15)
    raise TimeoutError(f"serving endpoint {endpoint} did not become ready")


def _converge_app_gateway_permissions(
    workspace: WorkspaceClient,
    *,
    gateway_endpoint: str,
    supervisor_endpoint: str,
    app_name: str,
    preserve_endpoints: tuple[str, ...] = (),
) -> None:
    """Grant only the outer proxy and revoke historical direct bypasses."""

    app = workspace.apps.get(app_name)
    service_principal = str(
        getattr(app, "service_principal_client_id", None)
        or (app.get("service_principal_client_id") if isinstance(app, dict) else "")
        or ""
    ).strip()
    if not service_principal:
        raise RuntimeError(f"app service principal not found for {app_name!r}")
    grant_direct_can_query(
        workspace,
        endpoint_name=gateway_endpoint,
        service_principal=service_principal,
    )
    print(
        f"[agentic] granted CAN_QUERY on {gateway_endpoint} "
        f"to app service principal {service_principal}"
    )
    obsolete_endpoints = {
        supervisor_endpoint,
        DEFAULT_GATEWAY_ENDPOINT,
        LEGACY_GATEWAY_ENDPOINT,
    }
    list_endpoints = getattr(getattr(workspace, "serving_endpoints", None), "list", None)
    if callable(list_endpoints):
        for item in list_endpoints():
            name = str(
                (item.get("name") if isinstance(item, dict) else getattr(item, "name", "")) or ""
            ).strip()
            if name == DEFAULT_GATEWAY_ENDPOINT or name.startswith(f"{DEFAULT_GATEWAY_ENDPOINT}-"):
                obsolete_endpoints.add(name)
    for obsolete_endpoint in obsolete_endpoints:
        if obsolete_endpoint == gateway_endpoint:
            continue
        if obsolete_endpoint in preserve_endpoints:
            print(
                f"[agentic] preserved App ACL on blue endpoint {obsolete_endpoint} "
                "until green proof"
            )
            continue
        removed = revoke_direct_permissions(
            workspace,
            endpoint_name=obsolete_endpoint,
            service_principal=service_principal,
            missing_ok=obsolete_endpoint != supervisor_endpoint,
        )
        print(
            f"[agentic] {'revoked' if removed else 'verified absent'} direct App ACL "
            f"on obsolete endpoint {obsolete_endpoint}"
        )


def _supervisor_agents() -> list[dict[str, Any]]:
    payload = _run(["supervisor-agents", "list-supervisor-agents"])
    rows = payload if isinstance(payload, list) else payload.get("supervisor_agents", [])
    return [row for row in rows if isinstance(row, dict)]


def _matching_supervisor(
    display_name: str, *, agents: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    rows = _supervisor_agents() if agents is None else agents
    matches = [row for row in rows if row.get("display_name") == display_name]
    if len(matches) > 1:
        raise RuntimeError(
            f"multiple Supervisor agents use reserved display name {display_name!r}; "
            "manual governance review is required"
        )
    return matches[0] if matches else None


def ensure_supervisor_agent(
    *,
    display_name: str,
    genie_space_id: str,
    catalog: str,
    expected_creator_application_id: str,
) -> SupervisorAgentBinding:
    agents = _supervisor_agents()
    canonical = _matching_supervisor(display_name, agents=agents)
    replacement_name = supervisor_replacement_name(
        display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    replacement = _matching_supervisor(replacement_name, agents=agents)
    legacy_replacement = _matching_supervisor(
        f"{display_name}{RUNTIME_REPLACEMENT_SUFFIX}",
        agents=agents,
    )
    if replacement is not None and legacy_replacement is not None:
        raise RuntimeError(
            "contract-hashed and legacy runtime Supervisor replacements coexist; "
            "manual governance cleanup is required before selecting either candidate"
        )
    replaced: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None
    if canonical is not None:
        try:
            assert_runtime_creator(
                canonical.get("creator"),
                application_id=expected_creator_application_id,
                resource=f"Supervisor agent {display_name}",
            )
        except RuntimeError:
            replaced = canonical
            agent = replacement
        else:
            try:
                assert_exact_supervisor_contract(
                    str(canonical["supervisor_agent_id"]),
                    genie_space_id=genie_space_id,
                    catalog=catalog,
                )
            except SupervisorContractDrift:
                replaced = canonical
                agent = replacement
            else:
                if replacement is not None or legacy_replacement is not None:
                    raise RuntimeError(
                        "a replacement Supervisor remains beside an exact canonical contract"
                    )
                print(
                    f"[agentic] canonical supervisor already exact: {display_name} "
                    f"({canonical['supervisor_agent_id']})"
                )
                return SupervisorAgentBinding(
                    supervisor_id=str(canonical["supervisor_agent_id"]),
                    display_name=display_name,
                    endpoint=str(canonical.get("endpoint_name") or ""),
                )
    elif replacement is not None:
        agent = replacement
    elif legacy_replacement is not None:
        agent = legacy_replacement
        replacement_name = f"{display_name}{RUNTIME_REPLACEMENT_SUFFIX}"

    if agent is not None:
        assert_runtime_creator(
            agent.get("creator"),
            application_id=expected_creator_application_id,
            resource=f"Supervisor agent {replacement_name}",
        )
        supervisor_id = str(agent["supervisor_agent_id"])
        try:
            assert_exact_supervisor_contract(
                supervisor_id,
                genie_space_id=genie_space_id,
                catalog=catalog,
            )
        except SupervisorContractDrift as exc:
            raise RuntimeError(
                "immutable green Supervisor candidate drifted; refusing in-place repair"
            ) from exc
        endpoint = str(agent.get("endpoint_name") or "")
        print(f"[agentic] exact supervisor candidate exists: {replacement_name} ({supervisor_id})")
        return SupervisorAgentBinding(
            supervisor_id=supervisor_id,
            display_name=replacement_name,
            endpoint=endpoint,
            replaced_supervisor_id=(
                str(replaced.get("supervisor_agent_id") or "") if replaced else None
            ),
            replaced_supervisor_endpoint=(
                str(replaced.get("endpoint_name") or "") if replaced else None
            ),
            replaced_supervisor_creator=(str(replaced.get("creator") or "") if replaced else None),
            replaced_supervisor_create_time=(
                str(replaced.get("create_time") or "") if replaced else None
            ),
        )

    target_display_name = replacement_name if replaced is not None else display_name
    print(f"[agentic] creating supervisor agent: {target_display_name}")
    created = _run(
        ["supervisor-agents", "create-supervisor-agent"],
        input_json={
            "display_name": target_display_name,
            "description": SUPERVISOR_DESCRIPTION,
            "instructions": SUPERVISOR_INSTRUCTIONS,
        },
    )
    if not isinstance(created, dict):
        raise RuntimeError("Supervisor create returned an invalid payload")
    supervisor_id = str(created.get("supervisor_agent_id") or "").strip()
    if not supervisor_id:
        raise RuntimeError("Supervisor create did not return a resource ID")
    _ensure_supervisor_tools(supervisor_id, genie_space_id=genie_space_id, catalog=catalog)
    endpoint = created.get("endpoint_name") or ""
    if not endpoint:
        refreshed = _run(
            ["supervisor-agents", "get-supervisor-agent", f"supervisor-agents/{supervisor_id}"]
        )
        if not isinstance(refreshed, dict):
            raise RuntimeError("Supervisor endpoint lookup returned an invalid payload")
        endpoint = refreshed.get("endpoint_name") or ""
    refreshed = _run(
        ["supervisor-agents", "get-supervisor-agent", f"supervisor-agents/{supervisor_id}"]
    )
    if not isinstance(refreshed, dict):
        raise RuntimeError("Supervisor postflight returned an invalid payload")
    assert_runtime_creator(
        refreshed.get("creator"),
        application_id=expected_creator_application_id,
        resource=f"Supervisor agent {target_display_name}",
    )
    assert_exact_supervisor_contract(
        supervisor_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    return SupervisorAgentBinding(
        supervisor_id=supervisor_id,
        display_name=target_display_name,
        endpoint=endpoint,
        replaced_supervisor_id=(
            str(replaced.get("supervisor_agent_id") or "") if replaced else None
        ),
        replaced_supervisor_endpoint=(
            str(replaced.get("endpoint_name") or "") if replaced else None
        ),
        replaced_supervisor_creator=(str(replaced.get("creator") or "") if replaced else None),
        replaced_supervisor_create_time=(
            str(replaced.get("create_time") or "") if replaced else None
        ),
    )


def _exact_supervisor_tools(
    supervisor_id: str,
    *,
    genie_space_id: str,
    catalog: str,
    specs: list[tuple[str, str, str, dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    parent = f"supervisor-agents/{supervisor_id}"
    current = _run(["supervisor-agents", "list-tools", parent])
    current_rows = current if isinstance(current, list) else current.get("tools", [])
    current_by_id = {
        str(row.get("tool_id") or ""): row
        for row in current_rows
        if isinstance(row, dict) and row.get("tool_id")
    }
    examples = _run(["supervisor-agents", "list-examples", parent])
    example_rows = examples if isinstance(examples, list) else examples.get("examples", [])
    if example_rows:
        raise SupervisorContractDrift(
            "Supervisor must contain zero examples under the reviewed contract"
        )
    specs = specs or _supervisor_tool_specs(genie_space_id=genie_space_id, catalog=catalog)
    expected_ids = {tool_id for tool_id, *_rest in specs}
    if set(current_by_id) != expected_ids:
        raise SupervisorContractDrift(
            "Supervisor exact tool-set postflight failed: expected "
            + ", ".join(sorted(expected_ids))
            + "; found "
            + ", ".join(sorted(current_by_id))
        )
    for tool_id, tool_type, description, body in specs:
        existing = current_by_id[tool_id]
        if not (
            existing.get("tool_type") == tool_type
            and existing.get("description") == description
            and existing.get(tool_type) == body[tool_type]
        ):
            raise SupervisorContractDrift(f"Supervisor tool {tool_id!r} failed exact postflight")
    return current_by_id


def assert_exact_supervisor_contract(
    supervisor_id: str,
    *,
    genie_space_id: str,
    catalog: str,
    expected_contract: dict[str, Any] | None = None,
) -> None:
    """Re-read immutable definition, exact tools, and zero examples."""

    parent = f"supervisor-agents/{supervisor_id}"
    details = _run(["supervisor-agents", "get-supervisor-agent", parent])
    if not isinstance(details, dict):
        raise SupervisorContractDrift(
            "Supervisor definition postflight returned an invalid payload"
        )
    contract = expected_contract or supervisor_contract_document(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    tools = contract.get("tools")
    if (
        set(contract) != {"description", "instructions", "tools", "examples"}
        or not isinstance(tools, list)
        or contract.get("examples") != []
    ):
        raise SupervisorContractDrift("stored Supervisor contract is invalid")
    specs: list[tuple[str, str, str, dict[str, Any]]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise SupervisorContractDrift("stored Supervisor tool contract is invalid")
        tool_id = str(tool.get("tool_id") or "")
        tool_type = str(tool.get("tool_type") or "")
        description = str(tool.get("description") or "")
        resource = tool.get(tool_type)
        if not tool_id or not tool_type or not description or not isinstance(resource, dict):
            raise SupervisorContractDrift("stored Supervisor tool contract is invalid")
        specs.append((tool_id, tool_type, description, {tool_type: resource}))
    if details.get("description") != contract["description"]:
        raise SupervisorContractDrift("Supervisor description drifted from the reviewed contract")
    if details.get("instructions") != contract["instructions"]:
        raise SupervisorContractDrift("Supervisor instructions drifted from the reviewed contract")
    _exact_supervisor_tools(
        supervisor_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
        specs=specs,
    )


def _ensure_supervisor_tools(supervisor_id: str, *, genie_space_id: str, catalog: str) -> None:
    parent = f"supervisor-agents/{supervisor_id}"
    current = _run(["supervisor-agents", "list-tools", parent])
    current_rows = current if isinstance(current, list) else current.get("tools", [])
    current_by_id = {
        str(row.get("tool_id") or ""): row
        for row in current_rows
        if isinstance(row, dict) and row.get("tool_id")
    }
    examples = _run(["supervisor-agents", "list-examples", parent])
    example_rows = examples if isinstance(examples, list) else examples.get("examples", [])
    if example_rows:
        raise RuntimeError(
            "Supervisor examples are not source-governed; remove them only after manual review"
        )
    tool_specs = _supervisor_tool_specs(genie_space_id=genie_space_id, catalog=catalog)
    expected_ids = {tool_id for tool_id, *_rest in tool_specs}
    unexpected = sorted(set(current_by_id) - expected_ids)
    if unexpected:
        raise RuntimeError(
            "Supervisor contains unexpected tools requiring manual governance review: "
            + ", ".join(unexpected)
        )
    for tool_id, tool_type, description, body in tool_specs:
        existing = current_by_id.get(tool_id)
        expected_resource = body[tool_type]
        exact = bool(
            existing
            and existing.get("tool_type") == tool_type
            and existing.get("description") == description
            and existing.get(tool_type) == expected_resource
        )
        if existing:
            if exact:
                continue
            print(f"[agentic] refreshing supervisor tool: {tool_id}")
            _run_no_json(["supervisor-agents", "delete-tool", f"{parent}/tools/{tool_id}"])
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
    _exact_supervisor_tools(
        supervisor_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )


def _write_env(path: Path, resources: ProvisionedResources) -> None:
    text = "\n".join(resources.env_lines()) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"[agentic] wrote env file: {path}")


def _parser() -> argparse.ArgumentParser:
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
        default=os.environ.get(
            "MIP_LAKEBASE_SYNC_TABLES",
            ",".join(row[0] for row in DEFAULT_SYNC_TABLES),
        ),
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
    parser.add_argument("--app-name", default=os.environ.get("MIP_APP_NAME", "mip-app"))
    parser.add_argument("--genie-space-id", default=os.environ.get("GENIE_SPACE_ID", ""))
    parser.add_argument(
        "--expected-runtime-application-id",
        default=os.environ.get("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", ""),
    )
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-gateway", action="store_true")
    parser.add_argument("--skip-supervisor", action="store_true")
    parser.add_argument("--skip-app-permissions", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=900)
    parser.add_argument("--out-env", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = WorkspaceClient()
    tables = tuple(
        name for raw_name in args.lakebase_sync_tables.split(",") if (name := raw_name.strip())
    )
    if not tables:
        raise ValueError("at least one --lakebase-sync-tables value is required")
    if len(tables) != len(set(tables)):
        raise ValueError("--lakebase-sync-tables contains duplicate table names")
    if not args.skip_sync:
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
    supervisor_binding: SupervisorAgentBinding | None = None
    if not args.skip_supervisor:
        if not args.genie_space_id:
            raise ValueError("GENIE_SPACE_ID is required before provisioning the supervisor agent")
        if not args.expected_runtime_application_id:
            raise ValueError(
                "dedicated agent-runtime application ID is required before provisioning "
                "the Supervisor"
            )
        assert_current_runtime_identity(
            workspace,
            application_id=args.expected_runtime_application_id,
        )
        supervisor_binding = ensure_supervisor_agent(
            display_name=args.supervisor_name,
            genie_space_id=args.genie_space_id,
            catalog=args.catalog,
            expected_creator_application_id=args.expected_runtime_application_id,
        )
        supervisor_id = supervisor_binding.supervisor_id
        supervisor_endpoint = supervisor_binding.endpoint
        if supervisor_endpoint:
            _wait_serving_endpoint_ready(supervisor_endpoint, timeout=f"{args.timeout_s}s")
            endpoint_details = workspace.serving_endpoints.get(supervisor_endpoint)
            assert_runtime_creator(
                getattr(endpoint_details, "creator", None),
                application_id=args.expected_runtime_application_id,
                resource=f"managed Supervisor endpoint {supervisor_endpoint}",
            )
    gateway_endpoint: str | None = None
    gateway_table: str | None = None
    gateway_model: str | None = None
    gateway_model_version: int | None = None
    gateway_deployment: Any | None = None
    if not args.skip_gateway:
        gateway_endpoint = args.gateway_endpoint
        if not gateway_endpoint or not supervisor_id or not supervisor_endpoint:
            raise ValueError(
                "AI Gateway provisioning needs both its ResponsesAgent endpoint and Supervisor"
            )
        if gateway_endpoint == supervisor_endpoint:
            raise ValueError(
                "AI Gateway ResponsesAgent endpoint must be distinct from its managed "
                "Supervisor upstream; refusing a self-recursive proxy deployment"
            )
        gateway_deployment = ensure_gateway_responses_agent(
            workspace,
            endpoint=gateway_endpoint,
            endpoint_prefix=args.gateway_endpoint_prefix,
            supervisor_id=supervisor_id,
            upstream_endpoint=supervisor_endpoint,
            model_name=args.gateway_agent_model,
            experiment_name=args.gateway_agent_experiment,
            inference_catalog=args.catalog,
            inference_schema=args.gateway_schema,
            inference_table_prefix=args.gateway_table_prefix,
            genie_space_id=args.genie_space_id,
            expected_creator_application_id=args.expected_runtime_application_id,
        )
        gateway_endpoint = gateway_deployment.endpoint
        _wait_serving_endpoint_ready(gateway_endpoint, timeout=f"{args.timeout_s}s")
        bind_gateway_runtime_resource_contract(
            workspace,
            gateway_deployment,
            supervisor_name=args.supervisor_name,
        )
        verify_gateway_responses_agent(workspace, gateway_deployment)
        gateway_details = workspace.serving_endpoints.get(gateway_endpoint)
        assert_runtime_creator(
            getattr(gateway_details, "creator", None),
            application_id=args.expected_runtime_application_id,
            resource=f"Gateway endpoint {gateway_endpoint}",
        )
        gateway_table = gateway_deployment.inference_table
        gateway_model = gateway_deployment.model_name
        gateway_model_version = gateway_deployment.model_version
        if not args.skip_app_permissions:
            _converge_app_gateway_permissions(
                workspace,
                gateway_endpoint=gateway_endpoint,
                supervisor_endpoint=supervisor_endpoint,
                app_name=args.app_name,
            )
    resources = ProvisionedResources(
        lakebase_sync_catalog=args.lakebase_catalog,
        lakebase_sync_schema=args.lakebase_schema,
        lakebase_sync_tables=tables,
        agent_supervisor_id=supervisor_id,
        agent_supervisor_name=args.supervisor_name if supervisor_id else None,
        agent_serving_endpoint=gateway_endpoint or supervisor_endpoint,
        agent_supervisor_endpoint=supervisor_endpoint,
        ai_gateway_endpoint=gateway_endpoint,
        ai_gateway_inference_table=gateway_table,
        ai_gateway_agent_model=gateway_model,
        ai_gateway_agent_model_version=gateway_model_version,
        ai_gateway_agent_model_family=(
            getattr(gateway_deployment, "model_family", args.gateway_agent_model)
            if gateway_deployment
            else None
        ),
        ai_gateway_experiment_base=(
            getattr(gateway_deployment, "experiment_base", args.gateway_agent_experiment)
            if gateway_deployment
            else None
        ),
        ai_gateway_table_prefix=(
            getattr(gateway_deployment, "inference_table_prefix", args.gateway_table_prefix)
            if gateway_deployment
            else None
        ),
        replaced_supervisor_id=(
            supervisor_binding.replaced_supervisor_id if supervisor_binding else None
        ),
        replaced_supervisor_endpoint=(
            supervisor_binding.replaced_supervisor_endpoint if supervisor_binding else None
        ),
        replaced_supervisor_creator=(
            supervisor_binding.replaced_supervisor_creator if supervisor_binding else None
        ),
        replaced_supervisor_create_time=(
            supervisor_binding.replaced_supervisor_create_time if supervisor_binding else None
        ),
        agent_runtime_application_id=args.expected_runtime_application_id or None,
    )
    for line in resources.env_lines():
        print(line)
    if args.out_env:
        _write_env(args.out_env, resources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
