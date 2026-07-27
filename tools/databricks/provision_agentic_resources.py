#!/usr/bin/env python3
"""Provision only MIP-owned Databricks agentic resources, never unrelated demos."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeAlias

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.agents.gateway_contract import (  # noqa: E402
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
from tools.databricks import app_deployment_lease  # noqa: E402
from tools.databricks.agent_runtime_access import (  # noqa: E402
    assert_current_runtime_identity,
    assert_runtime_creator,
)
from tools.databricks.agentic_env_file import write_agentic_env  # noqa: E402
from tools.databricks.agentic_provisioning_cli import build_parser  # noqa: E402
from tools.databricks.agentic_resource_contract import (  # noqa: E402
    ProvisionedResources,
    SupervisorAgentBinding,
    resolve_reviewed_function_owner,
)
from tools.databricks.agentic_supervisor_endpoint import (  # noqa: E402
    exact_supervisor_endpoint_id,
    plan_supervisor_agent,
    supervisor_agent_binding,
    supervisor_candidates,
    supervisor_endpoint_requires_managed_query_rotation,
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
from tools.databricks.signed_blue_supervisor_recovery import (  # noqa: E402
    recover_interrupted_signed_blue_finalization,
    signed_blue_supervisor_pin_from_env,
)
from tools.databricks.supervisor_agent_contract import (  # noqa: E402
    SupervisorContractDrift,
    supervisor_tool_resource_is_exact,
)
from tools.databricks.supervisor_agent_contract import (  # noqa: E402
    supervisor_tool_specs as _supervisor_tool_specs,
)
from tools.databricks.supervisor_contract_verification import (  # noqa: E402
    assert_exact_supervisor_contract as _assert_exact_supervisor_contract,
)
from tools.databricks.supervisor_creation_runtime import (  # noqa: E402
    assert_unique_live_supervisor_binding,
)

SyncTableDefinition: TypeAlias = tuple[str, str, tuple[str, ...]]

DEFAULT_SYNC_TABLES: tuple[SyncTableDefinition, ...] = (
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
    database_instance: str,
    logical_database: str,
) -> None:
    effective_database_instance = str(
        _field(table, "effective_database_instance_name") or ""
    ).strip()
    effective_logical_database = str(_field(table, "effective_logical_database_name") or "").strip()
    configured_database_instance = str(_field(table, "database_instance_name") or "").strip()
    configured_logical_database = str(_field(table, "logical_database_name") or "").strip()
    if effective_database_instance != database_instance:
        raise RuntimeError(
            f"{name} effectively targets Lakebase instance "
            f"{effective_database_instance or '<unknown>'}; expected {database_instance}."
        )
    if effective_logical_database != logical_database:
        raise RuntimeError(
            f"{name} effectively targets logical database "
            f"{effective_logical_database or '<unknown>'}; expected {logical_database}."
        )
    if configured_database_instance and configured_database_instance != database_instance:
        raise RuntimeError(
            f"{name} is configured for Lakebase instance {configured_database_instance}; "
            f"expected {database_instance}."
        )
    if configured_logical_database and configured_logical_database != logical_database:
        raise RuntimeError(
            f"{name} is configured for logical database {configured_logical_database}; "
            f"expected {logical_database}."
        )
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
    assert_single_writer: Callable[[], None],
    source_catalog: str,
    catalog: str,
    schema: str,
    database_instance: str,
    logical_database: str,
    storage_catalog: str,
    storage_schema: str,
    timeout_s: int,
    table_definitions: tuple[SyncTableDefinition, ...] = DEFAULT_SYNC_TABLES,
) -> tuple[str, ...]:
    synced: list[str] = []
    for table, source_table, keys in table_definitions:
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
                database_instance=database_instance,
                logical_database=logical_database,
            )
            print(f"[agentic] synced table exists: {name}")
        except (NotFound, ResourceDoesNotExist):
            print(f"[agentic] creating synced table: {name} <- {source}")
            assert_single_writer()
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


def _resolve_sync_table_definitions(
    table_names: tuple[str, ...],
) -> tuple[SyncTableDefinition, ...]:
    """Bind configured names to reviewed source/key contracts or fail closed."""

    definitions = {definition[0]: definition for definition in DEFAULT_SYNC_TABLES}
    unknown = sorted(set(table_names) - set(definitions))
    if unknown:
        raise ValueError(
            "--lakebase-sync-tables contains names without reviewed source/key contracts: "
            + ", ".join(unknown)
        )
    return tuple(definitions[name] for name in table_names)


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
    assert_single_writer: Callable[[], None],
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
        assert_single_writer=assert_single_writer,
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
            assert_single_writer=assert_single_writer,
        )
        print(
            f"[agentic] {'revoked' if removed else 'verified absent'} direct App ACL "
            f"on obsolete endpoint {obsolete_endpoint}"
        )


def _supervisor_agents() -> list[dict[str, Any]]:
    payload = _run(["supervisor-agents", "list-supervisor-agents"])
    rows = payload if isinstance(payload, list) else payload.get("supervisor_agents", [])
    return [row for row in rows if isinstance(row, dict)]


def _rename_supervisor_agent(supervisor_id: str, name: str) -> None:
    _run_no_json(
        [
            "supervisor-agents",
            "update-supervisor-agent",
            f"supervisor-agents/{supervisor_id}",
            "display_name",
            name,
        ]
    )


def ensure_supervisor_agent(
    workspace: WorkspaceClient,
    *,
    display_name: str,
    genie_space_id: str,
    catalog: str,
    expected_creator_application_id: str,
    expected_query_application_id: str | None = None,
    approved_query_application_ids: tuple[str, ...] = (),
    signed_blue_supervisor_pin: Mapping[str, object] | None = None,
    assert_single_writer: Callable[[], None],
) -> SupervisorAgentBinding:
    candidates = supervisor_candidates(
        _supervisor_agents(),
        display_name=display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    candidates = recover_interrupted_signed_blue_finalization(
        workspace,
        candidates,
        signed_blue_pin=signed_blue_supervisor_pin,
        display_name=display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
        runtime_application_id=expected_creator_application_id,
        managed_query_application_id=expected_query_application_id,
        additional_managed_query_application_ids=approved_query_application_ids,
        assert_contract=assert_exact_supervisor_contract,
        assert_single_writer=assert_single_writer,
        list_agents=_supervisor_agents,
        rename_agent=_rename_supervisor_agent,
    )
    plan = plan_supervisor_agent(
        workspace,
        candidates,
        display_name=display_name,
        genie_space_id=genie_space_id,
        catalog=catalog,
        runtime_application_id=expected_creator_application_id,
        managed_query_application_id=expected_query_application_id,
        additional_managed_query_application_ids=approved_query_application_ids,
        assert_contract=assert_exact_supervisor_contract,
    )
    replacement_name = plan.target_name
    replaced = plan.replaced
    agent = plan.candidate

    if plan.exact_canonical is not None:
        canonical = plan.exact_canonical
        endpoint = str(canonical.get("endpoint_name") or "")
        supervisor_id = str(canonical["supervisor_agent_id"])
        assert_unique_live_supervisor_binding(
            workspace,
            supervisor_id=supervisor_id,
            display_name=display_name,
            endpoint=endpoint,
            runtime_application_id=expected_creator_application_id,
        )
        print(f"[agentic] canonical supervisor already exact: {display_name} " f"({supervisor_id})")
        return supervisor_agent_binding(
            supervisor_id=supervisor_id,
            display_name=display_name,
            endpoint=endpoint,
        )

    def requires_query_rotation(endpoint: str) -> bool:
        return supervisor_endpoint_requires_managed_query_rotation(
            workspace,
            endpoint_name=endpoint,
            runtime_application_id=expected_creator_application_id,
            managed_query_application_id=expected_query_application_id,
            additional_managed_query_application_ids=approved_query_application_ids,
        )

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
                expected_display_name=replacement_name,
            )
        except SupervisorContractDrift as exc:
            raise RuntimeError(
                "immutable green Supervisor candidate drifted; refusing in-place repair"
            ) from exc
        endpoint = str(agent.get("endpoint_name") or "")
        if requires_query_rotation(endpoint):
            raise RuntimeError(
                "immutable green Supervisor candidate retains legacy query access; "
                "refusing in-place repair"
            )
        assert_unique_live_supervisor_binding(
            workspace,
            supervisor_id=supervisor_id,
            display_name=replacement_name,
            endpoint=endpoint,
            runtime_application_id=expected_creator_application_id,
        )
        print(f"[agentic] exact supervisor candidate exists: {replacement_name} ({supervisor_id})")
        return supervisor_agent_binding(
            supervisor_id=supervisor_id,
            display_name=replacement_name,
            endpoint=endpoint,
            replaced=replaced,
        )

    raise RuntimeError(
        "Supervisor creation requires the signed prepare/create/claim/complete "
        f"workflow for deterministic target {replacement_name!r}"
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
            and supervisor_tool_resource_is_exact(
                tool_type,
                existing.get(tool_type),
                body[tool_type],
            )
        ):
            raise SupervisorContractDrift(f"Supervisor tool {tool_id!r} failed exact postflight")
    return current_by_id


def assert_exact_supervisor_contract(
    supervisor_id: str,
    *,
    genie_space_id: str,
    catalog: str,
    expected_contract: dict[str, Any] | None = None,
    expected_display_name: str | None = None,
) -> None:
    """Re-read immutable definition, exact tools, and zero examples."""
    _assert_exact_supervisor_contract(
        supervisor_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
        run=_run,
        exact_tools=_exact_supervisor_tools,
        expected_contract=expected_contract,
        expected_display_name=expected_display_name,
    )


def _ensure_supervisor_tools(
    supervisor_id: str,
    *,
    genie_space_id: str,
    catalog: str,
    assert_single_writer: Callable[[], None],
) -> None:
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
            and supervisor_tool_resource_is_exact(
                tool_type,
                existing.get(tool_type),
                expected_resource,
            )
        )
        if existing:
            if exact:
                continue
            print(f"[agentic] refreshing supervisor tool: {tool_id}")
            assert_single_writer()
            _run_no_json(["supervisor-agents", "delete-tool", f"{parent}/tools/{tool_id}"])
        print(f"[agentic] creating supervisor tool: {tool_id}")
        payload = {
            "tool_type": tool_type,
            "description": description,
            **body,
        }
        assert_single_writer()
        _run(
            ["supervisor-agents", "create-tool", parent, tool_id],
            input_json=payload,
        )
    _exact_supervisor_tools(
        supervisor_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser(
        default_sync_tables=tuple(row[0] for row in DEFAULT_SYNC_TABLES)
    ).parse_args(argv)
    workspace = WorkspaceClient()
    reviewed_function_owner = resolve_reviewed_function_owner(
        workspace,
        args.catalog,
        args.reviewed_function_owner,
        args.capture_reviewed_function_owner,
    )
    tables = tuple(
        name for raw_name in args.lakebase_sync_tables.split(",") if (name := raw_name.strip())
    )
    if not tables:
        raise ValueError("at least one --lakebase-sync-tables value is required")
    if len(tables) != len(set(tables)):
        raise ValueError("--lakebase-sync-tables contains duplicate table names")
    table_definitions = _resolve_sync_table_definitions(tables)
    lease_check: Callable[[], None] | None = None
    if not args.skip_sync or not args.skip_supervisor or not args.skip_gateway:
        lease_check = app_deployment_lease.held_assertion(
            workspace,
            app_name=args.app_name,
            lease_id=args.deployment_lease_id,
            source_git_sha=args.deployment_source_git_sha,
        )
        lease_check()
    if not args.skip_gateway and not reviewed_function_owner:
        raise RuntimeError("reviewed-function owner proof is required for Gateway provisioning")
    if not args.skip_sync:
        assert lease_check is not None
        tables = ensure_synced_tables(
            workspace,
            assert_single_writer=lease_check,
            source_catalog=args.catalog,
            catalog=args.lakebase_catalog,
            schema=args.lakebase_schema,
            database_instance=args.database_instance,
            logical_database=args.logical_database,
            storage_catalog=args.catalog,
            storage_schema=args.storage_schema,
            timeout_s=args.timeout_s,
            table_definitions=table_definitions,
        )
    supervisor_id: str | None = None
    supervisor_endpoint: str | None = None
    supervisor_endpoint_id: str | None = None
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
        assert lease_check is not None
        lease_check()
        supervisor_binding = ensure_supervisor_agent(
            workspace,
            display_name=args.supervisor_name,
            genie_space_id=args.genie_space_id,
            catalog=args.catalog,
            expected_creator_application_id=args.expected_runtime_application_id,
            expected_query_application_id=args.proxy_caller_application_id,
            approved_query_application_ids=tuple(args.approved_query_application_id),
            signed_blue_supervisor_pin=signed_blue_supervisor_pin_from_env(),
            assert_single_writer=lease_check,
        )
        supervisor_id = supervisor_binding.supervisor_id
        supervisor_endpoint = supervisor_binding.endpoint
        if supervisor_endpoint:
            _wait_serving_endpoint_ready(supervisor_endpoint, timeout=f"{args.timeout_s}s")
            supervisor_endpoint_id = exact_supervisor_endpoint_id(
                workspace,
                endpoint_name=supervisor_endpoint,
                runtime_application_id=args.expected_runtime_application_id,
            )
            handoff_endpoint_id = assert_unique_live_supervisor_binding(
                workspace,
                supervisor_id=supervisor_binding.supervisor_id,
                display_name=supervisor_binding.display_name,
                endpoint=supervisor_endpoint,
                runtime_application_id=args.expected_runtime_application_id,
            )
            if handoff_endpoint_id != supervisor_endpoint_id:
                raise RuntimeError("Supervisor binding endpoint identity changed before export")
    elif not args.skip_gateway:
        supervisor_id = args.supervisor_id.strip()
        supervisor_endpoint = args.supervisor_endpoint.strip()
        if not supervisor_id or not supervisor_endpoint:
            raise ValueError(
                "split Gateway provisioning requires the proven Supervisor ID and endpoint"
            )
        assert_exact_supervisor_contract(
            supervisor_id,
            genie_space_id=args.genie_space_id,
            catalog=args.catalog,
        )
        supervisor_endpoint_id = exact_supervisor_endpoint_id(
            workspace,
            endpoint_name=supervisor_endpoint,
            runtime_application_id=args.expected_runtime_application_id,
        )
    gateway_endpoint: str | None = None
    gateway_table: str | None = None
    gateway_model: str | None = None
    gateway_model_version: int | None = None
    gateway_deployment: Any | None = None
    if not args.skip_gateway:
        gateway_endpoint = args.gateway_endpoint
        if (
            not gateway_endpoint
            or not supervisor_id
            or not supervisor_endpoint
            or not args.proxy_caller_application_id
            or not args.proxy_caller_credential_id
            or not args.proxy_caller_secret_reference
        ):
            raise ValueError(
                "AI Gateway provisioning needs its ResponsesAgent, Supervisor, and "
                "complete proxy-caller credential binding"
            )
        if gateway_endpoint == supervisor_endpoint:
            raise ValueError(
                "AI Gateway ResponsesAgent endpoint must be distinct from its managed "
                "Supervisor upstream; refusing a self-recursive proxy deployment"
            )
        assert lease_check is not None
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
            proxy_caller_application_id=args.proxy_caller_application_id,
            proxy_caller_credential_id=args.proxy_caller_credential_id,
            proxy_caller_secret_reference=args.proxy_caller_secret_reference,
            approved_query_application_ids=tuple(args.approved_query_application_id),
            deployment_app_name=args.app_name,
            deployment_lease_id=args.deployment_lease_id,
            deployment_source_git_sha=args.deployment_source_git_sha,
        )
        gateway_endpoint = gateway_deployment.endpoint
        _wait_serving_endpoint_ready(gateway_endpoint, timeout=f"{args.timeout_s}s")
        lease_check()
        bind_gateway_runtime_resource_contract(
            workspace,
            gateway_deployment,
            supervisor_name=args.supervisor_name,
            reviewed_function_owner=reviewed_function_owner,
            assert_single_writer=lease_check,
        )
        verify_gateway_responses_agent(
            workspace,
            gateway_deployment,
            assert_single_writer=lease_check,
        )
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
                assert_single_writer=lease_check,
            )
    resources = ProvisionedResources(
        lakebase_sync_catalog=args.lakebase_catalog,
        lakebase_sync_schema=args.lakebase_schema,
        lakebase_sync_tables=tables,
        agent_supervisor_id=supervisor_id,
        agent_supervisor_name=args.supervisor_name if supervisor_id else None,
        agent_serving_endpoint=gateway_endpoint or supervisor_endpoint,
        agent_supervisor_endpoint=supervisor_endpoint,
        agent_supervisor_endpoint_id=supervisor_endpoint_id,
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
        agent_proxy_application_id=args.proxy_caller_application_id or None,
        agent_proxy_credential_id=args.proxy_caller_credential_id or None,
        agent_proxy_secret_reference=args.proxy_caller_secret_reference or None,
        reviewed_function_owner=reviewed_function_owner or None,
    )
    for line in resources.env_lines():
        print(line)
    if args.out_env:
        write_agentic_env(args.out_env, resources, merge=args.merge_out_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
