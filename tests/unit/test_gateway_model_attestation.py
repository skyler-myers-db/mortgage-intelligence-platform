from __future__ import annotations

import base64
import json

import pytest

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import gateway_model_attestation as attestation


def _key(seed: bytes) -> str:
    return base64.urlsafe_b64encode(seed).decode("ascii").rstrip("=")


CURRENT_SIGNING = _key(b"c" * 32)
CURRENT_VERIFY = derive_gateway_proof_verify_key(CURRENT_SIGNING)
PREVIOUS_SIGNING = _key(b"p" * 32)
PREVIOUS_VERIFY = derive_gateway_proof_verify_key(PREVIOUS_SIGNING)


def _contract() -> dict[str, str]:
    return {
        "full_name": "mip.audit.proxy_0123456789ab",
        "model_source": "models:/m-reviewed-proxy",
        "source_hash": "a" * 64,
        "supervisor_id": "supervisor-123",
        "supervisor_endpoint_id": "supervisor-endpoint-456",
        "upstream_endpoint": "supervisor-0123456789ab",
        "runtime_application_id": "runtime-client",
        "model_family": "mip.audit.proxy",
        "experiment_base": "mip-agent-runtime-gateway-proxy",
        "catalog": "mip",
        "genie_space_id": "01f-genie",
        "inference_schema": "audit",
        "inference_table_prefix": "mip_gateway_proxy",
    }


def _configure_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", CURRENT_SIGNING)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", CURRENT_VERIFY)
    monkeypatch.delenv("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", raising=False)


def test_gateway_model_contract_signature_binds_every_historical_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_current(monkeypatch)
    contract = _contract()
    tags = attestation.sign_gateway_model_contract(**contract)

    assert set(tags) == {"mip.proxy_contract_attestation_v3"}
    envelope = json.loads(tags[attestation.ATTESTATION_TAG])
    assert envelope["contract"]["supervisor_endpoint_id"] == "supervisor-endpoint-456"
    assert attestation.verify_gateway_model_contract(tags=tags, **contract)
    for field in contract:
        drifted = {**contract, field: contract[field] + "-drift"}
        with pytest.raises(RuntimeError, match="identity"):
            attestation.verify_gateway_model_contract(tags=tags, **drifted)


def test_gateway_model_private_key_is_rejected_without_explicit_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_current(monkeypatch)
    monkeypatch.delenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING")

    with pytest.raises(RuntimeError, match="not explicitly authorized"):
        attestation.sign_gateway_model_contract(**_contract())


def test_gateway_model_contract_rejects_untrusted_self_signed_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", PREVIOUS_SIGNING)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", PREVIOUS_VERIFY)
    tags = attestation.sign_gateway_model_contract(**_contract())
    _configure_current(monkeypatch)

    with pytest.raises(RuntimeError, match="identity"):
        attestation.verify_gateway_model_contract(tags=tags, **_contract())


@pytest.mark.parametrize(
    "model_source",
    [
        "runs:/run/model",
        "models:/m-reviewed-proxy/1",
        "models:/registered-model/1",
        "models:/registered-model@champion",
    ],
)
def test_gateway_model_contract_rejects_mutable_or_versioned_model_uris(
    monkeypatch: pytest.MonkeyPatch,
    model_source: str,
) -> None:
    _configure_current(monkeypatch)

    with pytest.raises(RuntimeError, match="immutable MLflow logged-model URI"):
        attestation.sign_gateway_model_contract(**{**_contract(), "model_source": model_source})


def test_previous_trusted_attestation_is_read_only_and_reports_its_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", PREVIOUS_SIGNING)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", PREVIOUS_VERIFY)
    previous_tags = attestation.sign_gateway_model_contract(**_contract())
    _configure_current(monkeypatch)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", PREVIOUS_VERIFY)

    assert not attestation.verify_gateway_model_contract(tags=previous_tags, **_contract())
    assert attestation.gateway_model_attestation_record_key(previous_tags) == PREVIOUS_VERIFY


def test_unsigned_attestation_has_no_record_epoch() -> None:
    with pytest.raises(RuntimeError, match="envelope"):
        attestation.gateway_model_attestation_record_key({})


def test_unshipped_v2_draft_is_not_accepted_as_a_compatibility_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_current(monkeypatch)
    current = attestation.sign_gateway_model_contract(**_contract())
    legacy = {"mip.proxy_contract_attestation_v2": current[attestation.ATTESTATION_TAG]}
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", CURRENT_VERIFY)

    with pytest.raises(RuntimeError, match="envelope"):
        attestation.verify_gateway_model_contract(tags=legacy, **_contract())
