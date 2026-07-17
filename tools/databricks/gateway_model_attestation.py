"""Durable Ed25519 provenance for immutable Gateway proxy model versions.

Version 3 is the first governed durable schema.  The unshipped v2 draft is
intentionally not parsed so an older-shaped envelope cannot be mistaken for a
contract that binds the Supervisor endpoint's immutable ID.
"""

from __future__ import annotations

import base64
import json
import os
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.services.ai_gateway_proof_attestation import (
    AI_GATEWAY_PROOF_ATTESTATION_ALG,
    derive_gateway_proof_verify_key,
)

ATTESTATION_TAG = "mip.proxy_contract_attestation_v3"
_IMMUTABLE_LOGGED_MODEL_URI = re.compile(r"models:/m-[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_CONTRACT_FIELDS = frozenset(
    {
        "catalog",
        "experiment_base",
        "full_name",
        "genie_space_id",
        "inference_schema",
        "inference_table_prefix",
        "model_family",
        "model_source",
        "runtime_application_id",
        "source_hash",
        "supervisor_endpoint_id",
        "supervisor_id",
        "upstream_endpoint",
    }
)


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gateway model contract attestation key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("Gateway model contract attestation key has an invalid length")
    return decoded


def _payload(
    *,
    full_name: str,
    model_source: str,
    source_hash: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    upstream_endpoint: str,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    genie_space_id: str,
    inference_schema: str,
    inference_table_prefix: str,
) -> bytes:
    contract = {
        "catalog": catalog,
        "experiment_base": experiment_base,
        "full_name": full_name,
        "genie_space_id": genie_space_id,
        "inference_schema": inference_schema,
        "inference_table_prefix": inference_table_prefix,
        "model_family": model_family,
        "model_source": model_source,
        "runtime_application_id": runtime_application_id,
        "source_hash": source_hash,
        "supervisor_endpoint_id": supervisor_endpoint_id,
        "supervisor_id": supervisor_id,
        "upstream_endpoint": upstream_endpoint,
        "version": 3,
    }
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return b"mip-gateway-model-contract-v3\0" + canonical


def _contract_dict(contract: dict[str, str]) -> dict[str, str]:
    if set(contract) != _CONTRACT_FIELDS or any(
        not isinstance(value, str) or not value for value in contract.values()
    ):
        raise RuntimeError("Gateway model contract attestation payload is invalid")
    if _IMMUTABLE_LOGGED_MODEL_URI.fullmatch(contract["model_source"]) is None:
        raise RuntimeError("Gateway model contract requires an immutable MLflow logged-model URI")
    return {key: contract[key] for key in sorted(_CONTRACT_FIELDS)}


def gateway_model_contract_from_tags(tags: dict[str, str]) -> dict[str, str]:
    """Read the canonical contract from the single atomic attestation envelope."""

    try:
        envelope = json.loads(str(tags.get(ATTESTATION_TAG) or ""))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gateway model contract attestation envelope is invalid") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "alg",
        "contract",
        "signature",
        "verify_key",
    }:
        raise RuntimeError("Gateway model contract attestation envelope is invalid")
    contract = envelope.get("contract")
    if not isinstance(contract, dict):
        raise RuntimeError("Gateway model contract attestation envelope is invalid")
    return _contract_dict(contract)


def gateway_model_attestation_record_key(tags: dict[str, str]) -> str:
    """Return the public-key epoch named by a structurally valid envelope."""

    try:
        envelope = json.loads(str(tags.get(ATTESTATION_TAG) or ""))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gateway model contract attestation envelope is invalid") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "alg",
        "contract",
        "signature",
        "verify_key",
    }:
        raise RuntimeError("Gateway model contract attestation envelope is invalid")
    if envelope.get("alg") != AI_GATEWAY_PROOF_ATTESTATION_ALG:
        raise RuntimeError("Gateway model contract attestation identity is invalid")
    gateway_model_contract_from_tags(tags)
    record_key = str(envelope.get("verify_key") or "").strip()
    _decode(record_key, length=32)
    return record_key


def require_gateway_model_attestation_signing_authority() -> None:
    """Require the deliberately bounded runtime-owner signing capability."""

    if os.environ.get("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "").strip() != "1":
        raise RuntimeError("Gateway model contract signing is not explicitly authorized")
    signing_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", "").strip()
    verify_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    if not signing_key or not verify_key:
        raise RuntimeError("Gateway model contract signing and verification keys are required")
    if derive_gateway_proof_verify_key(signing_key) != verify_key:
        raise RuntimeError("Gateway model contract signing and verification keys do not match")


def sign_gateway_model_contract(**contract: str) -> dict[str, str]:
    """Sign an exact model-version contract with the server-owned release key."""

    require_gateway_model_attestation_signing_authority()
    signing_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", "").strip()
    verify_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing_key, length=32))
    signature = private.sign(_payload(**contract))
    envelope = {
        "alg": AI_GATEWAY_PROOF_ATTESTATION_ALG,
        "contract": _contract_dict(contract),
        "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        "verify_key": verify_key,
    }
    return {
        ATTESTATION_TAG: json.dumps(envelope, sort_keys=True, separators=(",", ":")),
    }


def verify_gateway_model_contract(*, tags: dict[str, str], **contract: str) -> bool:
    """Verify current/previous trust and report whether the record uses the current key."""

    current = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    previous = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", "").strip()
    try:
        envelope = json.loads(str(tags.get(ATTESTATION_TAG) or ""))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gateway model contract attestation envelope is invalid") from exc
    if not isinstance(envelope, dict):
        raise RuntimeError("Gateway model contract attestation envelope is invalid")
    record_key = gateway_model_attestation_record_key(tags)
    if (
        envelope.get("alg") != AI_GATEWAY_PROOF_ATTESTATION_ALG
        or record_key not in {current, previous} - {""}
        or gateway_model_contract_from_tags(tags) != _contract_dict(contract)
    ):
        raise RuntimeError("Gateway model contract attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(record_key, length=32))
        signature = _decode(str(envelope.get("signature") or ""), length=64)
        public.verify(signature, _payload(**contract))
    except (InvalidSignature, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError("Gateway model contract attestation signature is invalid") from exc
    return record_key == current
