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

from backend.agents.gateway_contract import (
    GATEWAY_MODEL_ATTESTATION_ALGORITHM_TAG,
    GATEWAY_MODEL_ATTESTATION_SIGNATURE_TAG,
    GATEWAY_MODEL_ATTESTATION_VERIFY_KEY_TAG,
    GATEWAY_MODEL_CONTRACT_FIELD_TAGS,
    GATEWAY_MODEL_CONTRACT_FIELDS,
    decode_gateway_attestation_base64,
    gateway_model_version_tags,
)
from backend.services.ai_gateway_proof_attestation import (
    AI_GATEWAY_PROOF_ATTESTATION_ALG,
    derive_gateway_proof_verify_key,
)

_IMMUTABLE_LOGGED_MODEL_URI = re.compile(r"models:/m-[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_CONTRACT_FIELDS = GATEWAY_MODEL_CONTRACT_FIELDS


def _decode(value: str, *, length: int) -> bytes:
    try:
        return decode_gateway_attestation_base64(value, length=length)
    except RuntimeError as exc:
        raise RuntimeError("Gateway model contract attestation key is invalid") from exc


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
    """Read the canonical contract from one complete attestation tag epoch."""

    return _contract_dict(dict(gateway_model_version_tags(tags).contract))


def gateway_model_attestation_record_key(tags: dict[str, str]) -> str:
    """Return the public-key epoch named by a structurally valid envelope."""

    record = gateway_model_version_tags(tags)
    if record.algorithm != AI_GATEWAY_PROOF_ATTESTATION_ALG:
        raise RuntimeError("Gateway model contract attestation identity is invalid")
    gateway_model_contract_from_tags(tags)
    record_key = record.verify_key.strip()
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
    """Build one atomic UC-safe tag set signed by the server-owned release key."""

    require_gateway_model_attestation_signing_authority()
    canonical_contract = _contract_dict(contract)
    signing_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", "").strip()
    verify_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing_key, length=32))
    signature = private.sign(_payload(**contract))
    return {
        **{
            GATEWAY_MODEL_CONTRACT_FIELD_TAGS[field]: value
            for field, value in canonical_contract.items()
        },
        GATEWAY_MODEL_ATTESTATION_ALGORITHM_TAG: AI_GATEWAY_PROOF_ATTESTATION_ALG,
        GATEWAY_MODEL_ATTESTATION_SIGNATURE_TAG: base64.urlsafe_b64encode(signature)
        .decode("ascii")
        .rstrip("="),
        GATEWAY_MODEL_ATTESTATION_VERIFY_KEY_TAG: verify_key,
    }


def verify_gateway_model_contract(*, tags: dict[str, str], **contract: str) -> bool:
    """Verify current/previous trust and report whether the record uses the current key."""

    current = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    previous = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", "").strip()
    record = gateway_model_version_tags(tags)
    record_key = gateway_model_attestation_record_key(tags)
    if (
        record.algorithm != AI_GATEWAY_PROOF_ATTESTATION_ALG
        or record_key not in {current, previous} - {""}
        or gateway_model_contract_from_tags(tags) != _contract_dict(contract)
    ):
        raise RuntimeError("Gateway model contract attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(record_key, length=32))
        signature = _decode(record.signature, length=64)
        public.verify(signature, _payload(**contract))
    except (InvalidSignature, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError("Gateway model contract attestation signature is invalid") from exc
    return record_key == current
