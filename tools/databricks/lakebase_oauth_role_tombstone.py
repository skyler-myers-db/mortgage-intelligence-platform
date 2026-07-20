"""Durable signed tombstones for control-plane-only Lakebase bootstrap roles."""

from __future__ import annotations

import base64
import hashlib
import time
from contextlib import suppress
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from tools.databricks.app_deployment_lease_support import key_registry

_ORPHAN_TOMBSTONE_DISPLAY_PREFIX = "mip-lb-orphan-v2:"
_ORPHAN_TOMBSTONE_EXTERNAL_PREFIX = "mip:lb:o:v2:"


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("temporary Lakebase orphan marker key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("temporary Lakebase orphan marker key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _validate_application_id(application_id: str) -> str:
    normalized = application_id.strip()
    if not normalized or len(normalized) > 64 or ":" in normalized:
        raise RuntimeError("temporary Lakebase orphan marker application id is invalid")
    return normalized


def _message(*, base_external_id: str, application_id: str) -> bytes:
    return f"mip-lakebase-orphan-v2\0{base_external_id}\0{application_id}".encode()


def _render_contract(
    *,
    base_external_id: str,
    application_id: str,
    verify_key: str,
    signature: str,
) -> tuple[str, str]:
    application_id = _validate_application_id(application_id)
    _decode(verify_key, length=32)
    _decode(signature, length=64)
    target_digest = hashlib.sha256(base_external_id.encode()).hexdigest()[:12]
    display_name = (
        f"{_ORPHAN_TOMBSTONE_DISPLAY_PREFIX}{target_digest}:"
        f"{application_id}:{verify_key}:{signature}"
    )
    if len(display_name) > 255:
        raise RuntimeError("temporary Lakebase orphan display name exceeds SCIM limits")
    external_digest = hashlib.sha256(display_name.encode()).hexdigest()[:48]
    external_id = f"{_ORPHAN_TOMBSTONE_EXTERNAL_PREFIX}{external_digest}"
    if len(external_id) > 64:
        raise RuntimeError("temporary Lakebase orphan external id exceeds SCIM limits")
    return display_name, external_id


def orphan_tombstone_contract(
    *,
    base_external_id: str,
    application_id: str,
    signing_key: str,
) -> tuple[str, str]:
    """Create a marker signed by the current proof key and carrying its public identity."""

    application_id = _validate_application_id(application_id)
    verify_key = derive_gateway_proof_verify_key(signing_key)
    registry = key_registry()
    if verify_key != registry[-1]:
        raise RuntimeError("temporary Lakebase orphan signer is not the current proof key")
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing_key, length=32))
    signature = _encode(
        private.sign(
            _message(
                base_external_id=base_external_id,
                application_id=application_id,
            )
        )
    )
    return _render_contract(
        base_external_id=base_external_id,
        application_id=application_id,
        verify_key=verify_key,
        signature=signature,
    )


def _decode_orphan_tombstone(
    display_name: str,
    *,
    base_external_id: str,
) -> tuple[str, str]:
    target_digest = hashlib.sha256(base_external_id.encode()).hexdigest()[:12]
    prefix = f"{_ORPHAN_TOMBSTONE_DISPLAY_PREFIX}{target_digest}:"
    if not display_name.startswith(prefix):
        raise RuntimeError("temporary Lakebase orphan marker display prefix drifted")
    fields = display_name[len(prefix) :].split(":")
    if len(fields) != 3:
        raise RuntimeError("temporary Lakebase orphan marker payload is invalid")
    application_id, verify_key, signature = fields
    application_id = _validate_application_id(application_id)
    if verify_key not in key_registry():
        raise RuntimeError("temporary Lakebase orphan marker signer is untrusted")
    try:
        Ed25519PublicKey.from_public_bytes(_decode(verify_key, length=32)).verify(
            _decode(signature, length=64),
            _message(
                base_external_id=base_external_id,
                application_id=application_id,
            ),
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("temporary Lakebase orphan marker signature is invalid") from exc
    expected_display, expected_external = _render_contract(
        base_external_id=base_external_id,
        application_id=application_id,
        verify_key=verify_key,
        signature=signature,
    )
    if display_name != expected_display:
        raise RuntimeError("temporary Lakebase orphan marker encoding drifted")
    return application_id, expected_external


def orphan_tombstones(
    client: Any,
    *,
    base_external_id: str,
) -> list[tuple[str, str, str, str]]:
    target_digest = hashlib.sha256(base_external_id.encode()).hexdigest()[:12]
    display_prefix = f"{_ORPHAN_TOMBSTONE_DISPLAY_PREFIX}{target_digest}:"
    results: list[tuple[str, str, str, str]] = []
    for candidate in client.service_principals.list():
        candidate_display = str(getattr(candidate, "display_name", "") or "")
        if not candidate_display.startswith(display_prefix):
            continue
        candidate_id = str(getattr(candidate, "id", "") or "").strip()
        if not candidate_id:
            raise RuntimeError("temporary Lakebase orphan marker has no immutable id")
        exact = client.service_principals.get(candidate_id)
        exact_display = str(getattr(exact, "display_name", "") or "")
        if candidate_display != exact_display:
            raise RuntimeError("temporary Lakebase orphan marker inventory changed")
        application_id, expected_external_id = _decode_orphan_tombstone(
            exact_display,
            base_external_id=base_external_id,
        )
        marker_application_id = str(getattr(exact, "application_id", "") or "").strip()
        if (
            not marker_application_id
            or str(getattr(exact, "external_id", "") or "") != expected_external_id
            or getattr(exact, "active", None) is not False
            or any(getattr(exact, field, None) for field in ("groups", "roles", "entitlements"))
            or list(client.service_principal_secrets_proxy.list(candidate_id))
        ):
            raise RuntimeError("temporary Lakebase orphan marker contract drifted")
        if any(
            str(getattr(app, "service_principal_client_id", "") or "")
            == marker_application_id
            for app in client.apps.list()
        ):
            raise RuntimeError("temporary Lakebase orphan marker is bound to an App")
        if any(marker[1] == application_id for marker in results):
            raise RuntimeError("temporary Lakebase orphan marker inventory is ambiguous")
        results.append(
            (
                candidate_id,
                application_id,
                exact_display,
                expected_external_id,
            )
        )
    return results


def ensure_orphan_tombstone(
    client: Any,
    *,
    base_external_id: str,
    application_id: str,
    signing_key: str,
) -> tuple[str, str, str, str]:
    display_name, external_id = orphan_tombstone_contract(
        base_external_id=base_external_id,
        application_id=application_id,
        signing_key=signing_key,
    )
    matches = [
        item
        for item in orphan_tombstones(client, base_external_id=base_external_id)
        if item[1] == application_id
    ]
    if not matches:
        with suppress(Exception):  # ambiguous create must resolve by exact inventory
            client.service_principals.create(
                display_name=display_name,
                external_id=external_id,
                active=False,
            )
        matches = [
            item
            for item in orphan_tombstones(client, base_external_id=base_external_id)
            if item[1] == application_id
        ]
    if len(matches) != 1:
        raise RuntimeError("temporary Lakebase orphan marker creation was ambiguous")
    return matches[0]


def delete_orphan_tombstone(
    client: Any,
    *,
    tombstone_id: str,
    base_external_id: str,
    attempts: int = 15,
) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        markers = orphan_tombstones(client, base_external_id=base_external_id)
        if not any(marker[0] == tombstone_id for marker in markers):
            return
        try:
            client.service_principals.delete(tombstone_id)
            last_error = None
        except Exception as exc:  # noqa: BLE001 - retry ambiguous deletion
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error is not None else ""
    raise RuntimeError(f"temporary Lakebase orphan marker deletion did not converge{detail}")
