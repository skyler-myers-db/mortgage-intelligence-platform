from __future__ import annotations

import base64
import json
from collections.abc import Callable
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
    PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    PRIOR_V2_GATEWAY_RESOURCE_FIELDS,
    PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS,
    _assert_live_resources,
    _verified_resource_environment,
    legacy_gateway_resource_digest,
    prior_v2_gateway_resource_digest,
    validated_prior_v2_gateway_resources,
)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _contract() -> dict[str, str]:
    values = {field: f"legacy-{field}" for field in LEGACY_GATEWAY_RESOURCE_FIELDS}
    values["proof_version"] = GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    values["workspace_host"] = "https://workspace.cloud.databricks.com"
    return values


def _entity(
    *,
    contract: dict[str, str],
    private: Ed25519PrivateKey,
    prior_v2: bool = False,
) -> object:
    contract_json = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    verify_key = _encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    signature = private.sign(
        GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG.encode() + b"\0" + contract_json.encode()
    )
    return SimpleNamespace(
        environment_vars={
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": contract_json,
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": (
                prior_v2_gateway_resource_digest(contract)
                if prior_v2
                else legacy_gateway_resource_digest(contract)
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

    assert environment[
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"
    ] == legacy_gateway_resource_digest(contract)


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
    entity.environment_vars["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE"] = _encode(b"x" * 64)

    with pytest.raises(RuntimeError, match="signature is invalid"):
        _verified_resource_environment(entity, contract=contract)


def test_legacy_served_resource_binding_rejects_untrusted_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(b"l" * 32)
    contract = _contract()
    entity = _entity(contract=contract, private=private)
    other = Ed25519PrivateKey.from_private_bytes(b"o" * 32)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        _encode(
            other.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ),
    )

    with pytest.raises(RuntimeError, match="signer is not trusted"):
        _verified_resource_environment(entity, contract=contract)


def test_legacy_served_resource_binding_rejects_digest_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(b"l" * 32)
    contract = _contract()
    entity = _entity(contract=contract, private=private)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        entity.environment_vars["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"],
    )
    entity.environment_vars["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256"] = "a" * 64

    with pytest.raises(RuntimeError, match="resource binding drifted"):
        _verified_resource_environment(entity, contract=contract)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda contract: contract.pop("gateway_experiment_owner"),
        lambda contract: contract.update(extra_field="unexpected"),
        lambda contract: contract.update(proof_version="gateway-runtime-resource-proof-v1"),
    ),
)
def test_legacy_resource_digest_rejects_non_exact_schema(
    mutate: Callable[[dict[str, str]], object],
) -> None:
    contract = _contract()
    mutate(contract)

    with pytest.raises(ValueError, match="legacy Gateway resource contract"):
        legacy_gateway_resource_digest(contract)


def _prior_v2_contract(*, proxy_aware: bool = True) -> dict[str, str]:
    fields = (
        PRIOR_V2_GATEWAY_RESOURCE_FIELDS if proxy_aware else PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS
    )
    values = {field: f"prior-{field}" for field in fields}
    values["proof_version"] = PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
    return values


@pytest.mark.parametrize("proxy_aware", (False, True))
def test_prior_v2_validator_preserves_exact_historical_schema(
    proxy_aware: bool,
) -> None:
    contract = _prior_v2_contract(proxy_aware=proxy_aware)
    resources = {
        **contract,
        "resource_digest": prior_v2_gateway_resource_digest(contract),
    }

    assert (
        validated_prior_v2_gateway_resources(
            resources,
            proxy_aware=proxy_aware,
        )
        == resources
    )
    with pytest.raises(ValueError, match="legacy Gateway resource contract"):
        legacy_gateway_resource_digest(contract)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda contract: contract.pop("gateway_endpoint_id"),
        lambda contract: contract.update(extra_field="unexpected"),
        lambda contract: contract.update(proof_version=GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION),
    ),
)
def test_prior_v2_validator_rejects_schema_and_version_tamper(
    mutate: Callable[[dict[str, str]], object],
) -> None:
    contract = _prior_v2_contract()
    mutate(contract)

    with pytest.raises(ValueError, match="prior v2 Gateway resource contract"):
        prior_v2_gateway_resource_digest(contract)


def test_prior_v2_signature_tamper_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(b"v" * 32)
    contract = _prior_v2_contract()
    entity = _entity(contract=contract, private=private, prior_v2=True)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        entity.environment_vars["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"],
    )
    entity.environment_vars["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE"] = _encode(b"x" * 64)

    with pytest.raises(RuntimeError, match="signature is invalid"):
        _verified_resource_environment(entity, contract=contract, prior_v2=True)


def test_prior_v2_live_transition_requires_authenticated_workspace_origin() -> None:
    contract = _prior_v2_contract()
    resources = {
        **contract,
        "resource_digest": prior_v2_gateway_resource_digest(contract),
    }
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://accounts.cloud.databricks.com")
    )

    with pytest.raises(RuntimeError, match="authenticated legacy Gateway workspace host"):
        _assert_live_resources(workspace, resources=resources, prior_v2=True)


def test_prior_v2_live_transition_rejects_supervisor_identity_drift() -> None:
    contract = _prior_v2_contract()
    resources = {
        **contract,
        "resource_digest": prior_v2_gateway_resource_digest(contract),
    }
    workspace = SimpleNamespace(
        config=SimpleNamespace(host="https://workspace.cloud.databricks.com"),
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "supervisor_agent_id": "different-supervisor",
                "endpoint_name": contract["supervisor_endpoint"],
                "creator": contract["supervisor_creator"],
            }
        ),
    )

    with pytest.raises(RuntimeError, match="Supervisor identity drifted"):
        _assert_live_resources(workspace, resources=resources, prior_v2=True)
