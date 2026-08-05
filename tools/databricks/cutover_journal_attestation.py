"""Ed25519 attestation for destructive agent cutover journal records."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ATTESTATION_ALG = "ed25519-agent-cutover-v1"


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("agent cutover attestation key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("agent cutover attestation key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {
        key: value
        for key, value in record.items()
        if key not in {"attestation_alg", "attestation_verify_key", "attestation_signature"}
    }
    return b"mip-agent-cutover\0" + json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_cutover_journal(record: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing, length=32))
    derived = _encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    if derived != verify:
        raise RuntimeError("agent cutover signing and verification keys do not match")
    return {
        **record,
        "attestation_alg": ATTESTATION_ALG,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(record))),
    }


def verify_cutover_journal(record: dict[str, Any]) -> None:
    current = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    previous = os.environ.get("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", "").strip()
    verify = str(record.get("attestation_verify_key") or "").strip()
    if record.get("attestation_alg") != ATTESTATION_ALG or verify not in {
        current,
        previous,
    } - {""}:
        raise RuntimeError("agent cutover journal attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(verify, length=32))
        signature = _decode(str(record.get("attestation_signature") or ""), length=64)
        public.verify(signature, _message(record))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("agent cutover journal signature is invalid") from exc
