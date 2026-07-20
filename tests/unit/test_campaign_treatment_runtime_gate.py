from __future__ import annotations

import asyncio

import pytest

import backend.services.lakebase_identity_gate as identity_gate
from backend.main import _lifespan, _require_campaign_treatment_runtime_gate


def test_campaign_treatment_runtime_gate_fails_closed_by_default_in_apps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    monkeypatch.delenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", raising=False)

    with pytest.raises(RuntimeError, match="disabled until governed access proof"):
        _require_campaign_treatment_runtime_gate()


def test_campaign_treatment_runtime_gate_accepts_only_exact_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    monkeypatch.setenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", "1")

    _require_campaign_treatment_runtime_gate()

    monkeypatch.setenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", "true")
    with pytest.raises(RuntimeError, match="disabled until governed access proof"):
        _require_campaign_treatment_runtime_gate()


def test_campaign_treatment_runtime_gate_preserves_documented_local_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_URL", raising=False)
    monkeypatch.delenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", raising=False)

    _require_campaign_treatment_runtime_gate()


def test_generic_startup_bypass_cannot_disable_apps_treatment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8000")
    monkeypatch.setenv("MIP_BYPASS_STARTUP_CHECKS", "1")
    monkeypatch.delenv("MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED", raising=False)

    async def enter_lifespan() -> None:
        async with _lifespan(None):  # type: ignore[arg-type]
            pass

    with pytest.raises(RuntimeError, match="disabled until governed access proof"):
        asyncio.run(enter_lifespan())


def test_generic_startup_bypass_cannot_disable_apps_lakebase_identity_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
