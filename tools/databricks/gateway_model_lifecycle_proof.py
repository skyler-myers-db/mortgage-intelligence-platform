"""Opaque dual-authority proof for active and archived Gateway allocations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Lock
from weakref import ReferenceType, ref


@dataclass(frozen=True, order=True)
class GatewayModelLifecycleState:
    """One exact control-plane classification for a Gateway model allocation."""

    model_name: str
    owner: str
    disposition: str
    versions_sha256: str
    inference_tables: tuple[str, ...]
    active_contract_json: str
    retirement_record_sha256: str


_PROOF_ISSUER = object()
_PROOF_LOCK = Lock()
_PROOF_REGISTRY: dict[
    int,
    tuple[
        ReferenceType[GatewayModelLifecycleProof],
        tuple[
            str,
            str,
            str,
            str,
            str,
            str,
            str,
            tuple[GatewayModelLifecycleState, ...],
        ],
    ],
] = {}


@dataclass(frozen=True, init=False)
class GatewayModelLifecycleProof:
    """Non-constructible evidence issued by the admin lifecycle inventory."""

    application_id: str
    inventory_principal: str
    catalog: str
    metastore_id: str
    workspace_id: str
    model_family: str
    candidate_model: str
    states: tuple[GatewayModelLifecycleState, ...]
    _issuer: object


@dataclass(frozen=True)
class ConsumedGatewayModelLifecycleProof:
    """Immutable one-use snapshot exposed to the runtime-authority verifier."""

    application_id: str
    inventory_principal: str
    catalog: str
    metastore_id: str
    workspace_id: str
    model_family: str
    candidate_model: str
    states: tuple[GatewayModelLifecycleState, ...]

    @property
    def by_model(self) -> dict[str, GatewayModelLifecycleState]:
        return {state.model_name: state for state in self.states}


def _snapshot(
    proof: GatewayModelLifecycleProof,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    tuple[GatewayModelLifecycleState, ...],
]:
    return (
        proof.application_id,
        proof.inventory_principal,
        proof.catalog,
        proof.metastore_id,
        proof.workspace_id,
        proof.model_family,
        proof.candidate_model,
        proof.states,
    )


def _validate_state_set(
    *,
    application_id: str,
    inventory_principal: str,
    catalog: str,
    metastore_id: str,
    workspace_id: str,
    model_family: str,
    candidate_model: str,
    states: tuple[GatewayModelLifecycleState, ...],
) -> None:
    required = (
        application_id,
        inventory_principal,
        catalog,
        metastore_id,
        workspace_id,
        model_family,
        candidate_model,
    )
    if any(not value or value != value.strip() for value in required):
        raise RuntimeError("Gateway model lifecycle proof scope is incomplete")
    if not states:
        raise RuntimeError("Gateway model lifecycle proof has no model states")
    names = [state.model_name for state in states]
    if names != sorted(names) or len(names) != len(set(names)):
        raise RuntimeError("Gateway model lifecycle proof has duplicate or unordered states")
    if candidate_model not in names:
        raise RuntimeError("Gateway model lifecycle proof omits the current candidate")
    table_names = [
        table_name
        for state in states
        for table_name in state.inference_tables
    ]
    if len(table_names) != len(set(table_names)):
        raise RuntimeError("Gateway model lifecycle proof assigns one table more than once")
    for state in states:
        text_fields = (
            state.model_name,
            state.owner,
            state.disposition,
            state.versions_sha256,
        )
        if any(not value or value != value.strip() for value in text_fields):
            raise RuntimeError("Gateway model lifecycle proof state is incomplete")
        if state.disposition not in {"active", "archived"}:
            raise RuntimeError("Gateway model lifecycle proof disposition is invalid")
        if re.fullmatch(rf"{re.escape(model_family)}_[0-9a-f]{{12}}", state.model_name) is None:
            raise RuntimeError("Gateway model lifecycle proof model identity is invalid")
        if (
            tuple(sorted(state.inference_tables)) != state.inference_tables
            or len(state.inference_tables) != len(set(state.inference_tables))
        ):
            raise RuntimeError("Gateway model lifecycle proof tables are not canonical")
        if (
            len(state.versions_sha256) != 64
            or any(character not in "0123456789abcdef" for character in state.versions_sha256)
        ):
            raise RuntimeError("Gateway model lifecycle version digest is invalid")
        if any(not name or name != name.strip() for name in state.inference_tables):
            raise RuntimeError("Gateway model lifecycle table identity is invalid")
        if state.disposition == "active":
            if (
                state.owner != application_id
                or not state.active_contract_json
                or state.retirement_record_sha256
            ):
                raise RuntimeError("active Gateway allocation proof is incomplete")
        elif (
            state.owner == application_id
            or state.active_contract_json
            or len(state.retirement_record_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in state.retirement_record_sha256
            )
        ):
            raise RuntimeError("archived Gateway allocation proof is incomplete")
    candidate = next(state for state in states if state.model_name == candidate_model)
    if candidate.disposition != "active" or candidate.owner != application_id:
        raise RuntimeError("Gateway candidate is not an active runtime-owned allocation")


def _issue_gateway_model_lifecycle_proof(
    *,
    application_id: str,
    inventory_principal: str,
    catalog: str,
    metastore_id: str,
    workspace_id: str,
    model_family: str,
    candidate_model: str,
    states: tuple[GatewayModelLifecycleState, ...],
) -> GatewayModelLifecycleProof:
    """Issue one opaque proof after an authoritative admin inventory."""

    _validate_state_set(
        application_id=application_id,
        inventory_principal=inventory_principal,
        catalog=catalog,
        metastore_id=metastore_id,
        workspace_id=workspace_id,
        model_family=model_family,
        candidate_model=candidate_model,
        states=states,
    )
    proof = object.__new__(GatewayModelLifecycleProof)
    for name, value in (
        ("application_id", application_id),
        ("inventory_principal", inventory_principal),
        ("catalog", catalog),
        ("metastore_id", metastore_id),
        ("workspace_id", workspace_id),
        ("model_family", model_family),
        ("candidate_model", candidate_model),
        ("states", states),
        ("_issuer", _PROOF_ISSUER),
    ):
        object.__setattr__(proof, name, value)
    proof_id = id(proof)

    def retire(reference: ReferenceType[GatewayModelLifecycleProof]) -> None:
        with _PROOF_LOCK:
            registered = _PROOF_REGISTRY.get(proof_id)
            if registered is not None and registered[0] is reference:
                _PROOF_REGISTRY.pop(proof_id, None)

    reference = ref(proof, retire)
    with _PROOF_LOCK:
        _PROOF_REGISTRY[proof_id] = (reference, _snapshot(proof))
    return proof


def consume_gateway_model_lifecycle_proof(
    proof: GatewayModelLifecycleProof,
) -> ConsumedGatewayModelLifecycleProof:
    """Consume an exact proof once; reject caller-constructed lookalikes."""

    if not isinstance(proof, GatewayModelLifecycleProof):
        raise RuntimeError("Gateway model lifecycle proof was not issued by the admin auditor")
    with _PROOF_LOCK:
        registered = _PROOF_REGISTRY.get(id(proof))
        if (
            registered is None
            or registered[0]() is not proof
            or registered[1] != _snapshot(proof)
            or getattr(proof, "_issuer", None) is not _PROOF_ISSUER
        ):
            raise RuntimeError("Gateway model lifecycle proof was not issued by the admin auditor")
        _PROOF_REGISTRY.pop(id(proof), None)
        snapshot = registered[1]
    return ConsumedGatewayModelLifecycleProof(*snapshot)
