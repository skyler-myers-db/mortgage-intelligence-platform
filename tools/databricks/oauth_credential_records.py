"""Immutable workspace records for OAuth credential mutation recovery."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from databricks.sdk.errors import (
    AlreadyExists,
    NotFound,
    ResourceAlreadyExists,
    ResourceDoesNotExist,
)
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks.app_deployment_lease_support import key_registry
from tools.databricks.oauth_credential_record_schema import (
    has_exact_string_fields,
)
from tools.databricks.probe_deadlines import bounded_workspace_read

INTENT_VERSION = 4
OBSERVED_VERSION = 1
SINK_ATTEMPT_VERSION = 1
DELIVERY_ACK_VERSION = 2
RESOLUTION_VERSION = 4
QUARANTINE_VERSION = 2
INTENT_SUFFIX = ".oauth-credential-intent.json"
OBSERVED_SUFFIX = ".oauth-credential-observed.json"
SINK_ATTEMPT_SUFFIX = ".oauth-credential-sink-attempt.json"
DELIVERY_ACK_SUFFIX = ".oauth-credential-delivery-ack.json"
RESOLUTION_SUFFIX = ".oauth-credential-resolution.json"
QUARANTINE_SUFFIX = ".oauth-credential-quarantine.json"
MAX_LEASE_ROOT_OBJECTS = 10_000
ATTESTATION_ALGORITHM = "ed25519-oauth-credential-mutation-v1"
CREDENTIAL_MUTATION_LEASE_NAME = "mip-oauth-credential-mutations"
_SIGNED_FIELDS = {
    "attestation_algorithm",
    "attestation_key_epoch",
    "attestation_signature",
    "attestation_verify_key",
}


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(
            value.strip() + "=" * (-len(value.strip()) % 4)
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OAuth credential recovery key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("OAuth credential recovery key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def canonical_sha256(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _attestation_message(payload: dict[str, object]) -> bytes:
    unsigned = {
        key: value for key, value in payload.items() if key not in _SIGNED_FIELDS
    }
    return b"mip-oauth-credential-mutation-v1\0" + canonical_json(unsigned)


def _sign(payload: dict[str, object]) -> dict[str, object]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing, length=32))
    derived = _encode(private.public_key().public_bytes_raw())
    registry = key_registry()
    if derived != verify or registry[-1] != verify:
        raise RuntimeError(
            "OAuth credential recovery signing identity is invalid"
        )
    unsigned = {
        key: value for key, value in payload.items() if key not in _SIGNED_FIELDS
    }
    return {
        **unsigned,
        "attestation_algorithm": ATTESTATION_ALGORITHM,
        "attestation_key_epoch": registry.index(verify),
        "attestation_verify_key": verify,
        "attestation_signature": _encode(
            private.sign(_attestation_message(unsigned))
        ),
    }


def _verify(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth credential recovery record is malformed")
    normalized = {str(key): value for key, value in payload.items()}
    unsigned = {
        key: value
        for key, value in normalized.items()
        if key not in _SIGNED_FIELDS
    }
    verify = field(normalized, "attestation_verify_key")
    registry = key_registry()
    if (
        set(normalized) != set(unsigned) | _SIGNED_FIELDS
        or not has_exact_string_fields(
            normalized,
            _SIGNED_FIELDS - {"attestation_key_epoch"},
        )
        or normalized.get("attestation_algorithm") != ATTESTATION_ALGORITHM
        or verify not in registry
        or type(normalized.get("attestation_key_epoch")) is not int
        or normalized.get("attestation_key_epoch") != registry.index(verify)
    ):
        raise RuntimeError(
            "OAuth credential recovery attestation identity is invalid"
        )
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(verify, length=32))
        public.verify(
            _decode(field(normalized, "attestation_signature"), length=64),
            _attestation_message(unsigned),
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError(
            "OAuth credential recovery record signature is invalid"
        ) from exc
    return unsigned


def field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    if isinstance(value, dict):
        return (
            raw
            if isinstance(raw, str) and raw == raw.strip()
            else ""
        )
    return str(getattr(raw, "value", raw) or "").strip()


def lease_root() -> str:
    from tools.databricks.app_deployment_lease import LEASE_ROOT

    return LEASE_ROOT


def validate_app_name(app_name: str) -> str:
    from tools.databricks.app_deployment_lease import _path

    reviewed = app_name.strip()
    _path(reviewed)
    return reviewed


def record_paths(workspace: Any) -> tuple[str, ...]:
    root = lease_root()
    try:
        objects = tuple(workspace.workspace.list(root))
    except (NotFound, ResourceDoesNotExist):
        return ()
    if len(objects) > MAX_LEASE_ROOT_OBJECTS:
        raise RuntimeError("OAuth credential recovery inventory is unbounded")
    paths = tuple(
        sorted(
            path
            for item in objects
            if (path := field(item, "path")).startswith(f"{root}/")
            and path.endswith(
                (
                    INTENT_SUFFIX,
                    OBSERVED_SUFFIX,
                    SINK_ATTEMPT_SUFFIX,
                    DELIVERY_ACK_SUFFIX,
                    RESOLUTION_SUFFIX,
                    QUARANTINE_SUFFIX,
                )
            )
        )
    )
    if len(paths) != len(set(paths)):
        raise RuntimeError("OAuth credential recovery inventory is duplicated")
    return paths


def read_bytes(workspace: Any, path: str) -> bytes:
    # Bounded, retried download: the SDK's streaming read can stall
    # indefinitely on a held-open response, and the quarantine inventory
    # walks every ledger record (2026-08-10 faulthandler capture). See
    # tools.databricks.probe_deadlines.bounded_workspace_read.
    try:
        return bounded_workspace_read(workspace, path)
    except (NotFound, ResourceDoesNotExist) as exc:
        raise RuntimeError(
            f"OAuth credential recovery record disappeared: {path}"
        ) from exc


def read_json(workspace: Any, path: str) -> tuple[dict[str, object], bytes]:
    encoded = read_bytes(workspace, path)
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"OAuth credential recovery record is not valid JSON: {path}"
        ) from exc
    try:
        return _verify(value), encoded
    except RuntimeError as exc:
        raise RuntimeError(
            f"OAuth credential recovery record is not authoritative: {path}"
        ) from exc


def write_immutable_json(
    workspace: Any,
    *,
    path: str,
    payload: dict[str, object],
) -> bytes:
    encoded = canonical_json(_sign(payload))
    try:
        workspace.workspace.upload(
            path,
            io.BytesIO(encoded),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except (AlreadyExists, ResourceAlreadyExists):
        pass
    except Exception as upload_error:
        try:
            persisted = read_bytes(workspace, path)
        except RuntimeError as read_error:
            raise RuntimeError(
                "OAuth credential recovery record could not be persisted"
            ) from read_error
        if persisted != encoded:
            raise RuntimeError(
                "OAuth credential recovery upload failed without an exact commit"
            ) from upload_error
    if read_bytes(workspace, path) != encoded:
        raise RuntimeError("OAuth credential recovery record is not authoritative")
    return encoded


def intent_path(app_name: str, lease_id: str, mutation_id: str) -> str:
    reviewed = validate_app_name(app_name)
    return (
        f"{lease_root()}/{reviewed}.{lease_id}.{mutation_id}"
        f"{INTENT_SUFFIX}"
    )


def resolution_path(intent_record_path: str) -> str:
    if not intent_record_path.endswith(INTENT_SUFFIX):
        raise ValueError("OAuth credential intent path is invalid")
    return f"{intent_record_path.removesuffix(INTENT_SUFFIX)}{RESOLUTION_SUFFIX}"


def observed_path(intent_record_path: str) -> str:
    if not intent_record_path.endswith(INTENT_SUFFIX):
        raise ValueError("OAuth credential intent path is invalid")
    return f"{intent_record_path.removesuffix(INTENT_SUFFIX)}{OBSERVED_SUFFIX}"


def sink_attempt_path(intent_record_path: str) -> str:
    if not intent_record_path.endswith(INTENT_SUFFIX):
        raise ValueError("OAuth credential intent path is invalid")
    return (
        f"{intent_record_path.removesuffix(INTENT_SUFFIX)}"
        f"{SINK_ATTEMPT_SUFFIX}"
    )


def delivery_ack_path(intent_record_path: str) -> str:
    if not intent_record_path.endswith(INTENT_SUFFIX):
        raise ValueError("OAuth credential intent path is invalid")
    return (
        f"{intent_record_path.removesuffix(INTENT_SUFFIX)}"
        f"{DELIVERY_ACK_SUFFIX}"
    )


def quarantine_path(app_name: str, lease_id: str) -> str:
    reviewed = validate_app_name(app_name)
    return f"{lease_root()}/{reviewed}.{lease_id}{QUARANTINE_SUFFIX}"


def _exact_string_list(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} is malformed")
    items = tuple(item for item in value if isinstance(item, str))
    if (
        len(items) != len(value)
        or any(not item or item != item.strip() for item in items)
        or tuple(sorted(items)) != items
        or len(items) != len(set(items))
    ):
        raise RuntimeError(f"{label} is malformed")
    return items


def exact_string_list(value: object, *, label: str) -> tuple[str, ...]:
    """Expose exact canonical list validation to the inventory module."""

    return _exact_string_list(value, label=label)


def validate_intent(
    path: str,
    record: dict[str, object],
) -> tuple[str, str, str]:
    expected = {
        "version",
        "app_name",
        "outer_app_name",
        "lease_id",
        "lease_recovery_root_id",
        "lease_generation_id",
        "lease_generation_seq",
        "lease_record_sha256",
        "mutation_id",
        "source_git_sha",
        "label",
        "principal_id",
        "authority_scope",
        "authority_identity",
        "provider_api",
        "operation_mode",
        "sink_descriptor",
        "sink_repository",
        "sink_secret_names",
        "sink_atomic_credential_bundle",
        "retirement_mode",
        "credential_lifetime_seconds",
        "before_credential_ids",
    }
    app_name = field(record, "app_name")
    lease_id = field(record, "lease_id")
    mutation_id = field(record, "mutation_id")
    try:
        lease_generation_id = str(UUID(field(record, "lease_generation_id")))
        canonical_lease_id = str(UUID(lease_id))
        recovery_root_lease_id = str(
            UUID(field(record, "lease_recovery_root_id"))
        )
    except ValueError as exc:
        raise RuntimeError(f"OAuth credential intent is malformed: {path}") from exc
    lease_generation_seq = record.get("lease_generation_seq")
    credential_lifetime_seconds = record.get("credential_lifetime_seconds")
    sink_secret_names = _exact_string_list(
        record.get("sink_secret_names"),
        label="OAuth credential intent sink secret names",
    )
    sink_repository = field(record, "sink_repository")
    sink_atomic = record.get("sink_atomic_credential_bundle")
    retirement_mode = field(record, "retirement_mode")
    canonical_sink = (
        f"github:{sink_repository}:atomic={str(sink_atomic).lower()}:"
        + ",".join(sink_secret_names)
    )
    lease_record_sha256 = field(record, "lease_record_sha256")
    if (
        set(record) != expected
        or not has_exact_string_fields(
            record,
            expected
            - {
                "version",
                "lease_generation_seq",
                "sink_secret_names",
                "sink_atomic_credential_bundle",
                "credential_lifetime_seconds",
                "before_credential_ids",
            },
        )
        or type(record.get("version")) is not int
        or record.get("version") != INTENT_VERSION
        or app_name != CREDENTIAL_MUTATION_LEASE_NAME
        or lease_id != canonical_lease_id
        or field(record, "lease_recovery_root_id") != recovery_root_lease_id
        or not lease_generation_id
        or field(record, "lease_generation_id") != lease_generation_id
        or not isinstance(lease_generation_seq, int)
        or isinstance(lease_generation_seq, bool)
        or lease_generation_seq < 0
        or not isinstance(credential_lifetime_seconds, int)
        or isinstance(credential_lifetime_seconds, bool)
        or credential_lifetime_seconds < 0
        or len(lease_record_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in lease_record_sha256
        )
        or len(field(record, "source_git_sha")) != 40
        or any(
            character not in "0123456789abcdef"
            for character in field(record, "source_git_sha")
        )
        or not field(record, "label")
        or not field(record, "principal_id")
        or not field(record, "outer_app_name")
        or validate_app_name(field(record, "outer_app_name"))
        != field(record, "outer_app_name")
        or field(record, "authority_scope") not in {"workspace", "account"}
        or not field(record, "authority_identity")
        or field(record, "operation_mode")
        not in {"persistent_delivery", "temporary_probe"}
        or not field(record, "provider_api")
        or not field(record, "sink_descriptor")
        or not isinstance(sink_atomic, bool)
        or retirement_mode not in {"immediate", "signed_app_cutover"}
        or (
            field(record, "operation_mode") == "temporary_probe"
            and (
                credential_lifetime_seconds == 0
                or sink_repository
                or sink_secret_names
                or sink_atomic
                or retirement_mode != "immediate"
            )
        )
        or (
            retirement_mode == "signed_app_cutover"
            and (
                field(record, "operation_mode") != "persistent_delivery"
                or sink_atomic is not True
            )
        )
        or (
            field(record, "operation_mode") == "persistent_delivery"
            and (
                credential_lifetime_seconds != 0
                or not sink_repository
                or not sink_secret_names
                or field(record, "sink_descriptor") != canonical_sink
            )
        )
        or mutation_id != lease_id
        or path != intent_path(app_name, lease_id, mutation_id)
    ):
        raise RuntimeError(f"OAuth credential intent is malformed: {path}")
    _exact_string_list(
        record.get("before_credential_ids"),
        label="OAuth credential intent prior inventory",
    )
    return app_name, lease_id, mutation_id


def validate_resolution(
    path: str,
    record: dict[str, object],
    *,
    intent_record_path: str,
    intent_encoded: bytes,
    intent_record: dict[str, object],
    observed_record: dict[str, object] | None,
    observed_encoded: bytes | None,
    sink_record: dict[str, object] | None,
    sink_encoded: bytes | None,
    delivery_ack_record: dict[str, object] | None,
    delivery_ack_encoded: bytes | None,
    canonical_resolver_lease_record: dict[str, str | int],
) -> None:
    from tools.databricks.oauth_credential_resolution_record import (
        validate_resolution as validate_resolution_record,
    )

    validate_resolution_record(
        path,
        record,
        intent_record_path=intent_record_path,
        intent_encoded=intent_encoded,
        intent_record=intent_record,
        observed_record=observed_record,
        observed_encoded=observed_encoded,
        sink_record=sink_record,
        sink_encoded=sink_encoded,
        delivery_ack_record=delivery_ack_record,
        delivery_ack_encoded=delivery_ack_encoded,
        canonical_resolver_lease_record=canonical_resolver_lease_record,
    )


def validate_observed(
    path: str,
    record: dict[str, object],
    *,
    intent_record_path: str,
    intent_encoded: bytes,
    intent_record: dict[str, object],
) -> None:
    expected = {
        "version",
        "intent_path",
        "intent_sha256",
        "app_name",
        "lease_id",
        "lease_generation_id",
        "lease_generation_seq",
        "lease_record_sha256",
        "mutation_id",
        "principal_id",
        "credential_id",
        "observed_credential_ids",
    }
    observed_ids = _exact_string_list(
        record.get("observed_credential_ids"),
        label="OAuth credential observed inventory",
    )
    before_ids = _exact_string_list(
        intent_record.get("before_credential_ids"),
        label="OAuth credential intent prior inventory",
    )
    credential_id = field(record, "credential_id")
    if (
        set(record) != expected
        or not has_exact_string_fields(
            record,
            expected
            - {"version", "lease_generation_seq", "observed_credential_ids"},
        )
        or type(record.get("version")) is not int
        or record.get("version") != OBSERVED_VERSION
        or path != observed_path(intent_record_path)
        or field(record, "intent_path") != intent_record_path
        or field(record, "intent_sha256")
        != hashlib.sha256(intent_encoded).hexdigest()
        or any(
            field(record, name) != field(intent_record, name)
            for name in (
                "app_name",
                "lease_id",
                "lease_generation_id",
                "lease_record_sha256",
                "mutation_id",
                "principal_id",
            )
        )
        or type(record.get("lease_generation_seq")) is not int
        or record.get("lease_generation_seq")
        != intent_record.get("lease_generation_seq")
        or not credential_id
        or credential_id in before_ids
        or set(observed_ids) != set(before_ids) | {credential_id}
    ):
        raise RuntimeError(f"OAuth credential observation is malformed: {path}")


def validate_sink_attempt(
    path: str,
    record: dict[str, object],
    *,
    intent_record_path: str,
    intent_encoded: bytes,
    intent_record: dict[str, object],
    observed_encoded: bytes,
) -> None:
    expected = {
        "version",
        "intent_path",
        "intent_sha256",
        "observed_path",
        "observed_sha256",
        "repository",
        "secret_names",
        "atomic_credential_bundle",
    }
    secret_names = _exact_string_list(
        record.get("secret_names"),
        label="OAuth credential sink secret names",
    )
    if (
        set(record) != expected
        or not has_exact_string_fields(
            record,
            expected - {"version", "secret_names", "atomic_credential_bundle"},
        )
        or type(record.get("version")) is not int
        or record.get("version") != SINK_ATTEMPT_VERSION
        or path != sink_attempt_path(intent_record_path)
        or field(record, "intent_path") != intent_record_path
        or field(record, "intent_sha256")
        != hashlib.sha256(intent_encoded).hexdigest()
        or field(record, "observed_path") != observed_path(intent_record_path)
        or field(record, "observed_sha256")
        != hashlib.sha256(observed_encoded).hexdigest()
        or field(record, "repository") != field(intent_record, "sink_repository")
        or secret_names
        != _exact_string_list(
            intent_record.get("sink_secret_names"),
            label="OAuth credential intent sink secret names",
        )
        or type(record.get("atomic_credential_bundle")) is not bool
        or record.get("atomic_credential_bundle")
        != intent_record.get("sink_atomic_credential_bundle")
    ):
        raise RuntimeError(f"OAuth credential sink attempt is malformed: {path}")


def validate_delivery_ack(
    path: str,
    record: dict[str, object],
    *,
    intent_record_path: str,
    intent_encoded: bytes,
    intent_record: dict[str, object],
    observed_record: dict[str, object],
    observed_encoded: bytes,
    sink_encoded: bytes,
) -> None:
    expected = {
        "version",
        "intent_path",
        "intent_sha256",
        "observed_path",
        "observed_sha256",
        "sink_attempt_path",
        "sink_attempt_sha256",
        "credential_id",
        "acknowledged_credential_ids",
        "retire_credential_ids",
        "retirement_mode",
    }
    before_ids = _exact_string_list(
        intent_record.get("before_credential_ids"),
        label="OAuth credential intent prior inventory",
    )
    acknowledged_ids = _exact_string_list(
        record.get("acknowledged_credential_ids"),
        label="OAuth credential acknowledged inventory",
    )
    retire_ids = _exact_string_list(
        record.get("retire_credential_ids"),
        label="OAuth credential retirement inventory",
    )
    credential_id = field(observed_record, "credential_id")
    if (
        set(record) != expected
        or not has_exact_string_fields(
            record,
            expected
            - {
                "version",
                "acknowledged_credential_ids",
                "retire_credential_ids",
            },
        )
        or type(record.get("version")) is not int
        or record.get("version") != DELIVERY_ACK_VERSION
        or path != delivery_ack_path(intent_record_path)
        or field(record, "intent_path") != intent_record_path
        or field(record, "intent_sha256")
        != hashlib.sha256(intent_encoded).hexdigest()
        or field(record, "observed_path") != observed_path(intent_record_path)
        or field(record, "observed_sha256")
        != hashlib.sha256(observed_encoded).hexdigest()
        or field(record, "sink_attempt_path")
        != sink_attempt_path(intent_record_path)
        or field(record, "sink_attempt_sha256")
        != hashlib.sha256(sink_encoded).hexdigest()
        or field(record, "credential_id") != credential_id
        or set(acknowledged_ids) != set(before_ids) | {credential_id}
        or retire_ids != before_ids
        or field(record, "retirement_mode")
        != field(intent_record, "retirement_mode")
    ):
        raise RuntimeError(
            f"OAuth credential delivery acknowledgement is malformed: {path}"
        )


def validate_quarantine(path: str, record: dict[str, object]) -> str:
    expected = {
        "version",
        "app_name",
        "lease_id",
        "source_git_sha",
        "label",
        "principal_id",
        "intent_path",
        "before_credential_ids",
        "candidate_credential_ids",
    }
    app_name = field(record, "app_name")
    lease_id = field(record, "lease_id")
    linked_intent_path = field(record, "intent_path")
    source_git_sha = field(record, "source_git_sha")
    if (
        set(record) != expected
        or not has_exact_string_fields(
            record,
            expected
            - {
                "version",
                "before_credential_ids",
                "candidate_credential_ids",
            },
        )
        or type(record.get("version")) is not int
        or record.get("version") != QUARANTINE_VERSION
        or not field(record, "label")
        or not field(record, "principal_id")
        or record.get("intent_path") != linked_intent_path
        or len(source_git_sha) != 40
        or any(
            character not in "0123456789abcdef"
            for character in source_git_sha
        )
        or (
            linked_intent_path
            and (
                not linked_intent_path.startswith(f"{lease_root()}/")
                or not linked_intent_path.endswith(INTENT_SUFFIX)
            )
        )
        or path != quarantine_path(app_name, lease_id)
    ):
        raise RuntimeError(f"OAuth credential quarantine is malformed: {path}")
    for name in ("before_credential_ids", "candidate_credential_ids"):
        _exact_string_list(
            record.get(name),
            label=f"OAuth credential quarantine {name}",
        )
    return linked_intent_path


def unresolved_record_paths(
    workspace: Any,
    *,
    allowed_intent_path: str = "",
) -> tuple[str, ...]:
    from tools.databricks.oauth_credential_record_inventory import (
        unresolved_record_paths as inventory_unresolved_record_paths,
    )

    return inventory_unresolved_record_paths(
        workspace,
        allowed_intent_path=allowed_intent_path,
    )

def sorted_ids(values: Iterable[str]) -> list[str]:
    from tools.databricks.oauth_credential_record_inventory import (
        sorted_ids as inventory_sorted_ids,
    )

    return inventory_sorted_ids(values)
