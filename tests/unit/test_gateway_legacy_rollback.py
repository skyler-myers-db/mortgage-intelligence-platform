from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.agents.gateway_contract import (
    GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG,
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
)
from tools.databricks.gateway_legacy_rollback import (
    LEGACY_GATEWAY_RESOURCE_FIELDS,
    _verified_resource_environment,
    legacy_gateway_resource_digest,
)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _contract() -> dict[str, str]:
    values = {field: f"legacy-{field}" for field in LEGACY_GATEWAY_RESOURCE_FIELDS}
    values["proof_version"] = GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    return values


def _entity(
    *,
    contract: dict[str, str],
    private: Ed25519PrivateKey,
) -> object:
    contract_json = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    verify_key = _encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    signature = private.sign(
        GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG.encode()
        + b"\0"
        + contract_json.encode()
    )
    return SimpleNamespace(
        environment_vars={
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": contract_json,
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": (
                legacy_gateway_resource_digest(contract)
            ),
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": _encode(signature),
            "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": verify_key,
        }
    )


def test_legacy_served_resource_binding_requires_its_original_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(b"l" * 32)
    contract = _contract()
    entity = _entity(contract=contract, private=private)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        entity.environment_vars["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"],
    )

    environment = _verified_resource_environment(entity, contract=contract)

    assert (
        environment["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"]
        == legacy_gateway_resource_digest(contract)
    )


def test_legacy_served_resource_binding_rejects_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(b"l" * 32)
    contract = _contract()
    entity = _entity(contract=contract, private=private)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        entity.environment_vars["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"],
    )
    entity.environment_vars["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE"] = _encode(
        b"x" * 64
    )

    with pytest.raises(RuntimeError, match="signature is invalid"):
        _verified_resource_environment(entity, contract=contract)
