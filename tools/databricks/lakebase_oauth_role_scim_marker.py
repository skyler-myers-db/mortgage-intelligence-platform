"""Signed ``displayName`` markers for temporary Lakebase SCIM identities.

Databricks workspace service-principal create and PATCH operations do not
persist ``externalId``.  Temporary privileged identities therefore carry
their recovery authority in a signed display name.  The deterministic prefix
reserves one namespace per target, while the signature prevents an arbitrary
same-name principal from authorizing destructive recovery.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from tools.databricks.app_deployment_lease_support import key_registry

_BOOTSTRAP_RESERVATION_PREFIX = "mip-lakebase-role-bootstrap-"
_BOOTSTRAP_OWNERSHIP_PREFIX = "mip:lb:b:v1:"
_SCIM_DISPLAY_NAME_LIMIT = 100


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("temporary Lakebase bootstrap marker key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("temporary Lakebase bootstrap marker key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _message(*, reservation_name: str, ownership_marker: str) -> bytes:
    return (
        "mip-lakebase-bootstrap-principal-v2\0" f"{reservation_name}\0{ownership_marker}"
    ).encode()


def bootstrap_principal_display_prefix(reservation_name: str) -> str:
    """Return the exact account-SCIM inventory prefix for one target."""

    digest = hashlib.sha256(reservation_name.encode()).digest()[:9]
    return f"b{_encode(digest)}."


def _reservation_from_ownership_marker(ownership_marker: str) -> str:
    if not ownership_marker.startswith(_BOOTSTRAP_OWNERSHIP_PREFIX):
        raise RuntimeError("temporary Lakebase bootstrap ownership marker is invalid")
    digest = ownership_marker.removeprefix(_BOOTSTRAP_OWNERSHIP_PREFIX)
    if len(digest) != 48 or any(character not in "0123456789abcdef" for character in digest):
        raise RuntimeError("temporary Lakebase bootstrap ownership marker is invalid")
    return f"{_BOOTSTRAP_RESERVATION_PREFIX}{digest[:24]}"


def bootstrap_principal_display_name(
    *,
    reservation_name: str,
    ownership_marker: str,
    signing_key: str,
) -> str:
    """Render the deterministic signed SCIM name used for a new creator."""

    verify_key = derive_gateway_proof_verify_key(signing_key)
    if verify_key != key_registry()[-1]:
        raise RuntimeError("temporary Lakebase bootstrap signer is not the current proof key")
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing_key, length=32))
    signature = _encode(
        private.sign(
            _message(
                reservation_name=reservation_name,
                ownership_marker=ownership_marker,
            )
        )
    )
    display_name = f"{bootstrap_principal_display_prefix(reservation_name)}{signature}"
    if (
        len(display_name) != _SCIM_DISPLAY_NAME_LIMIT
        or len(display_name.encode("utf-8")) != _SCIM_DISPLAY_NAME_LIMIT
    ):
        raise RuntimeError("temporary Lakebase bootstrap display name exceeds SCIM limits")
    return display_name


def assert_bootstrap_principal_display_name(
    actual_display_name: str,
    *,
    expected_name: str,
    ownership_marker: str,
) -> None:
    """Verify a signed name against a reservation or an exact signed name.

    Creation-time callers retain the exact signed name.  Recovery-time callers
    know only the deterministic reservation and accept signatures from the
    configured current/previous/historical key registry.
    """

    if expected_name.startswith("b"):
        if actual_display_name != expected_name:
            raise RuntimeError("temporary Lakebase bootstrap display identity drifted")
        reservation_name = _reservation_from_ownership_marker(ownership_marker)
    else:
        reservation_name = expected_name
    prefix = bootstrap_principal_display_prefix(reservation_name)
    if not actual_display_name.startswith(prefix):
        raise RuntimeError("temporary Lakebase bootstrap display marker drifted")
    signature = actual_display_name[len(prefix) :]
    if "." in signature:
        raise RuntimeError("temporary Lakebase bootstrap display marker is invalid")
    encoded_message = _message(
        reservation_name=reservation_name,
        ownership_marker=ownership_marker,
    )
    decoded_signature = _decode(signature, length=64)
    verified = False
    for verify_key in key_registry():
        try:
            Ed25519PublicKey.from_public_bytes(_decode(verify_key, length=32)).verify(
                decoded_signature,
                encoded_message,
            )
            verified = True
            break
        except (InvalidSignature, RuntimeError, ValueError):
            continue
    if not verified:
        raise RuntimeError("temporary Lakebase bootstrap marker signature is invalid")
    if (
        len(actual_display_name) != _SCIM_DISPLAY_NAME_LIMIT
        or len(actual_display_name.encode("utf-8")) != _SCIM_DISPLAY_NAME_LIMIT
    ):
        raise RuntimeError("temporary Lakebase bootstrap display name exceeds SCIM limits")


def is_reserved_bootstrap_display(
    display_name: str,
    *,
    reservation_name: str,
) -> bool:
    """Return whether a name occupies the target's reserved SCIM namespace."""

    return (
        display_name == reservation_name
        or display_name.startswith(f"{reservation_name}:")
        or display_name.startswith(bootstrap_principal_display_prefix(reservation_name))
    )


def assert_scim_external_id_unset(principal: object, *, label: str) -> None:
    """Pin the live Databricks contract: ``externalId`` is not persisted."""

    if str(getattr(principal, "external_id", "") or "").strip():
        raise RuntimeError(f"{label} unexpectedly persisted a SCIM external id")
