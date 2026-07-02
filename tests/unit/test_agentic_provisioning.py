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
            return [{"name": "databricks-claude-sonnet-4-5", "id": None}]
        raise AssertionError(f"unexpected databricks call: {args}")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)

    provision_agentic_resources._grant_app_can_query_serving_endpoint(
        endpoint="databricks-claude-sonnet-4-5",
        app_name="mip-app",
    )

    assert all(call[0][:2] != ["serving-endpoints", "update-permissions"] for call in calls)
