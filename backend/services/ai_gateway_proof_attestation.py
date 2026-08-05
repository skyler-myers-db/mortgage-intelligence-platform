"""Cryptographic attestation for exact AI Gateway inference-row proofs."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

AI_GATEWAY_PROOF_ATTESTATION_ALG = "ed25519-v1"


def derive_gateway_proof_verify_key(signing_key: str) -> str:
    """Return the public verification key for a base64url raw private key."""

    private = Ed25519PrivateKey.from_private_bytes(_decode_key(signing_key, expected_len=32))
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _encode(public)


def gateway_proof_key_id(verify_key: str) -> str:
    """Return the stable public-key fingerprint persisted beside signatures."""

    raw_public = _decode_key(verify_key, expected_len=32)
    return hashlib.sha256(raw_public).hexdigest()[:16]


def sign_gateway_proof(
    *,
    signing_key: str,
    proof_id: str,
    git_sha: str,
    client_request_id: str,
    endpoint_name: str,
    inference_table: str,
    sent_at: datetime,
    verified_at: datetime,
) -> tuple[str, str, str]:
    """Sign the immutable fields that make an exact proof claimable."""

    private = Ed25519PrivateKey.from_private_bytes(_decode_key(signing_key, expected_len=32))
    verify_key = derive_gateway_proof_verify_key(signing_key)
    signature = private.sign(
        _attestation_payload(
            proof_id=proof_id,
            git_sha=git_sha,
            client_request_id=client_request_id,
            endpoint_name=endpoint_name,
            inference_table=inference_table,
            sent_at=sent_at,
            verified_at=verified_at,
        )
    )
    return AI_GATEWAY_PROOF_ATTESTATION_ALG, gateway_proof_key_id(verify_key), _encode(signature)


def verify_gateway_proof(
    *,
    verify_key: str,
    attestation_alg: str | None,
    attestation_key_id: str | None,
    attestation_signature: str | None,
    proof_id: str,
    git_sha: str,
    client_request_id: str,
    endpoint_name: str,
    inference_table: str,
    sent_at: datetime,
    verified_at: datetime,
) -> bool:
    """Return whether a verified ledger row was signed by the configured verifier."""

    if attestation_alg != AI_GATEWAY_PROOF_ATTESTATION_ALG:
        return False
    try:
        if attestation_key_id != gateway_proof_key_id(verify_key):
            return False
        public = Ed25519PublicKey.from_public_bytes(_decode_key(verify_key, expected_len=32))
        signature = _decode_key(attestation_signature or "", expected_len=64)
        public.verify(
            signature,
            _attestation_payload(
                proof_id=proof_id,
                git_sha=git_sha,
                client_request_id=client_request_id,
                endpoint_name=endpoint_name,
                inference_table=inference_table,
                sent_at=sent_at,
                verified_at=verified_at,
            ),
        )
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def _attestation_payload(
    *,
    proof_id: str,
    git_sha: str,
    client_request_id: str,
    endpoint_name: str,
    inference_table: str,
    sent_at: datetime,
    verified_at: datetime,
) -> bytes:
    payload = {
        "client_request_id": client_request_id,
        "endpoint_name": endpoint_name,
        "git_sha": git_sha,
        "inference_table": inference_table,
        "proof_id": proof_id,
        "sent_at": _canonical_timestamp(sent_at),
        "verified_at": _canonical_timestamp(verified_at),
        "version": 1,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_timestamp(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decode_key(value: str, *, expected_len: int) -> bytes:
    text = str(value).strip()
    if not text:
        raise ValueError("AI Gateway proof attestation key is not configured")
    try:
        decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError("AI Gateway proof attestation key is invalid") from exc
    if len(decoded) != expected_len:
        raise ValueError("AI Gateway proof attestation key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
