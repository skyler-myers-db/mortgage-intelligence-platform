"""Contract for the DAIS-2026 capability probe + ``/api/admin/capabilities``.

The probe is the enforcement point for the no-overclaim posture: a feature flag
turned on without its backing dependency must resolve to ``not_provisioned``
(claimable=False), and preview-only capabilities must resolve to
``preview_mirror``/``hidden`` (claimable=False) — never to an "integrated"
claim. These tests pin that behaviour against the real probe logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.services.capabilities as capabilities_module
from backend.config.settings import Settings
from backend.main import app
from backend.services.capabilities import (
    CapabilityStatus,
    LiveCapabilityStatus,
    collect_live_capability_statuses,
    get_capabilities_snapshot,
    probe_capabilities,
)
from backend.services.databricks_sql import get_sql_client
from backend.services.genie_client import get_genie_client
from backend.services.lakebase import get_lakebase_client

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "databricks_host": "dbc-test.cloud.databricks.com",
        "databricks_warehouse_id": "wh-123",
        "genie_space_id": "space-abc",
        "lakebase_host": "lb-test",
        "lakebase_user": "mip_app",
    }
    base.update(overrides)
    return Settings(**base)


def _by_key(caps: list, key: str):
    return next(cap for cap in caps if cap.key == key)


class _LiveSqlClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.statements: list[str] = []

    def execute(self, statement: str, parameters: object | None = None) -> list[dict[str, object]]:
        self.statements.append(statement)
        if self.fail:
            raise RuntimeError("probe failed")
        return [{"ok": 1}]


class _LiveGenieClient:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok

    def ping(self) -> bool:
        return self.ok


class _LiveLakebase:
    pass


class _SyncStatus:
    detailed_state = "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE"


class _SyncedTable:
    data_synchronization_status = _SyncStatus()


class _FakeDatabaseApi:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def get_synced_database_table(self, name: str) -> _SyncedTable:
        self.requested.append(name)
        return _SyncedTable()


class _FakeWorkspaceClient:
    def __init__(self) -> None:
        self.database = _FakeDatabaseApi()


def test_preview_capabilities_are_never_claimable() -> None:
    """CustomerLake / App Spaces / Lakehouse//RT / declarative agents / glossary
    / ontology must never present as integrated, regardless of mirror flag."""
    preview_keys = {
        "genie_ontology",
        "customerlake",
        "app_spaces_microapps",
        "lakehouse_rt",
        "declarative_genie_agents",
        "uc_glossary_domains",
    }
    for mirror in (True, False):
        caps = probe_capabilities(_settings(mip_preview_mirror=mirror))
        for key in preview_keys:
            cap = _by_key(caps, key)
            assert cap.ga is False
            assert cap.claimable is False
            expected = CapabilityStatus.PREVIEW_MIRROR if mirror else CapabilityStatus.HIDDEN
            assert cap.status is expected, f"{key} -> {cap.status}"


def test_orchestrator_flag_on_without_libs_is_not_provisioned() -> None:
    """databricks-agents / mlflow>=3.1.3 are not installed in CI, so flipping the
    flag on must NOT yield a claimable capability — it surfaces honestly."""
    caps = probe_capabilities(_settings(mip_agent_orchestrator=True))
    orch = _by_key(caps, "agent_orchestrator")
    assert orch.ga is True
    assert orch.status is CapabilityStatus.NOT_PROVISIONED
    assert orch.claimable is False


def test_orchestrator_flag_off_is_not_provisioned() -> None:
    caps = probe_capabilities(_settings(mip_agent_orchestrator=False))
    orch = _by_key(caps, "agent_orchestrator")
    assert orch.status is CapabilityStatus.NOT_PROVISIONED
    assert "off" in orch.detail.lower()


def test_metric_certification_and_uc_agent_tools_are_configured_not_claimed() -> None:
    caps = probe_capabilities(_settings())
    for key in ("certified_metric_views", "uc_function_tools"):
        cap = _by_key(caps, key)
        assert cap.ga is True
        assert cap.status is CapabilityStatus.CONFIGURED
        assert cap.claimable is False
        assert "live" in cap.detail.lower()


def test_live_statuses_upgrade_configured_rows_only() -> None:
    caps = probe_capabilities(
        _settings(mip_lakebase_sync=True),
        live_statuses={
            "genie_conversation_api": LiveCapabilityStatus(True, "Genie live."),
            "certified_metric_views": LiveCapabilityStatus(True, "Metric views live."),
            "uc_function_tools": LiveCapabilityStatus(True, "UC tools live."),
            "lakebase_sync": LiveCapabilityStatus(True, "Lakebase sync live."),
            # Preview/no-public-API rows must ignore live evidence and stay non-claimable.
            "customerlake": LiveCapabilityStatus(True, "Should not matter."),
        },
    )

    for key in (
        "genie_conversation_api",
        "certified_metric_views",
        "uc_function_tools",
    ):
        cap = _by_key(caps, key)
        assert cap.status is CapabilityStatus.AVAILABLE
        assert cap.claimable is True
        assert "live" in cap.detail.lower()
    sync = _by_key(caps, "lakebase_sync")
    assert sync.status is CapabilityStatus.AVAILABLE
    assert sync.claimable is True
    assert "lakebase sync live" in sync.detail.lower()
    customerlake = _by_key(caps, "customerlake")
    assert customerlake.claimable is False


def test_live_status_failed_probe_stays_configured_not_claimable() -> None:
    caps = probe_capabilities(
        _settings(),
        live_statuses={
            "genie_conversation_api": LiveCapabilityStatus(False, "Genie ping failed."),
            "certified_metric_views": LiveCapabilityStatus(False, "Query failed."),
            "uc_function_tools": LiveCapabilityStatus(False, "Function failed."),
        },
    )

    for key in ("genie_conversation_api", "certified_metric_views", "uc_function_tools"):
        cap = _by_key(caps, key)
        assert cap.status is CapabilityStatus.CONFIGURED
        assert cap.claimable is False
        assert "did not pass" in cap.detail.lower()


def test_live_capability_probe_executes_exact_reviewed_contracts() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        sql_client=sql,
        genie_client=_LiveGenieClient(ok=True),
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_conversation_api"].available is True
    assert statuses["certified_metric_views"].available is True
    assert statuses["uc_function_tools"].available is True
    assert "lakebase_sync" not in statuses
    assert len(sql.statements) == 6
    assert any(
        "semantics.certified_lead_generation_metric_view" in statement
        for statement in sql.statements
    )
    assert not any(
        "semantics.lead_generation_metric_view" in statement
        and "semantics.certified_lead_generation_metric_view" not in statement
        for statement in sql.statements
    )
    assert any(".gold.fn_build_cohort(" in statement for statement in sql.statements)


def test_live_capability_probe_failures_are_captured() -> None:
    statuses = collect_live_capability_statuses(
        sql_client=_LiveSqlClient(fail=True),
        genie_client=_LiveGenieClient(ok=False),
        lakebase=_LiveLakebase(),
    )

    assert statuses["genie_conversation_api"].available is False
    assert statuses["certified_metric_views"].available is False
    assert statuses["uc_function_tools"].available is False


def test_lakebase_sync_live_probe_requires_synced_table_metadata() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(mip_lakebase_sync=True, mip_lakebase_sync_tables="source_readiness"),
        sql_client=sql,
        lakebase=_LiveLakebase(),
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["lakebase_sync"].available is True
    assert "source_readiness" in sql.statements[-1]


def test_lakebase_sync_capability_does_not_require_unused_lakebase_user() -> None:
    caps = probe_capabilities(
        _settings(
            lakebase_user=None,
            mip_lakebase_sync=True,
            mip_lakebase_sync_tables="source_readiness",
        ),
        live_statuses={"lakebase_sync": LiveCapabilityStatus(True, "Synced tables live.")},
    )

    sync = _by_key(caps, "lakebase_sync")
    assert sync.status is CapabilityStatus.AVAILABLE
    assert sync.claimable is True
    assert sync.detail == "Synced tables live."


def test_lakebase_sync_live_probe_runs_without_lakebase_client() -> None:
    sql = _LiveSqlClient()
    statuses = collect_live_capability_statuses(
        settings=_settings(mip_lakebase_sync=True, mip_lakebase_sync_tables="source_readiness"),
        sql_client=sql,
        lakebase=None,
        workspace_client=_FakeWorkspaceClient(),
    )

    assert statuses["lakebase_sync"].available is True
    assert "mip.gold.fn_build_cohort" not in sql.statements[-1]
    assert "source_readiness" in sql.statements[-1]


def test_certified_metric_view_sql_contracts_are_present() -> None:
    metric_dir = _REPO_ROOT / "sql" / "metric_views"
    expected = {
        "certified_borrower_opportunity_metric_view.sql",
        "certified_lead_generation_metric_view.sql",
        "certified_segment_performance_metric_view.sql",
    }
    for filename in expected:
        text = (metric_dir / filename).read_text(encoding="utf-8").lower()
        assert "with metrics" in text
        assert "language yaml" in text
        assert "certification:" in text
        assert "synonyms:" in text


def test_uc_growth_agent_tool_sql_contracts_are_present() -> None:
    function_dir = _REPO_ROOT / "sql" / "uc_functions"
    expected = {
        "fn_build_cohort.sql",
        "fn_segment_counts.sql",
        "fn_lead_queue_url.sql",
    }
    for filename in expected:
        text = (function_dir / filename).read_text(encoding="utf-8").lower()
        assert "create or replace function" in text
        assert "mortgage growth agent" in text
        assert "read-only" in text or "no outreach or state write" in text


def test_growth_agent_function_grants_are_documented_and_deployed() -> None:
    deploy = (_REPO_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    grants = (_REPO_ROOT / "docs" / "security" / "GRANTS.md").read_text(encoding="utf-8")
    for function_name in ("fn_build_cohort", "fn_segment_counts", "fn_lead_queue_url"):
        grant = "GRANT EXECUTE ON FUNCTION"
        assert grant in deploy
        assert f".gold.{function_name}" in deploy
        assert grant in grants
        assert f"mip.gold.{function_name}" in grants


def test_genie_conversation_needs_space_and_warehouse() -> None:
    ok = _by_key(probe_capabilities(_settings()), "genie_conversation_api")
    assert ok.status is CapabilityStatus.CONFIGURED
    assert ok.claimable is False
    assert "live genie probe" in ok.detail.lower()
    missing = _by_key(probe_capabilities(_settings(genie_space_id=None)), "genie_conversation_api")
    assert missing.status is CapabilityStatus.NOT_PROVISIONED
    assert missing.claimable is False
    placeholder = _by_key(
        probe_capabilities(_settings(genie_space_id="00000000PLACEHOLDER")),
        "genie_conversation_api",
    )
    assert placeholder.status is CapabilityStatus.NOT_PROVISIONED
    assert placeholder.claimable is False


def test_ai_gateway_and_lakebase_sync_gated_by_flags() -> None:
    off = probe_capabilities(_settings())
    assert _by_key(off, "ai_gateway").status is CapabilityStatus.NOT_PROVISIONED
    assert _by_key(off, "lakebase_sync").status is CapabilityStatus.NOT_PROVISIONED
    on = probe_capabilities(_settings(mip_ai_gateway=True, mip_lakebase_sync=True))
    assert _by_key(on, "ai_gateway").status is CapabilityStatus.NOT_PROVISIONED
    assert _by_key(on, "lakebase_sync").status is CapabilityStatus.CONFIGURED


def test_ai_gateway_requires_endpoint_and_inference_table_config() -> None:
    caps = probe_capabilities(
        _settings(
            mip_ai_gateway=True,
            mip_ai_gateway_endpoint="mip-agent-gateway",
            mip_ai_gateway_inference_table="mip_app.agent_inference_logs",
        )
    )
    gateway = _by_key(caps, "ai_gateway")
    assert gateway.status is CapabilityStatus.CONFIGURED
    assert gateway.claimable is False
    assert "live" in gateway.detail.lower()


def test_warehouse_placeholder_is_not_provisioned() -> None:
    caps = probe_capabilities(_settings(databricks_host="<workspace-host>.cloud.databricks.com"))
    assert _by_key(caps, "certified_metric_views").status is CapabilityStatus.NOT_PROVISIONED


def test_snapshot_to_dict_shape() -> None:
    caps = get_capabilities_snapshot(refresh=True)
    assert caps, "snapshot must be non-empty"
    row = caps[0].to_dict()
    assert set(row) == {"key", "label", "ga", "status", "claimable", "detail"}
    assert isinstance(row["claimable"], bool)


def test_capabilities_endpoint_admin_gated_and_shaped() -> None:
    client = TestClient(app)  # conftest stamps the admin group header
    resp = client.get("/api/admin/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert "capabilities" in body and body["capabilities"]
    keys = {row["key"] for row in body["capabilities"]}
    assert {"genie_conversation_api", "agent_orchestrator", "customerlake"} <= keys
    # Every preview row must be non-claimable in the live (flag-default) app.
    for row in body["capabilities"]:
        if row["ga"] is False:
            assert row["claimable"] is False


def test_capabilities_endpoint_live_probe_marks_live_dependencies_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capabilities_module, "get_settings", lambda: _settings())
    app.dependency_overrides[get_sql_client] = lambda: _LiveSqlClient()
    app.dependency_overrides[get_genie_client] = lambda: _LiveGenieClient(ok=True)
    app.dependency_overrides[get_lakebase_client] = lambda: _LiveLakebase()
    try:
        client = TestClient(app)
        resp = client.get("/api/admin/capabilities?live=1")
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_genie_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)

    assert resp.status_code == 200
    rows = {row["key"]: row for row in resp.json()["capabilities"]}
    assert rows["genie_conversation_api"]["status"] == "available"
    assert rows["certified_metric_views"]["status"] == "available"
    assert rows["uc_function_tools"]["status"] == "available"
    assert rows["genie_ontology"]["claimable"] is False


def test_capabilities_endpoint_requires_admin() -> None:
    client = TestClient(app)
    resp = client.get("/api/admin/capabilities", headers={"X-Forwarded-Groups": ""})
    assert resp.status_code == 403
