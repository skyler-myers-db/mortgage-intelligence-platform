from __future__ import annotations

import re
from typing import Any

import pytest
from databricks.sdk.service.database import (
    NewPipelineSpec,
    SyncedTableSchedulingPolicy,
    SyncedTableSpec,
)

from tools.databricks import export_gateway_runtime_contract, provision_agentic_resources
from tools.databricks.agentic_env_file import merge_agentic_env_values
from tools.databricks.provision_agentic_resources import (
    ProvisionedResources,
    SupervisorAgentBinding,
)

_PROXY_CLIENT_ID = "proxy-client"
_PROXY_CREDENTIAL_ID = "proxy-credential"
_PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
_PROXY_ARGS = [
    "--reviewed-function-owner",
    "reviewed-owner",
    "--proxy-caller-application-id",
    _PROXY_CLIENT_ID,
    "--proxy-caller-credential-id",
    _PROXY_CREDENTIAL_ID,
    "--proxy-caller-secret-reference",
    _PROXY_SECRET_REFERENCE,
]


def _assert_single_writer() -> None:
    return None


def test_capture_reviewed_function_owner_exports_authenticated_deployer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    out_env = tmp_path / "agentic.env"
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        provision_agentic_resources,
        "authenticated_reviewed_function_owner",
        lambda _workspace, *, catalog: (
            "reviewed-owner" if catalog == "mip" else pytest.fail("wrong catalog")
        ),
    )

    assert (
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--skip-supervisor",
                "--skip-gateway",
                "--capture-reviewed-function-owner",
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )
    assert "MIP_REVIEWED_FUNCTION_OWNER=reviewed-owner" in out_env.read_text(
        encoding="utf-8"
    ).splitlines()


def test_capture_reviewed_function_owner_rejects_configured_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        provision_agentic_resources,
        "authenticated_reviewed_function_owner",
        lambda _workspace, *, catalog: "reviewed-owner",
    )

    with pytest.raises(
        RuntimeError,
        match="differs from the authenticated deployer",
    ):
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--skip-supervisor",
                "--skip-gateway",
                "--capture-reviewed-function-owner",
                "--reviewed-function-owner",
                "unreviewed-owner",
            ]
        )


def test_split_provisioning_merge_preserves_replaced_supervisor_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "agentic.env"
    supervisor = ProvisionedResources(
        lakebase_sync_catalog="sync",
        lakebase_sync_schema="app",
        lakebase_sync_tables=("source_readiness",),
        agent_supervisor_id="green-supervisor",
        agent_supervisor_name="Mortgage Growth Agent",
        agent_serving_endpoint="green-supervisor-endpoint",
        agent_supervisor_endpoint="green-supervisor-endpoint",
        replaced_supervisor_id="blue-supervisor",
        replaced_supervisor_endpoint="blue-supervisor-endpoint",
        replaced_supervisor_creator="runtime-client",
        replaced_supervisor_create_time="2026-07-20T00:00:00Z",
        agent_runtime_application_id="runtime-client",
        reviewed_function_owner="reviewed-owner",
    )
    gateway = ProvisionedResources(
        lakebase_sync_catalog="sync",
        lakebase_sync_schema="app",
        lakebase_sync_tables=("source_readiness",),
        agent_supervisor_id="green-supervisor",
        agent_supervisor_name="Mortgage Growth Agent",
        agent_serving_endpoint="green-gateway",
        agent_supervisor_endpoint="green-supervisor-endpoint",
        ai_gateway_endpoint="green-gateway",
        ai_gateway_inference_table="mip.audit.green_inference",
        ai_gateway_agent_model="mip.audit.proxy",
        ai_gateway_agent_model_version=7,
        agent_runtime_application_id="runtime-client",
        agent_proxy_application_id=_PROXY_CLIENT_ID,
        agent_proxy_credential_id=_PROXY_CREDENTIAL_ID,
        agent_proxy_secret_reference=_PROXY_SECRET_REFERENCE,
        reviewed_function_owner="reviewed-owner",
    )

    provision_agentic_resources.write_agentic_env(path, supervisor)
    merge_agentic_env_values(
        path,
        {"MIP_AGENT_PROXY_SECRET_REFERENCE": _PROXY_SECRET_REFERENCE},
    )
    provision_agentic_resources.write_agentic_env(path, gateway, merge=True)
    monkeypatch.setattr(
        export_gateway_runtime_contract,
        "WorkspaceClient",
        lambda: object(),
    )
    monkeypatch.setattr(
        export_gateway_runtime_contract,
        "MlflowClient",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        export_gateway_runtime_contract,
        "resolve_contract",
        lambda *_args, **_kwargs: {
            "MIP_AGENT_SERVING_ENDPOINT": "verified-gateway",
            "MIP_AGENT_SUPERVISOR_ID": "green-supervisor",
            "MIP_AGENT_PROXY_SECRET_REFERENCE": _PROXY_SECRET_REFERENCE,
        },
    )
    assert (
        export_gateway_runtime_contract.main(
            [
                "--shell-env",
                str(path),
                "--genie-space-id",
                "space-123",
                    "--runtime-application-id",
                    "runtime-client",
                    "--reviewed-function-owner",
                    "reviewed-owner",
                "--proxy-caller-application-id",
                _PROXY_CLIENT_ID,
                "--proxy-caller-credential-id",
                _PROXY_CREDENTIAL_ID,
                "--proxy-caller-secret-reference",
                _PROXY_SECRET_REFERENCE,
            ]
        )
        == 0
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    keys = [line.split("=", 1)[0] for line in lines]
    assert len(keys) == len(set(keys))
    assert sum(line.startswith("MIP_AGENT_PROXY_SECRET_REFERENCE=") for line in lines) == 1
    values = dict(
        line.split("=", 1)
        for line in lines
    )
    assert values["MIP_AGENT_SERVING_ENDPOINT"] == "verified-gateway"
    assert values["MIP_REPLACED_AGENT_SUPERVISOR_ID"] == "blue-supervisor"
    assert (
        values["MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"]
        == "blue-supervisor-endpoint"
    )
    assert values["MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"] == "runtime-client"


@pytest.mark.parametrize("canonical_present", [True, False])
def test_hashed_and_legacy_supervisor_collision_fails_before_selection(
    monkeypatch: pytest.MonkeyPatch,
    canonical_present: bool,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = provision_agentic_resources.supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    agents = [
        {
            "supervisor_agent_id": "hashed-id",
            "display_name": replacement_name,
            "endpoint_name": "mas-hashed",
            "creator": "runtime-client",
        },
        {
            "supervisor_agent_id": "legacy-id",
            "display_name": (
                f"{display_name}{provision_agentic_resources.RUNTIME_REPLACEMENT_SUFFIX}"
            ),
            "endpoint_name": "mas-legacy",
            "creator": "runtime-client",
        },
    ]
    if canonical_present:
        agents.insert(
            0,
            {
                "supervisor_agent_id": "canonical-id",
                "display_name": display_name,
                "endpoint_name": "mas-canonical",
                "creator": "runtime-client",
            },
        )
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: agents)
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("collision must fail before contract selection")
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("collision must not create or inspect a selected agent")
        ),
    )

    with pytest.raises(RuntimeError, match="contract-hashed and legacy runtime"):
        provision_agentic_resources.ensure_supervisor_agent(
            display_name=display_name,
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            assert_single_writer=_assert_single_writer,
        )


def test_provisioned_resource_contract_is_reexported_and_preserves_sync_names() -> None:
    resources = ProvisionedResources(
        lakebase_sync_catalog="customer.app_state",
        lakebase_sync_schema="customer_sync",
        lakebase_sync_tables=("source_status_v2", "daily_funnel_v2"),
    )

    assert resources.env_lines() == [
        "MIP_LAKEBASE_SYNC=1",
        "MIP_LAKEBASE_SYNC_CATALOG=customer.app_state",
        "MIP_LAKEBASE_SYNC_SCHEMA=customer_sync",
        "MIP_LAKEBASE_SYNC_TABLES=source_status_v2,daily_funnel_v2",
    ]


def test_creator_mismatch_builds_blue_green_replacement_without_touching_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement_name = provision_agentic_resources.supervisor_replacement_name(
        "Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
    )
    old = {
        "supervisor_agent_id": "old-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-old",
        "creator": "skyler@entrada.ai",
        "create_time": "old-time",
    }
    calls: list[tuple[list[str], object | None]] = []
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [old])
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )

    def run(args: list[str], *, input_json: object | None = None) -> dict[str, str]:
        calls.append((args, input_json))
        if args[:2] == ["supervisor-agents", "create-supervisor-agent"]:
            assert isinstance(input_json, dict)
            assert input_json["display_name"] == replacement_name
            return {
                "supervisor_agent_id": "new-id",
                "endpoint_name": "mas-new",
            }
        return {
            "supervisor_agent_id": "new-id",
            "endpoint_name": "mas-new",
            "creator": "runtime-client",
        }

    monkeypatch.setattr(provision_agentic_resources, "_run", run)

    binding = provision_agentic_resources.ensure_supervisor_agent(
        display_name="Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
        expected_creator_application_id="runtime-client",
        assert_single_writer=_assert_single_writer,
    )

    assert binding == SupervisorAgentBinding(
        supervisor_id="new-id",
        display_name=replacement_name,
        endpoint="mas-new",
        replaced_supervisor_id="old-id",
        replaced_supervisor_endpoint="mas-old",
        replaced_supervisor_creator="skyler@entrada.ai",
        replaced_supervisor_create_time="old-time",
    )
    assert all("delete" not in " ".join(args) for args, _payload in calls)


def test_runtime_owned_contract_drift_builds_green_without_mutating_live_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = {
        "supervisor_agent_id": "old-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-old",
        "creator": "runtime-client",
        "create_time": "old-time",
    }
    replacement_name = provision_agentic_resources.supervisor_replacement_name(
        "Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
    )
    mutated_tools: list[str] = []
    calls: list[tuple[list[str], object | None]] = []
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [old])

    def exact(supervisor_id: str, **_kwargs: object) -> None:
        if supervisor_id == "old-id":
            raise provision_agentic_resources.SupervisorContractDrift("tool drift")

    monkeypatch.setattr(provision_agentic_resources, "assert_exact_supervisor_contract", exact)
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda supervisor_id, **_kwargs: mutated_tools.append(supervisor_id),
    )

    def run(args: list[str], *, input_json: object | None = None) -> dict[str, str]:
        calls.append((args, input_json))
        if args[:2] == ["supervisor-agents", "create-supervisor-agent"]:
            assert isinstance(input_json, dict)
            assert input_json["display_name"] == replacement_name
            return {"supervisor_agent_id": "new-id", "endpoint_name": "mas-new"}
        return {
            "supervisor_agent_id": "new-id",
            "endpoint_name": "mas-new",
            "creator": "runtime-client",
        }

    monkeypatch.setattr(provision_agentic_resources, "_run", run)

    binding = provision_agentic_resources.ensure_supervisor_agent(
        display_name="Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
        expected_creator_application_id="runtime-client",
        assert_single_writer=_assert_single_writer,
    )

    assert binding.replaced_supervisor_id == "old-id"
    assert binding.supervisor_id == "new-id"
    assert binding.display_name == replacement_name
    assert mutated_tools == ["new-id"]
    assert all("delete" not in " ".join(args) for args, _payload in calls)


def test_exact_runtime_supervisor_is_reused_without_tool_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = {
        "supervisor_agent_id": "canonical-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-canonical",
        "creator": "runtime-client",
        "create_time": "old-time",
    }
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [canonical])
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate tools")),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create")),
    )

    assert provision_agentic_resources.ensure_supervisor_agent(
        display_name="Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
        expected_creator_application_id="runtime-client",
        assert_single_writer=_assert_single_writer,
    ) == SupervisorAgentBinding(
        supervisor_id="canonical-id",
        display_name="Mortgage Growth Agent",
        endpoint="mas-canonical",
    )


def test_supervisor_tool_convergence_rejects_unexpected_live_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(args: list[str], *, input_json: object | None = None) -> object:
        nonlocal calls
        _ = input_json
        calls += 1
        if args[1] == "list-tools":
            return [{"tool_id": "unreviewed_tool", "tool_type": "uc_function"}]
        return []

    monkeypatch.setattr(provision_agentic_resources, "_run", run)
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run_no_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    with pytest.raises(RuntimeError, match="unexpected tools"):
        provision_agentic_resources._ensure_supervisor_tools(
            "supervisor-1",
            genie_space_id="space-123",
            catalog="mip",
            assert_single_writer=_assert_single_writer,
        )
    assert calls == 2


def test_exact_supervisor_tools_are_idempotent_and_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "tool_id": tool_id,
            "tool_type": tool_type,
            "description": description,
            **body,
        }
        for tool_id, tool_type, description, body in (
            provision_agentic_resources._supervisor_tool_specs(
                genie_space_id="space-123",
                catalog="mip",
            )
        )
    ]
    calls: list[str] = []

    def run(args: list[str], *, input_json: object | None = None) -> object:
        assert input_json is None
        calls.append(args[1])
        if args[1] == "list-tools":
            return rows
        if args[1] == "list-examples":
            return []
        raise AssertionError(args)

    monkeypatch.setattr(provision_agentic_resources, "_run", run)
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run_no_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    provision_agentic_resources._ensure_supervisor_tools(
        "supervisor-1",
        genie_space_id="space-123",
        catalog="mip",
        assert_single_writer=_assert_single_writer,
    )

    assert calls == ["list-tools", "list-examples", "list-tools", "list-examples"]


def test_converge_app_gateway_permissions_grants_outer_and_revokes_bypasses(monkeypatch) -> None:
    grants: list[tuple[str, str]] = []
    revocations: list[tuple[str, str, bool]] = []
    workspace = type(
        "Workspace",
        (),
        {
            "apps": type(
                "Apps",
                (),
                {
                    "get": lambda _self, name: {
                        "name": name,
                        "service_principal_client_id": "app-sp-123",
                    }
                },
            )()
        },
    )()
    monkeypatch.setattr(
        provision_agentic_resources,
        "grant_direct_can_query",
        lambda _workspace, *, endpoint_name, service_principal: grants.append(
            (endpoint_name, service_principal)
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "revoke_direct_permissions",
        lambda _workspace, *, endpoint_name, service_principal, missing_ok: (
            revocations.append((endpoint_name, service_principal, missing_ok)) or True
        ),
    )

    provision_agentic_resources._converge_app_gateway_permissions(
        workspace,  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        gateway_endpoint="mip-growth-agent-gateway",
        supervisor_endpoint="mas-agent-endpoint",
        app_name="mip-app",
    )

    assert grants == [("mip-growth-agent-gateway", "app-sp-123")]
    assert set(revocations) == {
        ("mas-agent-endpoint", "app-sp-123", False),
        ("mip-agent-gateway", "app-sp-123", True),
    }


def test_ensure_synced_tables_uses_configured_source_catalog(monkeypatch) -> None:
    created_sources: list[str] = []

    class _Database:
        def get_synced_database_table(self, name: str) -> None:
            _ = name
            raise provision_agentic_resources.NotFound("missing")

        def create_synced_database_table(self, table: Any) -> None:
            created_sources.append(table.spec.source_table_full_name)

    class _Workspace:
        database = _Database()

    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_synced_table_online",
        lambda *_args, **_kwargs: None,
    )

    tables = provision_agentic_resources.ensure_synced_tables(
        _Workspace(),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        source_catalog="acme_mip",
        catalog="acme_app_state",
        schema="mip_sync",
        database_instance="mip-app-state",
        logical_database="mip_app_state",
        storage_catalog="acme_mip",
        storage_schema="app",
        timeout_s=1,
    )

    assert tables == ("source_readiness", "segment_population", "funnel_snapshot_daily")
    assert created_sources == [
        "acme_mip.gold.source_readiness",
        "acme_mip.gold.segment_population",
        "acme_mip.gold.funnel_snapshot_daily",
    ]


def test_ensure_synced_tables_reasserts_lease_immediately_before_create(monkeypatch) -> None:
    mutations: list[str] = []

    class _Database:
        def get_synced_database_table(self, _name: str) -> None:
            raise provision_agentic_resources.NotFound("missing")

        def create_synced_database_table(self, _table: Any) -> None:
            mutations.append("create")

    workspace = type("Workspace", (), {"database": _Database()})()

    with pytest.raises(RuntimeError, match="lease lost"):
        provision_agentic_resources.ensure_synced_tables(
            workspace,  # type: ignore[arg-type]
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
            table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
        )

    assert mutations == []


def test_ensure_synced_tables_validates_existing_source_catalog(monkeypatch) -> None:
    checked: list[str] = []

    class _ExistingTable:
        def __init__(self, source: str, keys: list[str]) -> None:
            self.database_instance_name = "mip-app-state"
            self.logical_database_name = "mip_app_state"
            self.effective_database_instance_name = "mip-app-state"
            self.effective_logical_database_name = "mip_app_state"
            self.spec = SyncedTableSpec(
                source_table_full_name=source,
                primary_key_columns=keys,
                scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
                new_pipeline_spec=NewPipelineSpec(storage_catalog="acme_mip", storage_schema="app"),
            )

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            table = name.rsplit(".", 1)[-1]
            checked.append(name)
            keys = {
                "source_readiness": ["source_name"],
                "segment_population": ["segment_code", "state"],
                "funnel_snapshot_daily": ["snapshot_date", "state", "segment_code"],
            }[table]
            return _ExistingTable(f"acme_mip.gold.{table}", keys)

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"existing synced table should not be recreated: {table}")

    class _Workspace:
        database = _Database()

    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_synced_table_online",
        lambda *_args, **_kwargs: None,
    )

    assert provision_agentic_resources.ensure_synced_tables(
        _Workspace(),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        source_catalog="acme_mip",
        catalog="acme_app_state",
        schema="mip_sync",
        database_instance="mip-app-state",
        logical_database="mip_app_state",
        storage_catalog="acme_mip",
        storage_schema="app",
        timeout_s=1,
    ) == ("source_readiness", "segment_population", "funnel_snapshot_daily")
    assert checked == [
        "acme_app_state.mip_sync.source_readiness",
        "acme_app_state.mip_sync.segment_population",
        "acme_app_state.mip_sync.funnel_snapshot_daily",
    ]


def test_ensure_synced_tables_rejects_existing_wrong_source_catalog(monkeypatch) -> None:
    class _ExistingTable:
        database_instance_name = "mip-app-state"
        logical_database_name = "mip_app_state"
        effective_database_instance_name = "mip-app-state"
        effective_logical_database_name = "mip_app_state"
        spec = SyncedTableSpec(
            source_table_full_name="mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            _ = name
            return _ExistingTable()

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"stale existing table should not be recreated silently: {table}")

    class _Workspace:
        database = _Database()

    with pytest.raises(RuntimeError, match="expected acme_mip.gold.source_readiness"):
        provision_agentic_resources.ensure_synced_tables(
            _Workspace(),  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
        )


@pytest.mark.parametrize(
    ("database_instance", "logical_database", "message"),
    [
        ("wrong-instance", "mip_app_state", "wrong-instance; expected mip-app-state"),
        ("mip-app-state", "wrong_database", "wrong_database; expected mip_app_state"),
    ],
)
def test_ensure_synced_tables_rejects_existing_wrong_lakebase_target(
    monkeypatch,
    database_instance: str,
    logical_database: str,
    message: str,
) -> None:
    class _ExistingTable:
        spec = SyncedTableSpec(
            source_table_full_name="acme_mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

        def __init__(self) -> None:
            self.database_instance_name = None
            self.logical_database_name = None
            self.effective_database_instance_name = database_instance
            self.effective_logical_database_name = logical_database

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            _ = name
            return _ExistingTable()

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"wrong-target table should not be recreated silently: {table}")

    class _Workspace:
        database = _Database()

    with pytest.raises(RuntimeError, match=re.escape(message)):
        provision_agentic_resources.ensure_synced_tables(
            _Workspace(),  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
            table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
        )


def test_existing_registered_catalog_uses_authoritative_effective_lakebase_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExistingTable:
        database_instance_name = None
        logical_database_name = None
        effective_database_instance_name = "mip-app-state"
        effective_logical_database_name = "mip_app_state"
        spec = SyncedTableSpec(
            source_table_full_name="acme_mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            assert name == "acme_app_state.mip_sync.source_readiness"
            return _ExistingTable()

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"existing registered table must be reused: {table}")

    class _Workspace:
        database = _Database()

    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_synced_table_online",
        lambda *_args, **_kwargs: None,
    )

    assert provision_agentic_resources.ensure_synced_tables(
        _Workspace(),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        source_catalog="acme_mip",
        catalog="acme_app_state",
        schema="mip_sync",
        database_instance="mip-app-state",
        logical_database="mip_app_state",
        storage_catalog="acme_mip",
        storage_schema="app",
        timeout_s=1,
        table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
    ) == ("source_readiness",)


def test_existing_synced_table_rejects_configured_target_drift_even_when_effective_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExistingTable:
        database_instance_name = "wrong-configured-instance"
        logical_database_name = "mip_app_state"
        effective_database_instance_name = "mip-app-state"
        effective_logical_database_name = "mip_app_state"
        spec = SyncedTableSpec(
            source_table_full_name="acme_mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

    class _Database:
        def get_synced_database_table(self, _name: str) -> _ExistingTable:
            return _ExistingTable()

    class _Workspace:
        database = _Database()

    with pytest.raises(RuntimeError, match="configured for Lakebase instance"):
        provision_agentic_resources.ensure_synced_tables(
            _Workspace(),  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
            table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
        )


def test_converge_app_gateway_permissions_fails_without_app_identity() -> None:
    workspace = type(
        "Workspace",
        (),
        {"apps": type("Apps", (), {"get": lambda _self, _name: {}})()},
    )()

    with pytest.raises(RuntimeError, match="app service principal not found"):
        provision_agentic_resources._converge_app_gateway_permissions(
            workspace,  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            gateway_endpoint="mip-growth-agent-gateway",
            supervisor_endpoint="mas-agent-endpoint",
            app_name="mip-app",
        )


def test_main_defaults_ai_gateway_to_dedicated_endpoint(monkeypatch, tmp_path) -> None:
    out_env = tmp_path / "agentic.env"
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: _assert_single_writer,
    )

    monkeypatch.setattr(
        provision_agentic_resources,
        "WorkspaceClient",
        lambda: type(
            "Workspace",
            (),
            {
                "serving_endpoints": type(
                    "Endpoints",
                    (),
                    {"get": lambda _self, _name: type("Endpoint", (), {"creator": "runtime"})()},
                )()
            },
        )(),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: ("source_readiness", "segment_population"),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda **_kwargs: SupervisorAgentBinding(
            "supervisor-1",
            "Mortgage Growth Agent",
            "mip-supervisor-endpoint",
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_runtime_creator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_serving_endpoint_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "bind_gateway_runtime_resource_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_converge_app_gateway_permissions",
        lambda *_args, **_kwargs: None,
    )

    created_endpoints: list[dict[str, object]] = []

    class _Deployment:
        endpoint = "mip-growth-agent-gateway"
        inference_table = "mip.audit.mip_agent_gateway_growth_agent"
        model_name = "mip.audit.mortgage_growth_supervisor_proxy"
        model_version = 7

    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_gateway_responses_agent",
        lambda *_args, **kwargs: (created_endpoints.append(kwargs) or _Deployment()),
    )

    assert (
        provision_agentic_resources.main(
            [
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
                *_PROXY_ARGS,
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )

    assert created_endpoints == [
        {
            "endpoint": "mip-growth-agent-gateway",
            "endpoint_prefix": "mip-growth-agent-gateway",
            "supervisor_id": "supervisor-1",
            "upstream_endpoint": "mip-supervisor-endpoint",
            "model_name": "mip.audit.mortgage_growth_supervisor_proxy",
            "experiment_name": "mip-agent-runtime-gateway-proxy",
            "inference_catalog": "mip",
            "inference_schema": "audit",
            "inference_table_prefix": "mip_agent_gateway_growth_agent",
            "genie_space_id": "space-123",
            "expected_creator_application_id": "runtime",
            "proxy_caller_application_id": _PROXY_CLIENT_ID,
            "proxy_caller_credential_id": _PROXY_CREDENTIAL_ID,
            "proxy_caller_secret_reference": _PROXY_SECRET_REFERENCE,
            "deployment_app_name": "mip-app",
            "deployment_lease_id": "lease-123",
            "deployment_source_git_sha": "f" * 40,
        }
    ]
    assert "MIP_AGENT_SERVING_ENDPOINT=mip-growth-agent-gateway" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AGENT_SUPERVISOR_ENDPOINT=mip-supervisor-endpoint" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AI_GATEWAY_ENDPOINT=mip-growth-agent-gateway" in out_env.read_text(encoding="utf-8")
    assert "MIP_AI_GATEWAY_AGENT_MODEL_VERSION=7" in out_env.read_text(encoding="utf-8")


def test_main_rejects_missing_lease_before_supervisor_or_sync_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: pytest.fail("sync must not run before lease validation"),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda **_kwargs: pytest.fail("Supervisor must not mutate before lease validation"),
    )

    with pytest.raises(ValueError, match="exact source SHA"):
        provision_agentic_resources.main(
            [
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--out-env",
                str(tmp_path / "agentic.env"),
            ]
        )


def test_sync_only_main_rejects_lost_lease_before_sync_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: pytest.fail("sync must not run after lease loss"),
    )

    with pytest.raises(RuntimeError, match="lease lost"):
        provision_agentic_resources.main(
            [
                "--skip-supervisor",
                "--skip-gateway",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
            ]
        )


def test_main_reasserts_lease_after_gateway_wait_before_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    checks = 0
    binding_calls = 0

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError("lease disappeared during endpoint wait")

    workspace = type(
        "Workspace",
        (),
        {
            "serving_endpoints": type(
                "Endpoints",
                (),
                {"get": lambda _self, _name: type("Endpoint", (), {"creator": "runtime"})()},
            )()
        },
    )()
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: check,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_runtime_creator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda **_kwargs: SupervisorAgentBinding(
            "supervisor-1", "Mortgage Growth Agent", "mip-supervisor-endpoint"
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_serving_endpoint_ready",
        lambda *_args, **_kwargs: None,
    )

    class _Deployment:
        endpoint = "mip-growth-agent-gateway"
        inference_table = "mip.audit.mip_agent_gateway_growth_agent"
        model_name = "mip.audit.mortgage_growth_supervisor_proxy"
        model_version = 7

    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_gateway_responses_agent",
        lambda *_args, **_kwargs: _Deployment(),
    )

    def bind(*_args: object, **_kwargs: object) -> None:
        nonlocal binding_calls
        binding_calls += 1

    monkeypatch.setattr(
        provision_agentic_resources,
        "bind_gateway_runtime_resource_contract",
        bind,
    )

    with pytest.raises(RuntimeError, match="lease disappeared during endpoint wait"):
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
                *_PROXY_ARGS,
                "--out-env",
                str(tmp_path / "agentic.env"),
            ]
        )

    assert checks == 3
    assert binding_calls == 0


def test_isolated_skip_sync_child_rejects_unreviewed_sync_table_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    out_env = tmp_path / "agentic.env"
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)

    with pytest.raises(
        ValueError,
        match="names without reviewed source/key contracts: daily_funnel_v2, source_status_v2",
    ):
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--skip-supervisor",
                "--skip-gateway",
                "--lakebase-catalog",
                "acme_app_state",
                "--lakebase-schema",
                "acme_sync",
                "--lakebase-sync-tables",
                "source_status_v2,daily_funnel_v2",
                "--out-env",
                str(out_env),
            ]
        )
    assert not out_env.exists()


def test_main_rejects_gateway_equal_to_supervisor_before_proxy_mutation(monkeypatch) -> None:
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: _assert_single_writer,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "WorkspaceClient",
        lambda: type(
            "Workspace",
            (),
            {
                "serving_endpoints": type(
                    "Endpoints",
                    (),
                    {"get": lambda _self, _name: type("Endpoint", (), {"creator": "runtime"})()},
                )()
            },
        )(),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: ("source_readiness",),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda **_kwargs: SupervisorAgentBinding(
            "supervisor-1", "Mortgage Growth Agent", "same-endpoint"
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_runtime_creator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_serving_endpoint_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_gateway_responses_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    with pytest.raises(ValueError, match="self-recursive proxy"):
        provision_agentic_resources.main(
            [
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--gateway-endpoint",
                "same-endpoint",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
                *_PROXY_ARGS,
            ]
        )
