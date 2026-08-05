"""Signed, immutable workspace journal for foreign-catalog binding remediation."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
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
from tools.databricks.app_deployment_lease import LEASE_ROOT
from tools.databricks.app_deployment_lease_support import key_registry

ATTESTATION_ALG = "ed25519-foreign-catalog-binding-v1"
_DOMAIN = b"mip-foreign-catalog-binding\0"
_SIGNATURE_FIELDS = {
    "attestation_alg",
    "attestation_verify_key",
    "attestation_signature",
}


class ForeignCatalogOperationNotFound(RuntimeError):
    """No immutable operation fence exists for the exact deployment lease."""


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("foreign-catalog journal key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("foreign-catalog journal key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNATURE_FIELDS}
    return _DOMAIN + canonical(unsigned).encode("utf-8")


def sign(record: dict[str, Any]) -> dict[str, Any]:
    if _SIGNATURE_FIELDS.intersection(record):
        raise RuntimeError("foreign-catalog journal record is already attested")
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
        raise RuntimeError("foreign-catalog journal signing and verification keys do not match")
    return {
        **record,
        "attestation_alg": ATTESTATION_ALG,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(record))),
    }


def verify(record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RuntimeError("foreign-catalog journal record is not an object")
    verify_key = str(record.get("attestation_verify_key") or "").strip()
    if record.get("attestation_alg") != ATTESTATION_ALG or verify_key not in key_registry():
        raise RuntimeError("foreign-catalog journal attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(verify_key, length=32))
        signature = _decode(
            str(record.get("attestation_signature") or ""),
            length=64,
        )
        public.verify(signature, _message(record))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("foreign-catalog journal signature is invalid") from exc
    return {str(key): value for key, value in record.items()}


def _safe_app_name(app_name: str) -> str:
    value = app_name.strip()
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in value):
        raise ValueError("foreign-catalog journal App name is invalid")
    return value


def _safe_uuid(value: str, label: str) -> str:
    try:
        return str(UUID(value.strip()))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"foreign-catalog journal {label} is invalid") from exc


def operation_root(app_name: str, operation_id: str) -> str:
    return (
        f"{LEASE_ROOT}/{_safe_app_name(app_name)}.foreign-catalog/"
        f"{_safe_uuid(operation_id, 'operation ID')}"
    )


def manifest_path(app_name: str, operation_id: str) -> str:
    return f"{operation_root(app_name, operation_id)}/manifest.json"


def fence_path(app_name: str, lease_id: str) -> str:
    return (
        f"{LEASE_ROOT}/{_safe_app_name(app_name)}.foreign-catalog."
        f"{_safe_uuid(lease_id, 'lease ID')}.fence.json"
    )


def event_path(
    app_name: str,
    operation_id: str,
    *,
    index: int,
    direction: str,
    phase: str,
    catalog: str,
) -> str:
    if index < 0 or direction != "apply":
        raise ValueError("foreign-catalog journal event identity is invalid")
    if phase not in {"intent", "converged"}:
        raise ValueError("foreign-catalog journal event phase is invalid")
    catalog_key = hashlib.sha256(catalog.encode("utf-8")).hexdigest()[:16]
    return (
        f"{operation_root(app_name, operation_id)}/events/"
        f"{index:04d}-{direction}-{phase}-{catalog_key}.json"
    )


def failure_path(
    app_name: str,
    operation_id: str,
    *,
    attempt_id: str,
    index: int,
    direction: str,
) -> str:
    return (
        f"{operation_root(app_name, operation_id)}/events/"
        f"{index:04d}-{direction}-failure-"
        f"{_safe_uuid(attempt_id, 'attempt ID')}.json"
    )


def completion_path(app_name: str, operation_id: str) -> str:
    return f"{operation_root(app_name, operation_id)}/complete.json"


def _download(workspace: Any, path: str) -> dict[str, Any] | None:
    try:
        stream = workspace.workspace.download(path)
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("foreign-catalog journal record is not valid JSON") from exc
    return verify(value)


def upload_once(workspace: Any, path: str, record: dict[str, Any]) -> None:
    signed = verify(record)
    try:
        workspace.workspace.upload(
            path,
            io.BytesIO(canonical(signed).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except (AlreadyExists, ResourceAlreadyExists):
        if _download(workspace, path) != signed:
            raise RuntimeError("foreign-catalog journal immutable record already differs") from None
        return
    except Exception as upload_error:
        try:
            persisted = _download(workspace, path)
        except Exception as read_error:
            raise RuntimeError(
                "foreign-catalog journal upload failed and commit is ambiguous"
            ) from read_error
        if persisted != signed:
            raise RuntimeError(
                "foreign-catalog journal upload failed without an exact commit"
            ) from upload_error
    if _download(workspace, path) != signed:
        raise RuntimeError("foreign-catalog journal record did not persist exactly")


def persist_operation(
    workspace: Any,
    *,
    manifest: dict[str, Any],
    app_name: str,
    lease_id: str,
) -> None:
    operation_id = str(manifest.get("operation_id") or "")
    root = operation_root(app_name, operation_id)
    workspace.workspace.mkdirs(f"{root}/events")
    upload_once(workspace, manifest_path(app_name, operation_id), manifest)
    fence = sign(
        {
            "version": 1,
            "kind": "foreign-catalog-binding-operation-fence",
            "app_name": app_name,
            "operation_id": operation_id,
            "lease_id": lease_id,
            "manifest_sha256": digest(manifest),
            "created_at": manifest["created_at"],
        }
    )
    upload_once(workspace, fence_path(app_name, lease_id), fence)


def assert_operation(
    workspace: Any,
    *,
    manifest: dict[str, Any],
    app_name: str,
    lease_id: str,
) -> None:
    operation_id = str(manifest.get("operation_id") or "")
    if _download(workspace, manifest_path(app_name, operation_id)) != manifest:
        raise RuntimeError("foreign-catalog signed manifest is not authoritative")
    fence = _download(workspace, fence_path(app_name, lease_id))
    expected = {
        "version": 1,
        "kind": "foreign-catalog-binding-operation-fence",
        "app_name": app_name,
        "operation_id": operation_id,
        "lease_id": lease_id,
        "manifest_sha256": digest(manifest),
        "created_at": manifest["created_at"],
    }
    if (
        fence is None
        or {key: value for key, value in fence.items() if key not in _SIGNATURE_FIELDS} != expected
    ):
        raise RuntimeError("foreign-catalog operation fence is not authoritative")


def recover_operation(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
) -> dict[str, Any]:
    fence = _download(workspace, fence_path(app_name, lease_id))
    if fence is None:
        raise ForeignCatalogOperationNotFound(
            "foreign-catalog operation fence does not exist for the recovery lease"
        )
    required = {
        "version",
        "kind",
        "app_name",
        "operation_id",
        "lease_id",
        "manifest_sha256",
        "created_at",
        *_SIGNATURE_FIELDS,
    }
    if (
        set(fence) != required
        or fence["version"] != 1
        or fence["kind"] != "foreign-catalog-binding-operation-fence"
        or fence["app_name"] != app_name
        or fence["lease_id"] != lease_id
    ):
        raise RuntimeError("foreign-catalog operation fence is invalid")
    operation_id = _safe_uuid(str(fence["operation_id"]), "operation ID")
    manifest = _download(workspace, manifest_path(app_name, operation_id))
    if manifest is None or digest(manifest) != fence["manifest_sha256"]:
        raise RuntimeError("foreign-catalog fenced manifest is missing or changed")
    return manifest


def operation_completed(
    workspace: Any,
    *,
    manifest: dict[str, Any],
    app_name: str,
) -> bool:
    operation_id = str(manifest.get("operation_id") or "")
    completion = _download(workspace, completion_path(app_name, operation_id))
    if completion is None:
        return False
    expected = {
        "version": 1,
        "kind": "foreign-catalog-binding-operation-completion",
        "app_name": app_name,
        "operation_id": operation_id,
        "manifest_sha256": digest(manifest),
        "lease_id": str(manifest["lease"]["lease_id"]),
    }
    if {
        key: value
        for key, value in completion.items()
        if key not in _SIGNATURE_FIELDS | {"completed_at"}
    } != expected:
        raise RuntimeError("foreign-catalog completion record is invalid")
    try:
        completed_at = datetime.fromisoformat(str(completion["completed_at"]))
    except (KeyError, ValueError) as exc:
        raise RuntimeError("foreign-catalog completion time is invalid") from exc
    if completed_at.tzinfo is None or completed_at.astimezone(UTC) < datetime.fromisoformat(
        str(manifest["created_at"])
    ).astimezone(UTC):
        raise RuntimeError("foreign-catalog completion time is invalid")
    return True


def complete_operation(
    workspace: Any,
    *,
    manifest: dict[str, Any],
    app_name: str,
    lease_id: str,
) -> None:
    assert_operation(
        workspace,
        manifest=manifest,
        app_name=app_name,
        lease_id=lease_id,
    )
    if operation_completed(workspace, manifest=manifest, app_name=app_name):
        return
    completion = sign(
        {
            "version": 1,
            "kind": "foreign-catalog-binding-operation-completion",
            "app_name": app_name,
            "operation_id": str(manifest["operation_id"]),
            "manifest_sha256": digest(manifest),
            "lease_id": lease_id,
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    upload_once(
        workspace,
        completion_path(app_name, str(manifest["operation_id"])),
        completion,
    )
    if not operation_completed(workspace, manifest=manifest, app_name=app_name):
        raise RuntimeError("foreign-catalog completion record is not authoritative")


def load_event(
    workspace: Any,
    path: str,
) -> dict[str, Any] | None:
    return _download(workspace, path)
