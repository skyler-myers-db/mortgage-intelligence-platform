"""Security-boundary tests for the opaque Gateway model lifecycle proof."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tools.databricks.gateway_model_lifecycle_proof import (
    GatewayModelLifecycleProof,
    GatewayModelLifecycleState,
    _issue_gateway_model_lifecycle_proof,
    consume_gateway_model_lifecycle_proof,
)

_APPLICATION_ID = "runtime-application-id"
_INVENTORY_PRINCIPAL = "deployer@example.com"
_CATALOG = "mip"
_METASTORE_ID = "metastore-id"
_WORKSPACE_ID = "workspace-id"
_MODEL_FAMILY = f"{_CATALOG}.audit.mortgage_growth_supervisor_proxy"
_ARCHIVED_MODEL = f"{_MODEL_FAMILY}_aaaaaaaaaaaa"
_CANDIDATE_MODEL = f"{_MODEL_FAMILY}_bbbbbbbbbbbb"
_VERSION_DIGEST = "1" * 64
_RETIREMENT_DIGEST = "2" * 64


def _active_state(**changes: Any) -> GatewayModelLifecycleState:
    state = GatewayModelLifecycleState(
        model_name=_CANDIDATE_MODEL,
        owner=_APPLICATION_ID,
        disposition="active",
        versions_sha256=_VERSION_DIGEST,
        inference_tables=(f"{_CATALOG}.audit.gateway_payload_bbbbbbbbbbbb",),
        active_contract_json='{"allocation":"current"}',
        retirement_record_sha256="",
    )
    return replace(state, **changes)


def _archived_state(**changes: Any) -> GatewayModelLifecycleState:
    state = GatewayModelLifecycleState(
        model_name=_ARCHIVED_MODEL,
        owner="governance@example.com",
        disposition="archived",
        versions_sha256=_VERSION_DIGEST,
        inference_tables=(f"{_CATALOG}.audit.gateway_payload_aaaaaaaaaaaa",),
        active_contract_json="",
        retirement_record_sha256=_RETIREMENT_DIGEST,
    )
    return replace(state, **changes)


def _issue(
    *,
    states: tuple[GatewayModelLifecycleState, ...] | None = None,
    **changes: Any,
) -> GatewayModelLifecycleProof:
    values: dict[str, Any] = {
        "application_id": _APPLICATION_ID,
        "inventory_principal": _INVENTORY_PRINCIPAL,
        "catalog": _CATALOG,
        "metastore_id": _METASTORE_ID,
        "workspace_id": _WORKSPACE_ID,
        "model_family": _MODEL_FAMILY,
        "candidate_model": _CANDIDATE_MODEL,
        "states": states or (_archived_state(), _active_state()),
    }
    values.update(changes)
    return _issue_gateway_model_lifecycle_proof(**values)


def test_issued_lifecycle_proof_consumes_once_with_exact_snapshot() -> None:
    proof = _issue()

    consumed = consume_gateway_model_lifecycle_proof(proof)

    assert (
        consumed.application_id,
        consumed.inventory_principal,
        consumed.catalog,
        consumed.metastore_id,
        consumed.workspace_id,
        consumed.model_family,
        consumed.candidate_model,
    ) == (
        _APPLICATION_ID,
        _INVENTORY_PRINCIPAL,
        _CATALOG,
        _METASTORE_ID,
        _WORKSPACE_ID,
        _MODEL_FAMILY,
        _CANDIDATE_MODEL,
    )
    assert consumed.by_model == {
        _ARCHIVED_MODEL: _archived_state(),
        _CANDIDATE_MODEL: _active_state(),
    }
    with pytest.raises(RuntimeError, match="was not issued by the admin auditor"):
        consume_gateway_model_lifecycle_proof(proof)


def test_caller_constructed_lifecycle_proof_lookalike_is_rejected() -> None:
    lookalike = object.__new__(GatewayModelLifecycleProof)
    for name, value in (
        ("application_id", _APPLICATION_ID),
        ("inventory_principal", _INVENTORY_PRINCIPAL),
        ("catalog", _CATALOG),
        ("metastore_id", _METASTORE_ID),
        ("workspace_id", _WORKSPACE_ID),
        ("model_family", _MODEL_FAMILY),
        ("candidate_model", _CANDIDATE_MODEL),
        ("states", (_active_state(),)),
    ):
        object.__setattr__(lookalike, name, value)

    with pytest.raises(RuntimeError, match="was not issued by the admin auditor"):
        consume_gateway_model_lifecycle_proof(lookalike)


def test_mutated_issued_lifecycle_proof_is_rejected() -> None:
    proof = _issue()
    object.__setattr__(proof, "catalog", "other")

    with pytest.raises(RuntimeError, match="was not issued by the admin auditor"):
        consume_gateway_model_lifecycle_proof(proof)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("application_id", ""),
        ("inventory_principal", " deployer@example.com"),
        ("catalog", " mip"),
        ("metastore_id", ""),
        ("workspace_id", " "),
        ("model_family", "family "),
        ("candidate_model", ""),
    ],
)
def test_lifecycle_proof_rejects_incomplete_or_noncanonical_scope(
    field: str,
    value: str,
) -> None:
    change: dict[str, Any] = {field: value}
    with pytest.raises(RuntimeError, match="scope is incomplete"):
        _issue(**change)


def test_lifecycle_proof_requires_sorted_unique_states_and_candidate() -> None:
    with pytest.raises(RuntimeError, match="duplicate or unordered states"):
        _issue(states=(_active_state(), _archived_state()))
    with pytest.raises(RuntimeError, match="duplicate or unordered states"):
        _issue(states=(_active_state(), _active_state()))
    with pytest.raises(RuntimeError, match="omits the current candidate"):
        _issue(states=(_archived_state(),), candidate_model="missing")


def test_lifecycle_proof_assigns_each_inference_table_once() -> None:
    shared_table = f"{_CATALOG}.audit.gateway_payload_shared"
    states = (
        _archived_state(inference_tables=(shared_table,)),
        _active_state(inference_tables=(shared_table,)),
    )

    with pytest.raises(RuntimeError, match="assigns one table more than once"):
        _issue(states=states)


@pytest.mark.parametrize(
    "state",
    [
        _active_state(
            model_name="",
            inference_tables=(f"{_CATALOG}.audit.gateway_payload_invalid",),
        ),
        _active_state(owner=" runtime"),
        _active_state(disposition="retired"),
        _active_state(versions_sha256="A" * 64),
        _active_state(versions_sha256="1" * 63),
        _active_state(inference_tables=(" table",)),
        _active_state(
            inference_tables=(
                f"{_CATALOG}.audit.gateway_payload_z",
                f"{_CATALOG}.audit.gateway_payload_a",
            )
        ),
    ],
)
def test_lifecycle_proof_rejects_invalid_state_identity(
    state: GatewayModelLifecycleState,
) -> None:
    members = [_archived_state(), state]
    if state.model_name != _CANDIDATE_MODEL:
        members.append(_active_state())
    states = tuple(sorted(members))
    pattern = (
        "state is incomplete|disposition is invalid|model identity is invalid|"
        "tables are not canonical|version digest is invalid|table identity is invalid"
    )

    with pytest.raises(RuntimeError, match=pattern):
        _issue(states=states)


@pytest.mark.parametrize(
    "state",
    [
        _active_state(active_contract_json=""),
        _active_state(retirement_record_sha256=_RETIREMENT_DIGEST),
    ],
)
def test_active_state_requires_only_active_contract(
    state: GatewayModelLifecycleState,
) -> None:
    with pytest.raises(RuntimeError, match="active Gateway allocation proof is incomplete"):
        _issue(states=tuple(sorted((_archived_state(), state))))


@pytest.mark.parametrize(
    "state",
    [
        _archived_state(active_contract_json='{"unexpected":true}'),
        _archived_state(retirement_record_sha256=""),
        _archived_state(retirement_record_sha256="F" * 64),
    ],
)
def test_archived_state_requires_only_canonical_retirement_digest(
    state: GatewayModelLifecycleState,
) -> None:
    with pytest.raises(RuntimeError, match="archived Gateway allocation proof is incomplete"):
        _issue(states=tuple(sorted((state, _active_state()))))


@pytest.mark.parametrize(
    "candidate",
    [
        _active_state(owner="governance@example.com"),
        _active_state(
            disposition="archived",
            active_contract_json="",
            retirement_record_sha256=_RETIREMENT_DIGEST,
        ),
    ],
)
def test_candidate_must_be_active_and_runtime_owned(
    candidate: GatewayModelLifecycleState,
) -> None:
    with pytest.raises(
        RuntimeError,
        match=(
            "active Gateway allocation proof is incomplete|"
            "archived Gateway allocation proof is incomplete|"
            "candidate is not an active runtime-owned allocation"
        ),
    ):
        _issue(states=tuple(sorted((_archived_state(), candidate))))
