"""Deployment-promotion write gate: degrade-not-crash contract.

2026-07-16 / 2026-07-30 incident regression suite. The source-controlled
app.yaml baseline ships ``MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED=0``, so a
bare deploy (Databricks UI "Deploy" button, raw ``databricks apps deploy``)
runs without the promotion marker. The pre-restructure design raised inside
the FastAPI lifespan and crash-looped the whole App; the audited replacement
must instead:

* boot and keep serving reads + health on a baseline deploy,
* fail treatment writes closed (typed error → HTTP 503) per request, out of
  reach of ``MIP_BYPASS_STARTUP_CHECKS``,
* flip ``/api/health`` to ``degraded`` with a named reason, and
* keep the Lakebase identity gate's hard boot refusal (real identity
  misconfig is not a degradable state).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.api.health as health_mod  # noqa: F401 -- imported for monkeypatch surface parity
import backend.services.health_probes as health_probes
import backend.services.lakebase_identity_gate as identity_gate
import backend.services.resilience as resilience
from backend.main import (
    _campaign_treatment_runtime_disabled_handler,
    _lifespan,
    app,
)
from backend.schemas.portfolio import HouseholdDedupConfig
from backend.services.campaign_treatment import (
    CampaignTreatmentCoordinator,
    CampaignTreatmentCreateSpec,
)
from backend.services.campaign_treatment_runtime import (
    CAMPAIGN_TREATMENT_RUNTIME_CLIENT_DETAIL,
    CAMPAIGN_TREATMENT_RUNTIME_DISABLED,
    CAMPAIGN_TREATMENT_RUNTIME_ENABLED,
    CampaignTreatmentRuntimeDisabledError,
    campaign_treatment_runtime_enabled,
    campaign_treatment_runtime_state,
    require_campaign_treatment_runtime,
)
from backend.services.forced_degraded import _reset_forced_degraded_for_tests

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    # The health assertions read real breaker + probe-cache state; earlier
    # tests in the same process (e.g. genie failure-path suites) may leave a
    # breaker open or a stale degraded probe snapshot cached.
    resilience._reset_breakers_for_tests()
    _reset_forced_degraded_for_tests()
    health_probes._probe_cache.clear()


def _enter_lifespan() -> None:
    async def run() -> None:
        async with _lifespan(None):  # type: ignore[arg-type]
            pass

    asyncio.run(run())


def _baseline_apps_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    monkeypatch.delenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", raising=False)


def test_baseline_apps_deploy_boots_instead_of_crash_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE incident regression: lifespan must survive a marker-less deploy."""
    _baseline_apps_env(monkeypatch)
    monkeypatch.setattr(
        identity_gate, "verify_app_lakebase_identity_at_startup", lambda: None
    )

    _enter_lifespan()  # pre-fix behavior: RuntimeError killed the process here

    assert campaign_treatment_runtime_enabled() is False
    assert campaign_treatment_runtime_state() == CAMPAIGN_TREATMENT_RUNTIME_DISABLED


def test_treatment_writes_fail_closed_without_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _baseline_apps_env(monkeypatch)

    with pytest.raises(
        CampaignTreatmentRuntimeDisabledError,
        match="governed access proof",
    ):
        require_campaign_treatment_runtime()


def test_campaign_treatment_runtime_gate_accepts_only_exact_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    monkeypatch.setenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", "1")

    require_campaign_treatment_runtime()
    assert campaign_treatment_runtime_state() == CAMPAIGN_TREATMENT_RUNTIME_ENABLED

    monkeypatch.setenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", "true")
    with pytest.raises(CampaignTreatmentRuntimeDisabledError):
        require_campaign_treatment_runtime()


def test_campaign_treatment_runtime_gate_preserves_documented_local_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_URL", raising=False)
    monkeypatch.delenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", raising=False)

    require_campaign_treatment_runtime()
    assert campaign_treatment_runtime_enabled() is True


def test_generic_startup_bypass_cannot_enable_treatment_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write gate is per-request; MIP_BYPASS_STARTUP_CHECKS never reaches it."""
    _baseline_apps_env(monkeypatch)
    monkeypatch.setenv("MIP_BYPASS_STARTUP_CHECKS", "1")

    with pytest.raises(CampaignTreatmentRuntimeDisabledError):
        require_campaign_treatment_runtime()


class _RefuseAllStores:
    """Any attribute access means the gate leaked a write attempt through."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"store must not be touched on a baseline deploy: {name}")


def test_coordinator_refuses_writes_before_touching_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _baseline_apps_env(monkeypatch)

    coordinator = CampaignTreatmentCoordinator(
        lakebase=_RefuseAllStores(),  # type: ignore[arg-type]
        cohort_queries=_RefuseAllStores(),  # type: ignore[arg-type]
    )
    spec = CampaignTreatmentCreateSpec(
        name="Baseline deploy write probe",
        owner_email="ops@summit-mortgage.example",
        idempotency_key="baseline-deploy-probe",
        request_payload_hash="0" * 64,
        criteria={"segment": "in_the_money"},
        household_dedup=HouseholdDedupConfig(),
    )

    with pytest.raises(CampaignTreatmentRuntimeDisabledError):
        coordinator.create(spec)


def test_disabled_error_maps_to_structured_503() -> None:
    response = asyncio.run(
        _campaign_treatment_runtime_disabled_handler(
            None,  # type: ignore[arg-type]
            CampaignTreatmentRuntimeDisabledError("treatment runtime disabled"),
        )
    )

    assert response.status_code == 503
    payload = json.loads(bytes(response.body))
    assert payload["reason"] == "campaign_treatment_runtime_disabled"
    assert payload["retryable"] is False
    assert payload["dependency"] == "deployment"


def test_client_503_body_keeps_operator_remediation_off_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-08-07 platform audit F11.

    Across ~95 probes this was the only error body that named an internal
    repo artifact to an end user: the detail told the browser to "roll
    forward with scripts/deploy.sh". The remediation is real and operators
    need it -- it belongs in the structured log next to the correlation id,
    not in a response a customer can read. The ``reason`` code stays so
    clients and the DegradedBanner keep their machine-readable cause.
    """
    _baseline_apps_env(monkeypatch)

    with pytest.raises(CampaignTreatmentRuntimeDisabledError) as raised:
        require_campaign_treatment_runtime()

    detail = str(raised.value)
    assert detail == CAMPAIGN_TREATMENT_RUNTIME_CLIENT_DETAIL
    assert "scripts/deploy.sh" not in detail
    assert "app.yaml" not in detail
    assert "scripts/deploy.sh" in raised.value.operator_remediation

    response = asyncio.run(
        _campaign_treatment_runtime_disabled_handler(None, raised.value)  # type: ignore[arg-type]
    )
    payload = json.loads(bytes(response.body))
    assert payload["detail"] == detail
    assert "deploy.sh" not in json.dumps(payload)
    assert payload["reason"] == "campaign_treatment_runtime_disabled"
    assert payload["retryable"] is False


def test_health_reports_baseline_deploy_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare deploy is visible from health instead of a dead process."""
    _baseline_apps_env(monkeypatch)
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    anonymous = client.get("/api/health")
    assert anonymous.status_code == 200
    anon_body = anonymous.json()
    # R6-09 reconnaissance contract: value flips, keys do not.
    assert set(anon_body.keys()) == {"status", "mode"}
    assert anon_body["status"] == "degraded"

    authenticated = client.get(
        "/api/health", headers={"X-Forwarded-Email": "skyler@entrada.ai"}
    )
    assert authenticated.status_code == 200
    auth_body = authenticated.json()
    assert auth_body["status"] == "degraded"
    assert auth_body["campaign_treatment_runtime"] == CAMPAIGN_TREATMENT_RUNTIME_DISABLED


def test_health_reports_enabled_after_promoted_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    monkeypatch.setenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", "1")
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)

    authenticated = client.get(
        "/api/health", headers={"X-Forwarded-Email": "skyler@entrada.ai"}
    )
    assert authenticated.status_code == 200
    body = authenticated.json()
    assert body["status"] == "ok"
    assert body["campaign_treatment_runtime"] == CAMPAIGN_TREATMENT_RUNTIME_ENABLED


def test_generic_startup_bypass_cannot_disable_apps_lakebase_identity_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity misconfig stays a hard boot refusal — it is not degradable."""
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    monkeypatch.setenv("MIP_BYPASS_STARTUP_CHECKS", "1")
    monkeypatch.setenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", "1")
    called = False

    def fail_identity_gate() -> None:
        nonlocal called
        called = True
        raise identity_gate.LakebaseIdentityGateError("injected unsafe identity")

    monkeypatch.setattr(
        identity_gate,
        "verify_app_lakebase_identity_at_startup",
        fail_identity_gate,
    )

    async def enter_lifespan() -> None:
        async with _lifespan(None):  # type: ignore[arg-type]
            pass

    with pytest.raises(identity_gate.LakebaseIdentityGateError, match="unsafe identity"):
        asyncio.run(enter_lifespan())
    assert called is True
