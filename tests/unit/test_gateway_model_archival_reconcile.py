"""Race-focused tests for authoritative Gateway family reconciliation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import gateway_model_archival_reconcile as reconcile
from tools.databricks.gateway_model_archival import GatewayModelArchiveScope
from tools.databricks.gateway_model_retirement_record import record_sha256

_RUNTIME_ID = "runtime-application-id"
_ARCHIVE_OWNER = "governance@example.com"
_MODEL_FAMILY = "mip.audit.mortgage_growth_supervisor_proxy"
_PROTECTED = f"{_MODEL_FAMILY}_aaaaaaaaaaaa"
_HISTORICAL = f"{_MODEL_FAMILY}_bbbbbbbbbbbb"
_CONCURRENT = f"{_MODEL_FAMILY}_cccccccccccc"


def _scope() -> GatewayModelArchiveScope:
    return GatewayModelArchiveScope(
        app_name="mip-app",
        lease_id="11111111-1111-4111-8111-111111111111",
        source_git_sha="a" * 40,
        runtime_application_id=_RUNTIME_ID,
        app_application_id="app-application-id",
        proxy_application_id="proxy-application-id",
        verifier_application_id="verifier-application-id",
        archive_owner=_ARCHIVE_OWNER,
        governance_group="mortgage-governance",
        catalog="mip",
        model_family=_MODEL_FAMILY,
        experiment_base="mip-agent-runtime-gateway-proxy",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        rollback_scope="production",
        expected_lakebase_instance="mip-lakebase",
        warehouse_id="warehouse-id",
    )


def _protected_contract(*, revision: str = "1") -> tuple[dict[str, Any], ...]:
    contract = {"gateway_model_name": _PROTECTED, "revision": revision}
    return (
        {
            "kind": "endpoint-current-green",
            "gateway_model_name": _PROTECTED,
            "contract": contract,
            "contract_sha256": record_sha256(contract),
        },
    )


class _ModelInventory:
    def __init__(self) -> None:
        self.owners = {
            _PROTECTED: _RUNTIME_ID,
            _HISTORICAL: _RUNTIME_ID,
        }
        self.snapshots: list[dict[str, str]] | None = None
        self.calls = 0

    def list(self, **_kwargs: Any) -> list[Any]:
        self.calls += 1
        owners = (
            self.snapshots[min(self.calls - 1, len(self.snapshots) - 1)]
            if self.snapshots is not None
            else self.owners
        )
        return [
            SimpleNamespace(full_name=name, owner=owner)
            for name, owner in owners.items()
        ]


def _workspace(inventory: _ModelInventory) -> Any:
    return SimpleNamespace(registered_models=inventory)


def _install_protection(
    monkeypatch: pytest.MonkeyPatch,
    snapshots: list[tuple[dict[str, Any], ...]],
) -> None:
    calls = 0

    def discover(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], ...]:
        nonlocal calls
        snapshot = snapshots[min(calls, len(snapshots) - 1)]
        calls += 1
        return snapshot

    monkeypatch.setattr(
        reconcile,
        "discover_protected_allocation_contracts",
        discover,
    )


def test_reconcile_zero_models_and_zero_protection_is_exact_rechecked_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _ModelInventory()
    inventory.owners = {}
    _install_protection(monkeypatch, [(), ()])
    archive_calls: list[str] = []
    monkeypatch.setattr(
        reconcile,
        "archive_gateway_model",
        lambda *_args, model_name, **_kwargs: archive_calls.append(model_name),
    )

    completions = reconcile.archive_unprotected_gateway_models(
        _workspace(inventory),
        object(),
        object(),
        scope=_scope(),
        resolve_delta_version=lambda _name: "7",
    )

    assert completions == ()
    assert inventory.calls == 2
    assert archive_calls == []


@pytest.mark.parametrize(
    ("owners", "protection"),
    [
        ({_PROTECTED: _RUNTIME_ID}, ()),
        ({}, _protected_contract()),
    ],
)
def test_reconcile_rejects_asymmetric_model_and_protection_inventory(
    monkeypatch: pytest.MonkeyPatch,
    owners: dict[str, str],
    protection: tuple[dict[str, Any], ...],
) -> None:
    inventory = _ModelInventory()
    inventory.owners = owners
    _install_protection(monkeypatch, [protection])
    archive_calls: list[str] = []
    monkeypatch.setattr(
        reconcile,
        "archive_gateway_model",
        lambda *_args, model_name, **_kwargs: archive_calls.append(model_name),
    )

    with pytest.raises(RuntimeError, match="reconciliation protection is incomplete"):
        reconcile.archive_unprotected_gateway_models(
            _workspace(inventory),
            object(),
            object(),
            scope=_scope(),
            resolve_delta_version=lambda _name: "7",
        )

    assert archive_calls == []


def test_reconcile_archives_then_repostflights_every_historical_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _ModelInventory()
    _install_protection(
        monkeypatch,
        [_protected_contract(), _protected_contract()],
    )
    calls: list[str] = []

    def archive(
        _workspace: Any,
        _registry: Any,
        _tracking: Any,
        *,
        model_name: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(model_name)
        inventory.owners[model_name] = _ARCHIVE_OWNER
        return {"model_name": model_name, "phase": "completed"}

    monkeypatch.setattr(reconcile, "archive_gateway_model", archive)

    completions = reconcile.archive_unprotected_gateway_models(
        _workspace(inventory),
        object(),
        object(),
        scope=_scope(),
        resolve_delta_version=lambda _name: "7",
    )

    assert calls == [_HISTORICAL, _HISTORICAL]
    assert completions == (
        {"model_name": _HISTORICAL, "phase": "completed"},
    )
    assert inventory.calls == 2
    assert inventory.owners == {
        _PROTECTED: _RUNTIME_ID,
        _HISTORICAL: _ARCHIVE_OWNER,
    }


def test_reconcile_rejects_concurrent_new_family_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _ModelInventory()
    inventory.snapshots = [
        {
            _PROTECTED: _RUNTIME_ID,
            _HISTORICAL: _RUNTIME_ID,
        },
        {
            _PROTECTED: _RUNTIME_ID,
            _HISTORICAL: _ARCHIVE_OWNER,
            _CONCURRENT: _RUNTIME_ID,
        },
    ]
    _install_protection(
        monkeypatch,
        [_protected_contract(), _protected_contract()],
    )
    monkeypatch.setattr(
        reconcile,
        "archive_gateway_model",
        lambda *_args, model_name, **_kwargs: {
            "model_name": model_name,
            "phase": "completed",
        },
    )

    with pytest.raises(RuntimeError, match="reconciliation inventory changed"):
        reconcile.archive_unprotected_gateway_models(
            _workspace(inventory),
            object(),
            object(),
            scope=_scope(),
            resolve_delta_version=lambda _name: "7",
        )


def test_reconcile_rejects_concurrent_protection_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = _ModelInventory()
    _install_protection(
        monkeypatch,
        [_protected_contract(revision="1"), _protected_contract(revision="2")],
    )

    def archive(
        *_args: Any,
        model_name: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        inventory.owners[model_name] = _ARCHIVE_OWNER
        return {"model_name": model_name, "phase": "completed"}

    monkeypatch.setattr(reconcile, "archive_gateway_model", archive)

    with pytest.raises(RuntimeError, match="reconciliation inventory changed"):
        reconcile.archive_unprotected_gateway_models(
            _workspace(inventory),
            object(),
            object(),
            scope=_scope(),
            resolve_delta_version=lambda _name: "7",
        )


@pytest.mark.parametrize(
    "final_owners",
    [
        {
            _PROTECTED: "other-runtime",
            _HISTORICAL: _ARCHIVE_OWNER,
        },
        {
            _PROTECTED: _RUNTIME_ID,
            _HISTORICAL: _RUNTIME_ID,
        },
    ],
)
def test_reconcile_rejects_final_owner_drift(
    monkeypatch: pytest.MonkeyPatch,
    final_owners: dict[str, str],
) -> None:
    inventory = _ModelInventory()
    inventory.snapshots = [
        {
            _PROTECTED: _RUNTIME_ID,
            _HISTORICAL: _RUNTIME_ID,
        },
        final_owners,
    ]
    _install_protection(
        monkeypatch,
        [_protected_contract(), _protected_contract()],
    )
    monkeypatch.setattr(
        reconcile,
        "archive_gateway_model",
        lambda *_args, model_name, **_kwargs: {
            "model_name": model_name,
            "phase": "completed",
        },
    )

    with pytest.raises(RuntimeError, match="reconciliation inventory changed"):
        reconcile.archive_unprotected_gateway_models(
            _workspace(inventory),
            object(),
            object(),
            scope=_scope(),
            resolve_delta_version=lambda _name: "7",
        )
