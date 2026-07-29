"""Adversarial tests for the authoritative Gateway lifecycle audit."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import gateway_model_lifecycle_audit as audit
from tools.databricks.gateway_model_archival import GatewayModelArchiveScope
from tools.databricks.gateway_model_lifecycle_proof import (
    consume_gateway_model_lifecycle_proof,
)
from tools.databricks.gateway_model_retirement_record import record_sha256

_RUNTIME_ID = "runtime-application-id"
_INVENTORY_PRINCIPAL = "governance@example.com"
_MODEL_FAMILY = "mip.audit.mortgage_growth_supervisor_proxy"
_BLUE_MODEL = f"{_MODEL_FAMILY}_aaaaaaaaaaaa"
_GREEN_MODEL = f"{_MODEL_FAMILY}_bbbbbbbbbbbb"
_BLUE_TABLE = "mip.audit.mip_agent_gateway_growth_agent_aaaaaaaaaaaa_payload"
_GREEN_TABLE = "mip.audit.mip_agent_gateway_growth_agent_bbbbbbbbbbbb_payload"
_BLUE_GATEWAY = {
    "name": "blue-gateway",
    "endpoint_id": "blue-gateway-id",
    "creator": _RUNTIME_ID,
}
_BLUE_SUPERVISOR = {
    "supervisor_id": "blue-supervisor-id",
    "endpoint": "blue-supervisor",
    "endpoint_id": "blue-supervisor-endpoint-id",
    "creator": _RUNTIME_ID,
}


def _scope() -> GatewayModelArchiveScope:
    return GatewayModelArchiveScope(
        app_name="mip-app",
        lease_id="11111111-1111-4111-8111-111111111111",
        source_git_sha="a" * 40,
        runtime_application_id=_RUNTIME_ID,
        app_application_id="app-application-id",
        proxy_application_id="proxy-application-id",
        verifier_application_id="verifier-application-id",
        archive_owner=_INVENTORY_PRINCIPAL,
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


def _workspace(
    *,
    caller: str = _INVENTORY_PRINCIPAL,
    metastore_id: str = "metastore-id",
) -> Any:
    models = [
        SimpleNamespace(full_name=_BLUE_MODEL, owner=_RUNTIME_ID),
        SimpleNamespace(full_name=_GREEN_MODEL, owner=_RUNTIME_ID),
    ]
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name=caller, application_id="")
        ),
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id=metastore_id)
        ),
        registered_models=SimpleNamespace(
            list=lambda **_kwargs: list(models)
        ),
        get_workspace_id=lambda: 123456789,
    )


def _allocation(
    kind: str,
    model_name: str,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    exact = dict(contract or {"model": model_name})
    return {
        "kind": kind,
        "gateway_model_name": model_name,
        "contract": exact,
        "contract_sha256": record_sha256(exact),
    }


def _install_active_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = {
        _BLUE_MODEL: [
            _allocation("endpoint-current-blue", _BLUE_MODEL),
            _allocation("cutover", _BLUE_MODEL),
        ],
        _GREEN_MODEL: [
            _allocation("endpoint-current-green", _GREEN_MODEL),
        ],
    }
    monkeypatch.setattr(
        audit,
        "_active_contracts",
        lambda *_args, **_kwargs: contracts,
    )

    def versions(
        _registry: Any,
        _tracking: Any,
        *,
        model_name: str,
        **_kwargs: Any,
    ) -> tuple[list[dict[str, str]], str, str]:
        epoch = "current" if model_name == _GREEN_MODEL else "previous"
        return (
            [{"attestation_epoch": epoch}],
            ("b" if model_name == _GREEN_MODEL else "a") * 64,
            f"experiment-{model_name[-12:]}",
        )

    def tables(
        _workspace: Any,
        *,
        model_name: str,
        **_kwargs: Any,
    ) -> tuple[list[dict[str, str]], list[str]]:
        table = _GREEN_TABLE if model_name == _GREEN_MODEL else _BLUE_TABLE
        return ([{"full_name": table}], [])

    monkeypatch.setattr(audit, "inventory_gateway_model_versions", versions)
    monkeypatch.setattr(audit, "inventory_gateway_tables", tables)


def test_blue_green_audit_binds_explicit_current_candidate_and_admin_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_active_inventory(monkeypatch)

    proof = audit.audit_gateway_model_lifecycle(
        _workspace(),
        object(),
        object(),
        scope=_scope(),
        resolve_delta_version=lambda _name: "7",
        expected_inventory_principal=_INVENTORY_PRINCIPAL,
        expected_candidate_model=_GREEN_MODEL,
    )
    consumed = consume_gateway_model_lifecycle_proof(proof)

    assert consumed.candidate_model == _GREEN_MODEL
    assert consumed.inventory_principal == _INVENTORY_PRINCIPAL
    assert consumed.metastore_id == "metastore-id"
    assert consumed.workspace_id == "123456789"
    assert consumed.by_model[_BLUE_MODEL].disposition == "active"
    assert consumed.by_model[_GREEN_MODEL].disposition == "active"
    assert consumed.by_model[_BLUE_MODEL].versions_sha256 == "a" * 64
    assert consumed.by_model[_GREEN_MODEL].versions_sha256 == "b" * 64


def test_explicit_candidate_must_have_current_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_active_inventory(monkeypatch)

    with pytest.raises(RuntimeError, match="candidate does not use the current model epoch"):
        audit.audit_gateway_model_lifecycle(
            _workspace(),
            object(),
            object(),
            scope=_scope(),
            resolve_delta_version=lambda _name: "7",
            expected_inventory_principal=_INVENTORY_PRINCIPAL,
            expected_candidate_model=_BLUE_MODEL,
        )


@pytest.mark.parametrize(
    ("caller", "metastore_id", "archive_owner"),
    [
        ("other@example.com", "metastore-id", _INVENTORY_PRINCIPAL),
        (_INVENTORY_PRINCIPAL, "", _INVENTORY_PRINCIPAL),
        (_INVENTORY_PRINCIPAL, "metastore-id", "other-owner@example.com"),
    ],
)
def test_admin_identity_rejected_before_model_inventory(
    caller: str,
    metastore_id: str,
    archive_owner: str,
) -> None:
    workspace = _workspace(caller=caller, metastore_id=metastore_id)
    inventory_called = False

    def models(**_kwargs: Any) -> list[Any]:
        nonlocal inventory_called
        inventory_called = True
        return []

    workspace.registered_models.list = models

    with pytest.raises(RuntimeError, match="admin inventory identity is not exact"):
        audit.audit_gateway_model_lifecycle(
            workspace,
            object(),
            object(),
            scope=replace(_scope(), archive_owner=archive_owner),
            resolve_delta_version=lambda _name: "7",
            expected_inventory_principal=_INVENTORY_PRINCIPAL,
            expected_candidate_model=_GREEN_MODEL,
        )

    assert inventory_called is False


def _install_contract_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    journal: dict[str, str],
    gateway_pin: dict[str, str],
    supervisor_pin: dict[str, str],
) -> None:
    endpoint_contract = {
        "gateway_endpoint": _BLUE_GATEWAY["name"],
        "gateway_endpoint_id": _BLUE_GATEWAY["endpoint_id"],
        "gateway_endpoint_creator": _BLUE_GATEWAY["creator"],
    }
    monkeypatch.setattr(
        audit,
        "_endpoint_contracts",
        lambda _workspace: [
            _allocation(
                "endpoint-current-blue",
                _BLUE_MODEL,
                endpoint_contract,
            )
        ],
    )
    monkeypatch.setattr(
        audit,
        "_registration_recovery_contracts",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        audit,
        "_load_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("no server-owned last-good App rollback contract exists")
        ),
    )
    monkeypatch.setattr(
        audit,
        "read_signed_cutover_journal",
        lambda *_args, **_kwargs: journal,
    )
    monkeypatch.setattr(
        audit,
        "json_pin_from_env",
        lambda name: (
            gateway_pin
            if name == "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON"
            else supervisor_pin
        ),
    )


def _journal(
    *,
    gateway: dict[str, str] = _BLUE_GATEWAY,
    supervisor: dict[str, str] = _BLUE_SUPERVISOR,
) -> dict[str, str]:
    return {
        "old_gateway_endpoint": gateway["name"],
        "old_gateway_endpoint_id": gateway["endpoint_id"],
        "old_gateway_creator": gateway["creator"],
        "old_id": supervisor["supervisor_id"],
        "old_endpoint": supervisor["endpoint"],
        "old_endpoint_id": supervisor["endpoint_id"],
        "old_creator": supervisor["creator"],
    }


def test_current_cutover_journal_adds_exact_signed_blue_model_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_contract_sources(
        monkeypatch,
        journal=_journal(),
        gateway_pin=_BLUE_GATEWAY,
        supervisor_pin=_BLUE_SUPERVISOR,
    )

    contracts = audit._active_contracts(
        object(),
        object(),
        object(),
        scope=_scope(),
        model_family=_MODEL_FAMILY,
    )

    assert [item["kind"] for item in contracts[_BLUE_MODEL]] == [
        "cutover",
        "endpoint-current-blue",
    ]


def test_current_cutover_journal_rejects_partial_signed_blue_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched_gateway = {
        "name": "other-gateway",
        "endpoint_id": "other-gateway-id",
        "creator": _RUNTIME_ID,
    }
    _install_contract_sources(
        monkeypatch,
        journal=_journal(gateway=mismatched_gateway),
        gateway_pin=_BLUE_GATEWAY,
        supervisor_pin=_BLUE_SUPERVISOR,
    )

    with pytest.raises(RuntimeError, match="differs from signed-blue authority"):
        audit._active_contracts(
            object(),
            object(),
            object(),
            scope=_scope(),
            model_family=_MODEL_FAMILY,
        )


def test_stale_cutover_journal_is_not_accepted_as_current_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_gateway = {
        "name": "stale-gateway",
        "endpoint_id": "stale-gateway-id",
        "creator": "stale-creator",
    }
    stale_supervisor = {
        "supervisor_id": "stale-supervisor-id",
        "endpoint": "stale-supervisor",
        "endpoint_id": "stale-supervisor-endpoint-id",
        "creator": "stale-creator",
    }
    _install_contract_sources(
        monkeypatch,
        journal=_journal(
            gateway=stale_gateway,
            supervisor=stale_supervisor,
        ),
        gateway_pin=_BLUE_GATEWAY,
        supervisor_pin=_BLUE_SUPERVISOR,
    )

    with pytest.raises(RuntimeError, match="not the current signed-blue tuple"):
        audit._active_contracts(
            object(),
            object(),
            object(),
            scope=_scope(),
            model_family=_MODEL_FAMILY,
        )
