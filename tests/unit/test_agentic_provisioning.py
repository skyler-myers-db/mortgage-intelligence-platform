from __future__ import annotations

from typing import Any

import pytest
from databricks.sdk.service.database import (
    NewPipelineSpec,
    SyncedTableSchedulingPolicy,
    SyncedTableSpec,
)

from tools.databricks import provision_agentic_resources


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


def test_ensure_synced_tables_validates_existing_source_catalog(monkeypatch) -> None:
    checked: list[str] = []

    class _ExistingTable:
        def __init__(self, source: str, keys: list[str]) -> None:
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
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
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
            gateway_endpoint="mip-growth-agent-gateway",
            supervisor_endpoint="mas-agent-endpoint",
            app_name="mip-app",
        )


def test_main_defaults_ai_gateway_to_dedicated_endpoint(monkeypatch, tmp_path) -> None:
    out_env = tmp_path / "agentic.env"

    monkeypatch.setattr(
        provision_agentic_resources,
        "WorkspaceClient",
        lambda: object(),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: ("source_readiness", "segment_population"),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda **_kwargs: ("supervisor-1", "mip-supervisor-endpoint"),
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
        "_converge_app_gateway_permissions",
        lambda *_args, **_kwargs: None,
    )

    created_endpoints: list[dict[str, object]] = []

    class _Deployment:
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
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )

    assert created_endpoints == [
        {
            "endpoint": "mip-growth-agent-gateway",
            "upstream_endpoint": "mip-supervisor-endpoint",
            "model_name": "mip.audit.mortgage_growth_supervisor_proxy",
            "experiment_name": "/Shared/mip/agent-gateway-proxy",
            "inference_catalog": "mip",
            "inference_schema": "audit",
            "inference_table_prefix": "mip_agent_gateway_growth_agent",
        }
    ]
    assert "MIP_AGENT_SERVING_ENDPOINT=mip-growth-agent-gateway" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AGENT_SUPERVISOR_ENDPOINT=mip-supervisor-endpoint" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AI_GATEWAY_ENDPOINT=mip-growth-agent-gateway" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AI_GATEWAY_AGENT_MODEL_VERSION=7" in out_env.read_text(encoding="utf-8")


def test_main_rejects_gateway_equal_to_supervisor_before_proxy_mutation(monkeypatch) -> None:
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", lambda: object())
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: ("source_readiness",),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda **_kwargs: ("supervisor-1", "same-endpoint"),
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
            ["--genie-space-id", "space-123", "--gateway-endpoint", "same-endpoint"]
        )
