"""Signed immutable-ID provenance for managed serving-query groups."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.agents.gateway_contract import reviewed_workspace_https_origin
from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks.app_deployment_lease import LEASE_ROOT, assert_held
from tools.databricks.app_deployment_lease_support import key_registry

PROVENANCE_VERSION = 1
ATTESTATION_ALGORITHM = "ed25519-serving-query-group-v1"
_SIGNED_FIELDS = {
    "attestation_algorithm",
    "attestation_signature",
    "attestation_verify_key",
}
_REQUIRED_FIELDS = {
    "version",
    "app_name",
    "workspace_id",
    "workspace_host",
    "endpoint_id",
    "application_id",
    "service_principal_id",
    "group_name",
    "external_id",
    "creation_nonce",
    "origin_lease_id",
    "origin_source_git_sha",
    "admitted_lease_id",
    "admitted_source_git_sha",
    "prepared_at",
    "admitted_at",
    "group_id",
    "claimed_at",
    "claim_proof_kind",
    *_SIGNED_FIELDS,
}
_CLAIM_PROOF_KINDS = {"create_response", "signed_intent_projection"}
INTENT_EXTERNAL_ID_PREFIX = "mip:sq:v2:"


class MissingClaimedGroupProvenanceError(RuntimeError):
    """The exact endpoint/application binding has no signed immutable-ID claim."""


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("serving-query group provenance key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("serving-query group provenance key has an invalid length")
    return decoded


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNED_FIELDS}
    return b"mip-serving-query-group-v1\0" + _canonical(unsigned).encode("utf-8")


def _sign(record: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    if not signing or not verify or derive_gateway_proof_verify_key(signing) != verify:
        raise RuntimeError("serving-query group provenance signing identity is invalid")
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing, length=32))
    unsigned = {key: value for key, value in record.items() if key not in _SIGNED_FIELDS}
    return {
        **unsigned,
        "attestation_algorithm": ATTESTATION_ALGORITHM,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(unsigned))),
    }


def _verify(record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RuntimeError("serving-query group provenance is malformed")
    verify = _text(record.get("attestation_verify_key"))
    if record.get("attestation_algorithm") != ATTESTATION_ALGORITHM or verify not in key_registry():
        raise RuntimeError("serving-query group provenance attestation is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(_decode(verify, length=32)).verify(
            _decode(_text(record.get("attestation_signature")), length=64),
            _message(record),
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("serving-query group provenance signature is invalid") from exc
    return dict(record)


def _app_name(value: str) -> str:
    app_name = value.strip()
    if (
        not app_name
        or len(app_name) > 63
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for character in app_name
        )
        or not app_name[0].isalnum()
        or not app_name[-1].isalnum()
    ):
        raise RuntimeError("serving-query group provenance App name is invalid")
    return app_name


def _deployment_identity(lease_value: str, source_value: str) -> tuple[str, str]:
    lease_id = lease_value.strip()
    source_git_sha = source_value.strip()
    try:
        lease_id = str(UUID(lease_id))
    except ValueError as exc:
        raise RuntimeError("serving-query group provenance lease ID is invalid") from exc
    if len(source_git_sha) != 40 or any(
        character not in "0123456789abcdef" for character in source_git_sha
    ):
        raise RuntimeError("serving-query group provenance source SHA is invalid")
    return lease_id, source_git_sha


def _workspace_identity(workspace: Any) -> tuple[str, str]:
    workspace_id = _text(workspace.get_workspace_id())
    host = reviewed_workspace_https_origin(
        _text(getattr(getattr(workspace, "config", None), "host", ""))
    )
    if not workspace_id.isdecimal():
        raise RuntimeError("serving-query group workspace ID is invalid")
    return workspace_id, host


def _path(*, app_name: str, endpoint_id: str, application_id: str) -> str:
    digest = hashlib.sha256(f"{endpoint_id}\0{application_id}".encode()).hexdigest()
    return f"{LEASE_ROOT}/{app_name}.serving-query-group-{digest}.json"


def intent_external_id(
    *,
    endpoint_id: str,
    application_id: str,
    creation_nonce: str,
) -> str:
    """Return the nonce-bound SCIM marker for one signed create intent."""

    nonce = str(UUID(creation_nonce))
    digest = hashlib.sha256(
        f"{endpoint_id}\0{application_id}\0{nonce}".encode()
    ).digest()
    return f"{INTENT_EXTERNAL_ID_PREFIX}{_encode(digest)}"


def _validated(
    value: object,
    *,
    app_name: str,
    workspace_id: str,
    workspace_host: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    group_name: str,
) -> dict[str, Any]:
    record = _verify(value)
    if record.get("version") != PROVENANCE_VERSION or set(record) != _REQUIRED_FIELDS:
        raise RuntimeError("serving-query group provenance is incomplete")
    strings = _REQUIRED_FIELDS - {"version"}
    if any(not isinstance(record.get(field), str) for field in strings):
        raise RuntimeError("serving-query group provenance is malformed")
    try:
        str(UUID(record["origin_lease_id"]))
        str(UUID(record["admitted_lease_id"]))
        creation_nonce = str(UUID(record["creation_nonce"]))
        prepared_at = datetime.fromisoformat(record["prepared_at"])
        admitted_at = datetime.fromisoformat(record["admitted_at"])
    except ValueError as exc:
        raise RuntimeError("serving-query group provenance identity is invalid") from exc
    claimed_at = record["claimed_at"]
    try:
        claimed = datetime.fromisoformat(claimed_at) if claimed_at else None
    except ValueError as exc:
        raise RuntimeError("serving-query group provenance claim time is invalid") from exc
    expected = (
        app_name,
        workspace_id,
        workspace_host,
        endpoint_id,
        application_id,
        service_principal_id,
        group_name,
    )
    actual = tuple(
        record[field]
        for field in (
            "app_name",
            "workspace_id",
            "workspace_host",
            "endpoint_id",
            "application_id",
            "service_principal_id",
            "group_name",
        )
    )
    source_shas = (
        record["origin_source_git_sha"],
        record["admitted_source_git_sha"],
    )
    group_id = record["group_id"]
    claim_proof_kind = record["claim_proof_kind"]
    if (
        actual != expected
        or prepared_at.tzinfo is None
        or admitted_at.tzinfo is None
        or any(len(source_sha) != 40 for source_sha in source_shas)
        or any(
            character not in "0123456789abcdef"
            for source_sha in source_shas
            for character in source_sha
        )
        or record["external_id"]
        != intent_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
            creation_nonce=creation_nonce,
        )
        or bool(group_id) != bool(claimed_at) or bool(group_id) != bool(claim_proof_kind)
        or (claim_proof_kind and claim_proof_kind not in _CLAIM_PROOF_KINDS)
        or (claimed is not None and claimed.tzinfo is None)
    ):
        raise RuntimeError("serving-query group provenance scope or claim is invalid")
    return record


def _download(
    workspace: Any,
    *,
    path: str,
    validation: dict[str, str],
) -> dict[str, Any] | None:
    try:
        stream = workspace.workspace.download(path)
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("serving-query group provenance is not valid JSON") from exc
    return _validated(value, **validation)


def _upload(
    workspace: Any,
    *,
    path: str,
    record: dict[str, Any],
    expected: dict[str, Any] | None,
    validation: dict[str, str],
    assert_single_writer: Callable[[], None],
) -> dict[str, Any]:
    signed = _validated(_sign(record), **validation)
    if _download(workspace, path=path, validation=validation) != expected:
        raise RuntimeError("serving-query group provenance changed before persistence")
    assert_single_writer()
    try:
        workspace.workspace.upload(
            path,
            io.BytesIO(_canonical(signed).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=expected is not None,
        )
    except Exception as write_error:  # noqa: BLE001 - resolve ambiguous server commit
        if _download(workspace, path=path, validation=validation) != signed:
            raise RuntimeError(
                "serving-query group provenance write did not commit exactly"
            ) from write_error
    persisted = _download(workspace, path=path, validation=validation)
    if persisted != signed:
        raise RuntimeError("serving-query group provenance did not persist exactly")
    return persisted


def prepare(
    workspace: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    group_name: str,
    assert_single_writer: Callable[[], None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist or read the signed intent before any WorkspaceGroup creation."""

    app_name = _app_name(app_name)
    lease_id, source_git_sha = _deployment_identity(
        deployment_lease_id,
        deployment_source_git_sha,
    )
    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    workspace_id, workspace_host = _workspace_identity(workspace)
    validation = {
        "app_name": app_name,
        "workspace_id": workspace_id,
        "workspace_host": workspace_host,
        "endpoint_id": endpoint_id,
        "application_id": application_id,
        "service_principal_id": service_principal_id,
        "group_name": group_name,
    }
    path = _path(
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    existing = _download(workspace, path=path, validation=validation)
    if existing is not None:
        if existing["group_id"] or (
            existing["admitted_lease_id"] == lease_id
            and existing["admitted_source_git_sha"] == source_git_sha
        ):
            return existing
        admitted = {
            **existing,
            "admitted_lease_id": lease_id,
            "admitted_source_git_sha": source_git_sha,
            "admitted_at": (now or datetime.now(UTC)).isoformat(),
        }
        return _upload(
            workspace,
            path=path,
            record=admitted,
            expected=existing,
            validation=validation,
            assert_single_writer=assert_single_writer,
        )
    creation_nonce = str(uuid4())
    record = {
        "version": PROVENANCE_VERSION,
        **validation,
        "external_id": intent_external_id(
            endpoint_id=endpoint_id,
            application_id=application_id,
            creation_nonce=creation_nonce,
        ),
        "creation_nonce": creation_nonce,
        "origin_lease_id": lease_id,
        "origin_source_git_sha": source_git_sha,
        "admitted_lease_id": lease_id,
        "admitted_source_git_sha": source_git_sha,
        "prepared_at": (now or datetime.now(UTC)).isoformat(),
        "admitted_at": (now or datetime.now(UTC)).isoformat(),
        "group_id": "",
        "claimed_at": "",
        "claim_proof_kind": "",
    }
    return _upload(
        workspace,
        path=path,
        record=record,
        expected=None,
        validation=validation,
        assert_single_writer=assert_single_writer,
    )


def admit_existing(
    workspace: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    group_name: str,
    expected_record: dict[str, Any],
    assert_single_writer: Callable[[], None],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """CAS-readmit one exact durable intent without manufacturing a replacement."""

    app_name = _app_name(app_name)
    lease_id, source_git_sha = _deployment_identity(
        deployment_lease_id,
        deployment_source_git_sha,
    )
    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    workspace_id, workspace_host = _workspace_identity(workspace)
    validation = {
        "app_name": app_name,
        "workspace_id": workspace_id,
        "workspace_host": workspace_host,
        "endpoint_id": endpoint_id,
        "application_id": application_id,
        "service_principal_id": service_principal_id,
        "group_name": group_name,
    }
    path = _path(
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    current = _download(workspace, path=path, validation=validation)
    if current is None:
        return None
    if current != expected_record:
        raise RuntimeError("serving-query group provenance changed before readmission")
    if current["group_id"] or (
        current["admitted_lease_id"] == lease_id
        and current["admitted_source_git_sha"] == source_git_sha
    ):
        return current
    admitted = {
        **current,
        "admitted_lease_id": lease_id,
        "admitted_source_git_sha": source_git_sha,
        "admitted_at": (now or datetime.now(UTC)).isoformat(),
    }
    return _upload(
        workspace,
        path=path,
        record=admitted,
        expected=current,
        validation=validation,
        assert_single_writer=assert_single_writer,
    )


def claim(
    workspace: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    record: dict[str, Any],
    group_id: str,
    proof_kind: str,
    assert_single_writer: Callable[[], None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bind a successful provider create response to its immutable group ID."""

    immutable_id = group_id.strip()
    if not immutable_id:
        raise RuntimeError("serving-query group provenance claim ID is missing")
    if proof_kind not in _CLAIM_PROOF_KINDS:
        raise RuntimeError("serving-query group claim proof kind is invalid")
    app_name = _app_name(app_name)
    lease_id, source_git_sha = _deployment_identity(
        deployment_lease_id,
        deployment_source_git_sha,
    )
    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    workspace_id, workspace_host = _workspace_identity(workspace)
    validation = {
        field: str(record[field])
        for field in (
            "app_name",
            "workspace_id",
            "workspace_host",
            "endpoint_id",
            "application_id",
            "service_principal_id",
            "group_name",
        )
    }
    if (
        validation["app_name"] != app_name
        or validation["workspace_id"] != workspace_id
        or validation["workspace_host"] != workspace_host
    ):
        raise RuntimeError("serving-query group claim belongs to another workspace")
    path = _path(
        app_name=app_name,
        endpoint_id=validation["endpoint_id"],
        application_id=validation["application_id"],
    )
    current = _download(workspace, path=path, validation=validation)
    if current != record:
        raise RuntimeError("serving-query group provenance changed before claim")
    if (
        current["admitted_lease_id"] != lease_id
        or current["admitted_source_git_sha"] != source_git_sha
    ):
        raise RuntimeError("serving-query group claim uses an unadmitted deployment")
    if current["group_id"]:
        if current["group_id"] != immutable_id:
            raise RuntimeError("serving-query group provenance claims another immutable ID")
        return current
    claimed = {
        **current,
        "group_id": immutable_id,
        "claimed_at": (now or datetime.now(UTC)).isoformat(),
        "claim_proof_kind": proof_kind,
    }
    return _upload(
        workspace,
        path=path,
        record=claimed,
        expected=current,
        validation=validation,
        assert_single_writer=assert_single_writer,
    )


def read_existing(
    workspace: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    group_name: str,
) -> dict[str, Any] | None:
    """Read and verify an existing signed intent or immutable-ID claim."""

    app_name = _app_name(app_name)
    workspace_id, workspace_host = _workspace_identity(workspace)
    validation = {
        "app_name": app_name,
        "workspace_id": workspace_id,
        "workspace_host": workspace_host,
        "endpoint_id": endpoint_id,
        "application_id": application_id,
        "service_principal_id": service_principal_id,
        "group_name": group_name,
    }
    return _download(
        workspace,
        path=_path(
            app_name=app_name,
            endpoint_id=endpoint_id,
            application_id=application_id,
        ),
        validation=validation,
    )


def require_claimed(
    workspace: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    group_name: str,
) -> dict[str, Any]:
    """Read and verify the exact signed immutable-ID claim for authorization."""

    record = read_existing(
        workspace,
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=service_principal_id,
        group_name=group_name,
    )
    if record is None or not record["group_id"]:
        raise MissingClaimedGroupProvenanceError(
            "managed serving-query group has no signed immutable-ID provenance"
        )
    return record
