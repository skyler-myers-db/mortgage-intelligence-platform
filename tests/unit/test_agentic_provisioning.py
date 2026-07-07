from __future__ import annotations

from typing import Any

import pytest
from databricks.sdk.service.database import (
    NewPipelineSpec,
    SyncedTableSchedulingPolicy,
    SyncedTableSpec,
)

from tools.databricks import provision_agentic_resources


def test_grant_app_can_query_serving_endpoint_uses_endpoint_id_and_app_sp(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any] | None]] = []

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((args, input_json))
        if args == ["apps", "get", "mip-app"]:
            return {"service_principal_client_id": "app-sp-123"}
        if args == ["serving-endpoints", "list"]:
            return [
                {"name": "other", "id": "endpoint-other"},
                {"name": "mas-agent-endpoint", "id": "endpoint-123"},
            ]
        if args == ["serving-endpoints", "update-permissions", "endpoint-123"]:
            assert input_json == {
                "access_control_list": [
                    {
                        "service_principal_name": "app-sp-123",
                        "permission_level": "CAN_QUERY",
                    }
                ]
            }
            return {"ok": True}
        raise AssertionError(f"unexpected databricks call: {args}")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)

    provision_agentic_resources._grant_app_can_query_serving_endpoint(
        endpoint="mas-agent-endpoint",
        app_name="mip-app",
    )

    assert calls[-1][0] == ["serving-endpoints", "update-permissions", "endpoint-123"]


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


def test_grant_app_can_query_serving_endpoint_skips_builtin_without_endpoint_id(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any] | None]] = []

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((args, input_json))
        if args == ["apps", "get", "mip-app"]:
            return {"service_principal_client_id": "app-sp-123"}
        if args == ["serving-endpoints", "list"]:
            return [{"name": "mip-agent-gateway", "id": None}]
        raise AssertionError(f"unexpected databricks call: {args}")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)

    provision_agentic_resources._grant_app_can_query_serving_endpoint(
        endpoint="mip-agent-gateway",
        app_name="mip-app",
    )

    assert all(call[0][:2] != ["serving-endpoints", "update-permissions"] for call in calls)


def test_ensure_ai_gateway_on_endpoint_configures_inference_table_and_user_rate_limit(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any] | None]] = []

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((args, input_json))
        if args == ["serving-endpoints", "put-ai-gateway", "mip-supervisor-endpoint"]:
            assert input_json == {
                "inference_table_config": {
                    "enabled": True,
                    "catalog_name": "mip",
                    "schema_name": "audit",
                    "table_name_prefix": "mip_agent_gateway_llama",
                },
                "rate_limits": [
                    {
                        "key": "user",
                        "calls": 42,
                        "renewal_period": "minute",
                    }
                ],
            }
            return {"ok": True}
        if args == ["serving-endpoints", "get", "mip-supervisor-endpoint"]:
            return {"state": {"ready": "READY", "config_update": "NOT_UPDATING"}}
        raise AssertionError(f"unexpected databricks call: {args}")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)

    table = provision_agentic_resources.ensure_ai_gateway_on_endpoint(
        endpoint="mip-supervisor-endpoint",
        catalog="mip",
        schema="audit",
        table_prefix="mip_agent_gateway_llama",
        per_user_calls_per_minute=42,
        timeout="1s",
    )

    assert table == "mip.audit.mip_agent_gateway_llama"
    assert calls[0][0] == ["serving-endpoints", "put-ai-gateway", "mip-supervisor-endpoint"]


def test_ensure_ai_gateway_on_endpoint_rejects_non_positive_rate_limit() -> None:
    with pytest.raises(ValueError, match="per-user rate limit"):
        provision_agentic_resources.ensure_ai_gateway_on_endpoint(
            endpoint="mip-supervisor-endpoint",
            catalog="mip",
            schema="audit",
            table_prefix="mip_agent_gateway_llama",
            per_user_calls_per_minute=0,
            timeout="1s",
        )


def test_main_defaults_ai_gateway_to_dedicated_endpoint(monkeypatch, tmp_path) -> None:
    gateway_calls: list[dict[str, object]] = []
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
        "_grant_app_can_query_serving_endpoint",
        lambda *_args, **_kwargs: None,
    )

    created_endpoints: list[dict[str, object]] = []
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_gateway_serving_endpoint",
        lambda **kwargs: created_endpoints.append(kwargs),
    )

    def fake_ensure_ai_gateway(**kwargs: object) -> str:
        gateway_calls.append(kwargs)
        return "mip.audit.mip_agent_gateway_llama"

    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_ai_gateway_on_endpoint",
        fake_ensure_ai_gateway,
    )

    assert (
        provision_agentic_resources.main(
            [
                "--genie-space-id",
                "space-123",
                "--gateway-per-user-calls-per-minute",
                "37",
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )

    assert created_endpoints and created_endpoints[0]["name"] == "mip-agent-gateway"
    assert gateway_calls == [
        {
            "endpoint": "mip-agent-gateway",
            "catalog": "mip",
            "schema": "audit",
            "table_prefix": "mip_agent_gateway_llama",
            "per_user_calls_per_minute": 37,
            "timeout": "900s",
        }
    ]
    assert "MIP_AGENT_SERVING_ENDPOINT=mip-supervisor-endpoint" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AI_GATEWAY_ENDPOINT=mip-agent-gateway" in out_env.read_text(
        encoding="utf-8"
    )


def test_ensure_ai_gateway_on_endpoint_skips_when_platform_rejects_endpoint_type(
    monkeypatch, capsys
) -> None:
    """Platform-ineligible endpoint types degrade honestly: warn + None, no raise.

    Observed live 2026-07-07: `put-ai-gateway` on the managed Supervisor Agent
    endpoint returns "AI Gateway is currently only supported for External
    Models, Provisioned Throughput and Custom or Agent Model endpoints." The
    deploy must continue and the capability must self-report non-claimable —
    the gate itself is never relaxed.
    """

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        assert args[:2] == ["serving-endpoints", "put-ai-gateway"]
        raise RuntimeError(
            "databricks serving-endpoints put-ai-gateway mas-x failed: Error: "
            "AI Gateway is currently only supported for External Models, "
            "Provisioned Throughput and Custom or Agent Model endpoints."
        )

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)
    result = provision_agentic_resources.ensure_ai_gateway_on_endpoint(
        endpoint="mas-x",
        catalog="mip",
        schema="audit",
        table_prefix="mip_agent_gateway_llama",
        per_user_calls_per_minute=60,
        timeout="60s",
    )
    assert result is None
    out = capsys.readouterr().out
    assert "WARNING" in out and "non-claimable" in out


def test_ensure_ai_gateway_on_endpoint_still_raises_on_other_errors(monkeypatch) -> None:
    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        raise RuntimeError("databricks serving-endpoints put-ai-gateway mas-x failed: PERMISSION_DENIED")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)
    try:
        provision_agentic_resources.ensure_ai_gateway_on_endpoint(
            endpoint="mas-x",
            catalog="mip",
            schema="audit",
            table_prefix="mip_agent_gateway_llama",
            per_user_calls_per_minute=60,
            timeout="60s",
        )
    except RuntimeError as exc:
        assert "PERMISSION_DENIED" in str(exc)
    else:  # pragma: no cover - the assertion above must fire
        raise AssertionError("non-eligibility errors must still raise")


def test_ensure_gateway_serving_endpoint_creates_when_missing(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any] | None]] = []

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((args, input_json))
        if args[:2] == ["serving-endpoints", "get"]:
            raise RuntimeError("serving-endpoints get mip-agent-gateway failed: does not exist")
        return {"name": "mip-agent-gateway"}

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)
    provision_agentic_resources.ensure_gateway_serving_endpoint(
        name="mip-agent-gateway",
        entity_name="system.ai.llama_v3_2_3b_instruct",
        entity_version="1",
    )
    create = next(c for c in calls if c[0][:2] == ["serving-endpoints", "create"])
    entity = create[1]["config"]["served_entities"][0]
    assert entity["entity_name"] == "system.ai.llama_v3_2_3b_instruct"
    assert entity["entity_version"] == "1"
    assert entity["scale_to_zero_enabled"] is True


def test_ensure_gateway_serving_endpoint_reuses_existing(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append(args)
        return {"name": "mip-agent-gateway"}

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)
    provision_agentic_resources.ensure_gateway_serving_endpoint(
        name="mip-agent-gateway",
        entity_name="system.ai.llama_v3_2_3b_instruct",
        entity_version="1",
    )
    assert all(c[:2] != ["serving-endpoints", "create"] for c in calls)


def test_ensure_ai_gateway_on_endpoint_survives_slow_endpoint_warmup(monkeypatch, capsys) -> None:
    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True}

    def slow_ready(endpoint: str, *, timeout: str) -> None:
        raise RuntimeError(f"endpoint {endpoint} not READY within {timeout}")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)
    monkeypatch.setattr(provision_agentic_resources, "_wait_serving_endpoint_ready", slow_ready)
    result = provision_agentic_resources.ensure_ai_gateway_on_endpoint(
        endpoint="mip-agent-gateway",
        catalog="mip",
        schema="audit",
        table_prefix="mip_agent_gateway_llama",
        per_user_calls_per_minute=60,
        timeout="60s",
    )
    assert result == "mip.audit.mip_agent_gateway_llama"
    assert "WARNING" in capsys.readouterr().out
