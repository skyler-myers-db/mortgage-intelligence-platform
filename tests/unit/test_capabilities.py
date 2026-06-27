"""Contract for the DAIS-2026 capability probe + ``/api/admin/capabilities``.

The probe is the enforcement point for the no-overclaim posture: a feature flag
turned on without its backing dependency must resolve to ``not_provisioned``
(claimable=False), and preview-only capabilities must resolve to
``preview_mirror``/``hidden`` (claimable=False) — never to an "integrated"
claim. These tests pin that behaviour against the real probe logic.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.config.settings import Settings
from backend.main import app
from backend.services.capabilities import (
    CapabilityStatus,
    get_capabilities_snapshot,
    probe_capabilities,
)


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
            expected = (
                CapabilityStatus.PREVIEW_MIRROR if mirror else CapabilityStatus.HIDDEN
            )
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


def test_metric_certification_and_uc_agent_tools_need_concrete_contracts() -> None:
    caps = probe_capabilities(_settings())
    for key in ("certified_metric_views", "uc_function_tools"):
        cap = _by_key(caps, key)
        assert cap.ga is True
        assert cap.status is CapabilityStatus.NOT_PROVISIONED
        assert cap.claimable is False


def test_genie_conversation_needs_space_and_warehouse() -> None:
    ok = _by_key(probe_capabilities(_settings()), "genie_conversation_api")
    assert ok.status is CapabilityStatus.AVAILABLE and ok.claimable is True
    missing = _by_key(
        probe_capabilities(_settings(genie_space_id=None)), "genie_conversation_api"
    )
    assert missing.status is CapabilityStatus.NOT_PROVISIONED
    assert missing.claimable is False
    placeholder = _by_key(
        probe_capabilities(_settings(genie_space_id="00000000PLACEHOLDER")), "genie_conversation_api"
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
    assert gateway.status is CapabilityStatus.NOT_PROVISIONED
    assert gateway.claimable is False
    assert "live signal probe is not implemented" in gateway.detail


def test_warehouse_placeholder_is_not_provisioned() -> None:
    caps = probe_capabilities(
        _settings(databricks_host="<workspace-host>.cloud.databricks.com")
    )
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


def test_capabilities_endpoint_requires_admin() -> None:
    client = TestClient(app)
    resp = client.get("/api/admin/capabilities", headers={"X-Forwarded-Groups": ""})
    assert resp.status_code == 403
