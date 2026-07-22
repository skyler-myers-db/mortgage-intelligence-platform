"""Durable signed tombstones for control-plane-only Lakebase bootstrap roles."""

from __future__ import annotations

import base64
import hashlib
import time
from contextlib import suppress
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from databricks.sdk.errors import NotFound
from tools.databricks.app_deployment_lease_support import key_registry
from tools.databricks.lakebase_oauth_role_account_inventory import (
    assert_exact_account_marker_contract,
    assert_no_workspace_app_binding,
    exact_account_principals_by_display_prefix,
)
from tools.databricks.lakebase_oauth_role_account_principal import (
    assert_account_workspace_assignment_boundary,
    assert_exact_account_principal_has_no_secrets,
    assert_no_account_workspace_assignments,
)

_ORPHAN_TOMBSTONE_V2_DISPLAY_PREFIX = "o2"
_ORPHAN_TOMBSTONE_V3_DISPLAY_PREFIX = "p"
_SCIM_DISPLAY_NAME_LIMIT = 100
_SCIM_PRINCIPAL_ID_BYTES = 6
_V3_TARGET_DIGEST_LENGTH = 5
_V3_APPLICATION_ID_LENGTH = 22
_V3_PRINCIPAL_ID_LENGTH = 8
_DELETION_DEADLINE_SECONDS = 180.0
_DELETION_STABILITY_SECONDS = 30.0
_DELETION_POLL_SECONDS = 2.0

OrphanTombstone = tuple[str, str, str, str, str | None]


class _TombstoneAccountReappeared(RuntimeError):
    pass


def _assert_unique_version_per_original(
    markers: list[OrphanTombstone],
    *,
    application_id: str,
    principal_id: str | None,
) -> None:
    same_version = [
        marker
        for marker in markers
        if marker[1] == application_id and (marker[4] is None) == (principal_id is None)
    ]
    if same_version:
        raise RuntimeError("temporary Lakebase orphan marker inventory is ambiguous")


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
    try:
        canonical = str(UUID(normalized))
    except ValueError as exc:
        raise RuntimeError("temporary Lakebase orphan marker application id is invalid") from exc
    if normalized != canonical:
        raise RuntimeError("temporary Lakebase orphan marker application id is invalid")
    return canonical


def _validate_principal_id(principal_id: str) -> str:
    normalized = principal_id.strip()
    if (
        not normalized
        or not normalized.isascii()
        or not normalized.isdecimal()
        or normalized.startswith("0")
    ):
        raise RuntimeError("temporary Lakebase orphan marker principal id is invalid")
    numeric = int(normalized)
    if numeric <= 0 or numeric >= 1 << (_SCIM_PRINCIPAL_ID_BYTES * 8):
        raise RuntimeError("temporary Lakebase orphan marker principal id is invalid")
    return normalized


def _v2_message(*, base_external_id: str, application_id: str) -> bytes:
    return f"mip-lakebase-orphan-v2\0{base_external_id}\0{application_id}".encode()


def _v3_message(
    *,
    base_external_id: str,
    application_id: str,
    principal_id: str,
) -> bytes:
    return (
        "mip-lakebase-orphan-v3\0" f"{base_external_id}\0{application_id}\0{principal_id}"
    ).encode()


def _v2_display_prefix(base_external_id: str) -> str:
    target_digest = _encode(hashlib.sha256(base_external_id.encode()).digest()[:7])
    return f"{_ORPHAN_TOMBSTONE_V2_DISPLAY_PREFIX}{target_digest}."


def _v3_target_digest(base_external_id: str) -> str:
    return _encode(hashlib.sha256(base_external_id.encode()).digest())[:_V3_TARGET_DIGEST_LENGTH]


def orphan_tombstone_display_prefix(base_external_id: str) -> str:
    """Return the exact account-SCIM inventory prefix for one target."""

    return f"{_ORPHAN_TOMBSTONE_V3_DISPLAY_PREFIX}{_v3_target_digest(base_external_id)}"


def _orphan_tombstone_display_prefixes(base_external_id: str) -> tuple[str, str]:
    return (
        orphan_tombstone_display_prefix(base_external_id),
        _v2_display_prefix(base_external_id),
    )


def _render_contract(
    *,
    base_external_id: str,
    application_id: str,
    principal_id: str,
    signature: bytes,
) -> tuple[str, str]:
    application_id = _validate_application_id(application_id)
    principal_id = _validate_principal_id(principal_id)
    if len(signature) != 64:
        raise RuntimeError("temporary Lakebase orphan marker signature is invalid")
    role_id = _encode(UUID(application_id).bytes)
    encoded_principal_id = _encode(int(principal_id).to_bytes(_SCIM_PRINCIPAL_ID_BYTES, "big"))
    marker_application_id = str(UUID(bytes=signature[:16]))
    display_name = (
        f"{orphan_tombstone_display_prefix(base_external_id)}"
        f"{role_id}{encoded_principal_id}{_encode(signature[16:])}"
    )
    if (
        len(display_name) != _SCIM_DISPLAY_NAME_LIMIT
        or len(display_name.encode("utf-8")) != _SCIM_DISPLAY_NAME_LIMIT
    ):
        raise RuntimeError("temporary Lakebase orphan display name exceeds SCIM limits")
    if marker_application_id == application_id:
        raise RuntimeError("temporary Lakebase orphan marker identity collides with its role")
    return display_name, marker_application_id


def orphan_tombstone_contract(
    *,
    base_external_id: str,
    application_id: str,
    principal_id: str,
    signing_key: str,
) -> tuple[str, str]:
    """Create a v3 marker binding both original immutable SCIM identifiers."""

    application_id = _validate_application_id(application_id)
    principal_id = _validate_principal_id(principal_id)
    verify_key = derive_gateway_proof_verify_key(signing_key)
    registry = key_registry()
    if verify_key != registry[-1]:
        raise RuntimeError("temporary Lakebase orphan signer is not the current proof key")
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing_key, length=32))
    signature = private.sign(
        _v3_message(
            base_external_id=base_external_id,
            application_id=application_id,
            principal_id=principal_id,
        )
    )
    return _render_contract(
        base_external_id=base_external_id,
        application_id=application_id,
        principal_id=principal_id,
        signature=signature,
    )


def _render_v2_contract(
    *,
    base_external_id: str,
    application_id: str,
    signature: bytes,
) -> tuple[str, str]:
    """Render the deployed v2 format for strict read-compatibility checks."""

    application_id = _validate_application_id(application_id)
    if len(signature) != 64:
        raise RuntimeError("temporary Lakebase orphan marker signature is invalid")
    role_id = _encode(UUID(application_id).bytes)
    marker_application_id = str(UUID(bytes=signature[:16]))
    display_name = f"{_v2_display_prefix(base_external_id)}{role_id}.{_encode(signature[16:])}"
    if (
        len(display_name) != _SCIM_DISPLAY_NAME_LIMIT
        or len(display_name.encode("utf-8")) != _SCIM_DISPLAY_NAME_LIMIT
    ):
        raise RuntimeError("temporary Lakebase orphan display name exceeds SCIM limits")
    if marker_application_id == application_id:
        raise RuntimeError("temporary Lakebase orphan marker identity collides with its role")
    return display_name, marker_application_id


def _decode_orphan_tombstone(
    display_name: str,
    *,
    base_external_id: str,
    marker_application_id: str,
) -> tuple[str, str | None, str]:
    v3_prefix = orphan_tombstone_display_prefix(base_external_id)
    v2_prefix = _v2_display_prefix(base_external_id)
    if display_name.startswith(v3_prefix):
        version = 3
        prefix = v3_prefix
    elif display_name.startswith(v2_prefix):
        version = 2
        prefix = v2_prefix
    else:
        raise RuntimeError("temporary Lakebase orphan marker display prefix drifted")
    payload = display_name[len(prefix) :]
    if version == 3:
        if len(payload) != (_V3_APPLICATION_ID_LENGTH + _V3_PRINCIPAL_ID_LENGTH + 64):
            raise RuntimeError("temporary Lakebase orphan marker payload is invalid")
        encoded_role_id = payload[:_V3_APPLICATION_ID_LENGTH]
        encoded_principal_id = payload[
            _V3_APPLICATION_ID_LENGTH : _V3_APPLICATION_ID_LENGTH + _V3_PRINCIPAL_ID_LENGTH
        ]
        encoded_signature_tail = payload[_V3_APPLICATION_ID_LENGTH + _V3_PRINCIPAL_ID_LENGTH :]
    else:
        fields = payload.split(".")
        if len(fields) != 2:
            raise RuntimeError("temporary Lakebase orphan marker payload is invalid")
        encoded_role_id, encoded_signature_tail = fields
        encoded_principal_id = None
    try:
        application_id = str(UUID(bytes=_decode(encoded_role_id, length=16)))
        principal_id = (
            str(
                int.from_bytes(
                    _decode(str(encoded_principal_id), length=_SCIM_PRINCIPAL_ID_BYTES),
                    "big",
                )
            )
            if encoded_principal_id is not None
            else None
        )
        if principal_id is not None:
            principal_id = _validate_principal_id(principal_id)
        signature = UUID(marker_application_id).bytes + _decode(
            encoded_signature_tail,
            length=48,
        )
    except (OverflowError, ValueError) as exc:
        raise RuntimeError("temporary Lakebase orphan marker payload is invalid") from exc
    message = (
        _v3_message(
            base_external_id=base_external_id,
            application_id=application_id,
            principal_id=str(principal_id),
        )
        if version == 3
        else _v2_message(
            base_external_id=base_external_id,
            application_id=application_id,
        )
    )
    verified = False
    for verify_key in key_registry():
        try:
            Ed25519PublicKey.from_public_bytes(_decode(verify_key, length=32)).verify(
                signature,
                message,
            )
            verified = True
            break
        except (InvalidSignature, RuntimeError, ValueError):
            continue
    if not verified:
        raise RuntimeError("temporary Lakebase orphan marker signature is invalid")
    expected_display, expected_marker_application_id = (
        _render_contract(
            base_external_id=base_external_id,
            application_id=application_id,
            principal_id=str(principal_id),
            signature=signature,
        )
        if version == 3
        else _render_v2_contract(
            base_external_id=base_external_id,
            application_id=application_id,
            signature=signature,
        )
    )
    if display_name != expected_display or marker_application_id != expected_marker_application_id:
        raise RuntimeError("temporary Lakebase orphan marker encoding drifted")
    return application_id, principal_id, expected_marker_application_id


def orphan_tombstones(
    client: Any,
    *,
    base_external_id: str,
    account_client: Any | None = None,
) -> list[OrphanTombstone]:
    display_prefixes = _orphan_tombstone_display_prefixes(base_external_id)
    results: list[OrphanTombstone] = []
    workspace_ids: set[str] = set()
    for candidate in client.service_principals.list():
        candidate_display = str(getattr(candidate, "display_name", "") or "")
        if not candidate_display.startswith(display_prefixes):
            continue
        candidate_id = str(getattr(candidate, "id", "") or "").strip()
        if not candidate_id:
            raise RuntimeError("temporary Lakebase orphan marker has no immutable id")
        exact = client.service_principals.get(candidate_id)
        exact_display = str(getattr(exact, "display_name", "") or "")
        if candidate_display != exact_display:
            raise RuntimeError("temporary Lakebase orphan marker inventory changed")
        marker_application_id = str(getattr(exact, "application_id", "") or "").strip()
        application_id, principal_id, expected_marker_application_id = _decode_orphan_tombstone(
            exact_display,
            base_external_id=base_external_id,
            marker_application_id=marker_application_id,
        )
        if (
            marker_application_id != expected_marker_application_id
            or str(getattr(exact, "external_id", "") or "").strip()
            or getattr(exact, "active", None) is not False
            or any(getattr(exact, field, None) for field in ("groups", "roles", "entitlements"))
            or list(client.service_principal_secrets_proxy.list(candidate_id))
        ):
            raise RuntimeError("temporary Lakebase orphan marker contract drifted")
        if any(
            str(getattr(app, "service_principal_client_id", "") or "") == marker_application_id
            for app in client.apps.list()
        ):
            raise RuntimeError("temporary Lakebase orphan marker is bound to an App")
        _assert_unique_version_per_original(
            results,
            application_id=application_id,
            principal_id=principal_id,
        )
        results.append(
            (
                candidate_id,
                application_id,
                exact_display,
                marker_application_id,
                principal_id,
            )
        )
        workspace_ids.add(candidate_id)
    if account_client is not None:
        account_ids: set[str] = set()
        account_candidates: dict[str, Any] = {}
        for display_prefix in display_prefixes:
            for exact in exact_account_principals_by_display_prefix(
                account_client,
                display_prefix=display_prefix,
            ):
                candidate_id = str(getattr(exact, "id", "") or "").strip()
                if candidate_id in account_candidates:
                    raise RuntimeError(
                        "temporary Lakebase account orphan marker inventory is ambiguous"
                    )
                account_candidates[candidate_id] = exact
        for exact in account_candidates.values():
            candidate_id, marker_application_id, exact_display = (
                assert_exact_account_marker_contract(exact)
            )
            account_ids.add(candidate_id)
            application_id, principal_id, expected_marker_application_id = _decode_orphan_tombstone(
                exact_display,
                base_external_id=base_external_id,
                marker_application_id=marker_application_id,
            )
            if marker_application_id != expected_marker_application_id:
                raise RuntimeError("temporary Lakebase account orphan marker contract drifted")
            assert_exact_account_principal_has_no_secrets(
                account_client,
                principal_id=candidate_id,
            )
            assert_account_workspace_assignment_boundary(
                account_client,
                client,
                principal_id=candidate_id,
                application_id=marker_application_id,
                display_name=exact_display,
                expected_workspace_active=False,
            )
            assert_no_workspace_app_binding(
                client,
                application_ids={marker_application_id, application_id},
            )
            matching_id = next((marker for marker in results if marker[0] == candidate_id), None)
            expected = (
                candidate_id,
                application_id,
                exact_display,
                marker_application_id,
                principal_id,
            )
            if matching_id is not None:
                if matching_id != expected:
                    raise RuntimeError("temporary Lakebase orphan marker inventory changed")
                continue
            _assert_unique_version_per_original(
                results,
                application_id=application_id,
                principal_id=principal_id,
            )
            results.append(expected)
        if workspace_ids - account_ids:
            raise RuntimeError("temporary Lakebase account orphan marker inventory changed")
    return results


def ensure_orphan_tombstone(
    client: Any,
    *,
    base_external_id: str,
    application_id: str,
    principal_id: str,
    signing_key: str,
    account_client: Any | None = None,
) -> OrphanTombstone:
    principal_id = _validate_principal_id(principal_id)
    display_name, marker_application_id = orphan_tombstone_contract(
        base_external_id=base_external_id,
        application_id=application_id,
        principal_id=principal_id,
        signing_key=signing_key,
    )
    markers = orphan_tombstones(
        client,
        base_external_id=base_external_id,
        account_client=account_client,
    )
    conflicting_v3 = [
        item
        for item in markers
        if item[1] == application_id and item[4] is not None and item[4] != principal_id
    ]
    if conflicting_v3:
        raise RuntimeError("temporary Lakebase orphan marker principal identity is ambiguous")
    matches = [item for item in markers if item[1] == application_id and item[4] == principal_id]
    if not matches:
        with suppress(Exception):  # ambiguous create must resolve by exact inventory
            client.service_principals.create(
                application_id=marker_application_id,
                display_name=display_name,
                active=False,
            )
        markers = orphan_tombstones(
            client,
            base_external_id=base_external_id,
            account_client=account_client,
        )
        conflicting_v3 = [
            item
            for item in markers
            if item[1] == application_id and item[4] is not None and item[4] != principal_id
        ]
        if conflicting_v3:
            raise RuntimeError("temporary Lakebase orphan marker principal identity is ambiguous")
        matches = [
            item for item in markers if item[1] == application_id and item[4] == principal_id
        ]
    if len(matches) != 1:
        raise RuntimeError("temporary Lakebase orphan marker creation was ambiguous")
    return matches[0]


def _assert_direct_tombstone_contract(
    principal: Any,
    *,
    tombstone_id: str,
    marker_application_id: str,
    display_name: str,
    expected_active: bool,
) -> None:
    immutable = tuple(
        str(getattr(principal, field, "") or "")
        for field in ("id", "application_id", "display_name", "external_id")
    )
    if (
        immutable != (tombstone_id, marker_application_id, display_name, "")
        or getattr(principal, "active", None) is not expected_active
        or any(getattr(principal, field, None) for field in ("groups", "roles", "entitlements"))
    ):
        raise RuntimeError("temporary Lakebase direct orphan marker contract drifted")


def _prove_tombstone_absent_by_direct_id(
    client: Any,
    account_client: Any,
    *,
    tombstone_id: str,
    marker_application_id: str | None,
    display_name: str | None,
    deadline_at: float,
    stability_seconds: float = _DELETION_STABILITY_SECONDS,
    poll_seconds: float = _DELETION_POLL_SECONDS,
) -> None:
    """Require two-plane direct-ID absence for a monotonic stability window."""

    if (
        not tombstone_id
        or deadline_at <= time.monotonic()
        or stability_seconds < 0
        or poll_seconds <= 0
    ):
        raise RuntimeError("temporary Lakebase tombstone absence proof is incomplete")
    stable_since: float | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline_at:
        account_absent = False
        workspace_absent = False
        try:
            account_principal = account_client.service_principals.get(tombstone_id)
        except NotFound:
            account_absent = True
        else:
            if marker_application_id is None or display_name is None:
                raise RuntimeError(
                    "temporary Lakebase tombstone remains present outside signed inventory"
                )
            _assert_direct_tombstone_contract(
                account_principal,
                tombstone_id=tombstone_id,
                marker_application_id=marker_application_id,
                display_name=display_name,
                expected_active=True,
            )
            raise _TombstoneAccountReappeared("temporary Lakebase account tombstone reappeared")
        try:
            workspace_principal = client.service_principals.get(tombstone_id)
        except NotFound:
            workspace_absent = True
        else:
            if marker_application_id is None or display_name is None:
                raise RuntimeError(
                    "temporary Lakebase tombstone remains present outside signed inventory"
                )
            _assert_direct_tombstone_contract(
                workspace_principal,
                tombstone_id=tombstone_id,
                marker_application_id=marker_application_id,
                display_name=display_name,
                expected_active=False,
            )
            if list(client.service_principal_secrets_proxy.list(tombstone_id)):
                raise RuntimeError("temporary Lakebase workspace tombstone has credentials")
        if account_absent and workspace_absent:
            try:
                assert_no_account_workspace_assignments(
                    account_client,
                    principal_id=tombstone_id,
                )
                if marker_application_id is not None:
                    assert_no_workspace_app_binding(
                        client,
                        application_ids={marker_application_id},
                    )
            except Exception as exc:  # noqa: BLE001 - reset the stability window
                stable_since = None
                last_error = exc
            else:
                observed_at = time.monotonic()
                if stable_since is None:
                    stable_since = observed_at
                last_error = None
                if observed_at - stable_since >= stability_seconds:
                    return
        else:
            stable_since = None
            last_error = RuntimeError("temporary Lakebase tombstone remains present")
        time.sleep(poll_seconds)
    detail = f"; last_error={type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"temporary Lakebase tombstone direct absence did not stabilize{detail}")


def upgrade_v2_orphan_tombstone(
    client: Any,
    *,
    account_client: Any,
    base_external_id: str,
    application_id: str,
    principal_id: str,
    signing_key: str,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
    allow_unlocked_recovery_for_tests: bool = False,
) -> OrphanTombstone:
    """Atomically hand recovery authority from v2 to an exact-postflighted v3."""

    v3 = ensure_orphan_tombstone(
        client,
        base_external_id=base_external_id,
        application_id=application_id,
        principal_id=principal_id,
        signing_key=signing_key,
        account_client=account_client,
    )
    matches = [
        marker
        for marker in orphan_tombstones(
            client,
            base_external_id=base_external_id,
            account_client=account_client,
        )
        if marker[1] == application_id
    ]
    exact_v3 = [marker for marker in matches if marker[4] == principal_id]
    v2 = [marker for marker in matches if marker[4] is None]
    if exact_v3 != [v3] or len(v2) > 1:
        raise RuntimeError("temporary Lakebase v2-to-v3 marker handoff is ambiguous")
    if not v2:
        return v3
    delete_orphan_tombstone(
        client,
        account_client=account_client,
        tombstone_id=v2[0][0],
        base_external_id=base_external_id,
        bootstrap_lock_cursor=bootstrap_lock_cursor,
        bootstrap_lock_key=bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
    )
    remaining = [
        marker
        for marker in orphan_tombstones(
            client,
            base_external_id=base_external_id,
            account_client=account_client,
        )
        if marker[1] == application_id
    ]
    if remaining != [v3]:
        raise RuntimeError("temporary Lakebase v3 marker post-migration contract drifted")
    return v3


def delete_orphan_tombstone(
    client: Any,
    *,
    account_client: Any,
    tombstone_id: str,
    base_external_id: str,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
    allow_unlocked_recovery_for_tests: bool = False,
    attempts: int = 3,
    deadline_seconds: float = _DELETION_DEADLINE_SECONDS,
) -> None:
    from tools.databricks.lakebase_oauth_role_account_principal import (
        retire_exact_account_principal,
    )

    exact: OrphanTombstone | None = None
    last_error: Exception | None = None
    if attempts < 3 or deadline_seconds < _DELETION_STABILITY_SECONDS:
        raise RuntimeError("temporary Lakebase tombstone deletion contract is incomplete")
    for attempt in range(attempts):
        if exact is None:
            markers = orphan_tombstones(
                client,
                base_external_id=base_external_id,
                account_client=account_client,
            )
            exact = next((marker for marker in markers if marker[0] == tombstone_id), None)
            if exact is None:
                _prove_tombstone_absent_by_direct_id(
                    client,
                    account_client,
                    tombstone_id=tombstone_id,
                    marker_application_id=None,
                    display_name=None,
                    deadline_at=time.monotonic() + deadline_seconds,
                )
                return
        try:
            retire_exact_account_principal(
                account_client,
                client,
                principal_id=exact[0],
                application_id=exact[3],
                display_name=exact[2],
                bootstrap_lock_cursor=bootstrap_lock_cursor,
                bootstrap_lock_key=bootstrap_lock_key,
                allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
                deadline_seconds=deadline_seconds,
            )
            _prove_tombstone_absent_by_direct_id(
                client,
                account_client,
                tombstone_id=tombstone_id,
                marker_application_id=exact[3],
                display_name=exact[2],
                deadline_at=time.monotonic() + deadline_seconds,
            )
            return
        except _TombstoneAccountReappeared as exc:
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 - retry ambiguous deletion
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(_DELETION_POLL_SECONDS)
    detail = f"; last_error={type(last_error).__name__}" if last_error is not None else ""
    raise RuntimeError(f"temporary Lakebase orphan marker deletion did not converge{detail}")
