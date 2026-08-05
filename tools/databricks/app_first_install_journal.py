#!/usr/bin/env python3
"""Persist and reconcile lease-bound ownership of an unsigned first-install App."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks.app_deployment_lease import LEASE_ROOT, assert_held
from tools.databricks.app_deployment_lease_support import key_registry
from tools.databricks.app_rollback_record_contract import _load_record
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract,
    app_resource_contract_digest,
    validated_app_resource_contract,
)

JOURNAL_VERSION = 3
ATTESTATION_ALG = "ed25519-app-first-install-v3"
MARKER_PREFIX = "mip-first-install:"
AUDIT_SETTLEMENT_DELAY = timedelta(hours=1)
CREATE_AUTHORIZATION_WINDOW = timedelta(minutes=15)
_IMMUTABLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _path(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in normalized
    ):
        raise ValueError("first-install journal App name is invalid")
    return f"{LEASE_ROOT}/{normalized}.first-install.json"


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("first-install journal key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("first-install journal key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {
        key: value
        for key, value in record.items()
        if key not in {"attestation_alg", "attestation_verify_key", "attestation_signature"}
    }
    return b"mip-app-first-install\0" + json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sign(record: dict[str, Any]) -> dict[str, Any]:
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
        raise RuntimeError("first-install journal signing and verification keys do not match")
    return {
        **record,
        "attestation_alg": ATTESTATION_ALG,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(record))),
    }


def _verify(record: dict[str, Any]) -> None:
    verify = _text(record.get("attestation_verify_key"))
    if record.get("attestation_alg") != ATTESTATION_ALG or verify not in key_registry():
        raise RuntimeError("first-install journal attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(verify, length=32))
        signature = _decode(_text(record.get("attestation_signature")), length=64)
        public.verify(signature, _message(record))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("first-install journal signature is invalid") from exc


def _validated_payload(value: object, *, app_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"name", "description", "resources"}:
        raise RuntimeError("first-install App payload is invalid")
    if _text(value.get("name")) != app_name:
        raise RuntimeError("first-install App payload names another App")
    description = _text(value.get("description"))
    resources = validated_app_resource_contract(value.get("resources"))
    return {"name": app_name, "description": description, "resources": resources}


def _validated_record(value: object, *, app_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != JOURNAL_VERSION:
        raise RuntimeError("first-install journal version is invalid")
    _verify(value)
    required = {
        "version",
        "app_name",
        "bootstrap_id",
        "prepared_lease_id",
        "source_git_sha",
        "creator",
        "workspace_id",
        "prepared_at",
        "create_authorized_until",
        "audit_settlement_until",
        "original_description",
        "marked_description",
        "app_resources",
        "app_resources_sha256",
        "app_id",
        "app_service_principal_client_id",
        "app_service_principal_scim_id",
        "claimed_at",
        "claim_proof_kind",
        "create_audit_event_id",
        "create_audit_request_id",
        "attestation_alg",
        "attestation_verify_key",
        "attestation_signature",
    }
    if set(value) != required or value.get("app_name") != app_name:
        raise RuntimeError("first-install journal is incomplete")
    try:
        bootstrap_id = str(UUID(_text(value.get("bootstrap_id"))))
        prepared_at = datetime.fromisoformat(_text(value.get("prepared_at")))
        authorized_until = datetime.fromisoformat(
            _text(value.get("create_authorized_until"))
        )
        settlement_until = datetime.fromisoformat(
            _text(value.get("audit_settlement_until"))
        )
    except ValueError as exc:
        raise RuntimeError("first-install journal identity is invalid") from exc
    if (
        prepared_at.tzinfo is None
        or authorized_until.tzinfo is None
        or settlement_until.tzinfo is None
        or authorized_until <= prepared_at
        or settlement_until != authorized_until + AUDIT_SETTLEMENT_DELAY
    ):
        raise RuntimeError("first-install journal timestamp is invalid")
    source_git_sha = _text(value.get("source_git_sha"))
    creator = _text(value.get("creator"))
    workspace_id = _text(value.get("workspace_id"))
    lease_id = _text(value.get("prepared_lease_id"))
    original_description = _text(value.get("original_description"))
    marked_description = _text(value.get("marked_description"))
    app_id = _text(value.get("app_id"))
    app_client_id = _text(value.get("app_service_principal_client_id"))
    app_scim_id = _text(value.get("app_service_principal_scim_id"))
    claimed_at = _text(value.get("claimed_at"))
    claim_proof_kind = _text(value.get("claim_proof_kind"))
    audit_event_id = _text(value.get("create_audit_event_id"))
    audit_request_id = _text(value.get("create_audit_request_id"))
    expected_marker = f"[{MARKER_PREFIX}{bootstrap_id}]"
    if (
        len(source_git_sha) != 40
        or any(character not in "0123456789abcdef" for character in source_git_sha)
        or not creator
        or not workspace_id.isdigit()
        or not lease_id
        or not marked_description.endswith(expected_marker)
        or marked_description != f"{original_description} {expected_marker}".strip()
    ):
        raise RuntimeError("first-install journal ownership marker is invalid")
    claim_values = (app_id, app_client_id, app_scim_id, claimed_at, claim_proof_kind)
    if any(claim_values) != all(claim_values):
        raise RuntimeError("first-install journal App identity claim is incomplete")
    if all(claim_values):
        try:
            claimed_timestamp = datetime.fromisoformat(claimed_at)
        except ValueError as exc:
            raise RuntimeError("first-install journal claim timestamp is invalid") from exc
        if claimed_timestamp.tzinfo is None:
            raise RuntimeError("first-install journal claim timestamp is invalid")
        if any(
            _IMMUTABLE_ID.fullmatch(identity) is None
            for identity in (app_id, app_client_id, app_scim_id)
        ):
            raise RuntimeError("first-install journal App identity claim is invalid")
        if claim_proof_kind == "create_response":
            if audit_event_id or audit_request_id:
                raise RuntimeError("first-install create-response claim has audit fields")
        elif claim_proof_kind == "system_access_audit":
            if not audit_event_id or not audit_request_id:
                raise RuntimeError("first-install audit claim is incomplete")
            if any(
                _IMMUTABLE_ID.fullmatch(identity) is None
                for identity in (audit_event_id, audit_request_id)
            ):
                raise RuntimeError("first-install audit claim identity is invalid")
        else:
            raise RuntimeError("first-install journal claim proof is invalid")
    elif audit_event_id or audit_request_id:
        raise RuntimeError("unclaimed first-install journal has audit fields")
    resources = validated_app_resource_contract(value.get("app_resources"))
    if value.get("app_resources_sha256") != app_resource_contract_digest(resources):
        raise RuntimeError("first-install journal resource digest is invalid")
    return {
        **value,
        "bootstrap_id": bootstrap_id,
        "source_git_sha": source_git_sha,
        "creator": creator,
        "workspace_id": workspace_id,
        "prepared_lease_id": lease_id,
        "create_authorized_until": authorized_until.isoformat(),
        "audit_settlement_until": (
            authorized_until + AUDIT_SETTLEMENT_DELAY
        ).isoformat(),
        "original_description": original_description,
        "marked_description": marked_description,
        "app_resources": resources,
        "app_resources_sha256": app_resource_contract_digest(resources),
        "app_id": app_id,
        "app_service_principal_client_id": app_client_id,
        "app_service_principal_scim_id": app_scim_id,
        "claimed_at": claimed_at,
        "claim_proof_kind": claim_proof_kind,
        "create_audit_event_id": audit_event_id,
        "create_audit_request_id": audit_request_id,
    }


def _download(workspace: Any, *, app_name: str) -> dict[str, Any] | None:
    try:
        stream = workspace.workspace.download(_path(app_name))
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("first-install journal is not valid JSON") from exc
    return _validated_record(value, app_name=app_name)


def _delete_record_exact(
    workspace: Any,
    *,
    app_name: str,
    expected: dict[str, Any],
    lease_id: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> None:
    if _download(workspace, app_name=app_name) != expected:
        raise RuntimeError("first-install journal changed before exact deletion")
    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    try:
        workspace.workspace.delete(_path(app_name))
    except Exception as delete_error:
        after_error = _download(workspace, app_name=app_name)
        if after_error is None:
            return
        if after_error != expected:
            raise RuntimeError("first-install journal changed during deletion") from delete_error
        raise RuntimeError("first-install journal remained after deletion") from delete_error
    remaining = _download(workspace, app_name=app_name)
    if remaining is not None:
        raise RuntimeError("first-install journal remained after exact deletion")


def _app_or_none(workspace: Any, *, app_name: str) -> object | None:
    try:
        return workspace.apps.get(app_name)
    except (NotFound, ResourceDoesNotExist):
        return None


def _assert_app_metadata(workspace: Any, *, app_name: str, record: dict[str, Any]) -> object:
    app = _app_or_none(workspace, app_name=app_name)
    if app is None:
        raise RuntimeError("journaled first-install App is absent")
    if (
        _text(_field(app, "name")) != app_name
        or _text(_field(app, "description")) != record["marked_description"]
        or _text(_field(app, "creator")) != record["creator"]
        or app_resource_contract(workspace, app_name=app_name) != record["app_resources"]
    ):
        raise RuntimeError("unsigned App does not match the signed first-install ownership journal")
    return app


def _assert_owned_app(workspace: Any, *, app_name: str, record: dict[str, Any]) -> object:
    app = _assert_app_metadata(workspace, app_name=app_name, record=record)
    app_id = _text(_field(app, "id"))
    client_id = _text(_field(app, "service_principal_client_id"))
    scim_id = _text(_field(app, "service_principal_id"))
    if (
        not record["app_id"]
        or not record["app_service_principal_client_id"]
        or not record["app_service_principal_scim_id"]
        or app_id != record["app_id"]
        or client_id != record["app_service_principal_client_id"]
        or scim_id != record["app_service_principal_scim_id"]
    ):
        raise RuntimeError("unsigned App identity does not match the signed first-install claim")
    return app


def prepare(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist ownership intent before App creation and return its marked payload."""

    if _download(workspace, app_name=app_name) is not None:
        raise RuntimeError("a first-install journal already exists")
    lease = assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    reviewed = _validated_payload(payload, app_name=app_name)
    workspace_id = _text(_field(getattr(workspace, "config", None), "workspace_id"))
    if not workspace_id.isdigit():
        raise RuntimeError("first-install workspace identity is unavailable")
    prepared_at = now or datetime.now(UTC)
    try:
        lease_expires_at = datetime.fromisoformat(_text(lease.get("expires_at")))
    except ValueError as exc:
        raise RuntimeError("first-install creation lease expiration is invalid") from exc
    if lease_expires_at.tzinfo is None or lease_expires_at <= prepared_at:
        raise RuntimeError("first-install creation lease is not valid for App creation")
    authorized_until = min(
        lease_expires_at,
        prepared_at + CREATE_AUTHORIZATION_WINDOW,
    )
    bootstrap_id = str(uuid4())
    marker = f"[{MARKER_PREFIX}{bootstrap_id}]"
    marked_description = f"{reviewed['description']} {marker}".strip()
    record = {
        "version": JOURNAL_VERSION,
        "app_name": app_name,
        "bootstrap_id": bootstrap_id,
        "prepared_lease_id": lease_id,
        "source_git_sha": source_git_sha,
        "creator": _text(lease.get("holder")),
        "workspace_id": workspace_id,
        "prepared_at": prepared_at.isoformat(),
        "create_authorized_until": authorized_until.isoformat(),
        "audit_settlement_until": (
            authorized_until + AUDIT_SETTLEMENT_DELAY
        ).isoformat(),
        "original_description": reviewed["description"],
        "marked_description": marked_description,
        "app_resources": reviewed["resources"],
        "app_resources_sha256": app_resource_contract_digest(reviewed["resources"]),
        "app_id": "",
        "app_service_principal_client_id": "",
        "app_service_principal_scim_id": "",
        "claimed_at": "",
        "claim_proof_kind": "",
        "create_audit_event_id": "",
        "create_audit_request_id": "",
    }
    signed = _sign(record)
    workspace.workspace.upload(
        _path(app_name),
        io.BytesIO(json.dumps(signed, sort_keys=True).encode("utf-8")),
        format=ImportFormat.AUTO,
        overwrite=False,
    )
    if _download(workspace, app_name=app_name) != _validated_record(signed, app_name=app_name):
        raise RuntimeError("first-install journal did not persist exactly")
    return {**reviewed, "description": marked_description}


def _persist_identity_claim(
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
    app_id: str,
    client_id: str,
    scim_id: str,
    proof_kind: str,
    lease_id: str,
    source_git_sha: str,
    audit_event_id: str = "",
    audit_request_id: str = "",
    now: datetime | None = None,
) -> None:
    claimed = _validated_record(
        _sign(
            {
                **record,
                "app_id": app_id,
                "app_service_principal_client_id": client_id,
                "app_service_principal_scim_id": scim_id,
                "claimed_at": (now or datetime.now(UTC)).isoformat(),
                "claim_proof_kind": proof_kind,
                "create_audit_event_id": audit_event_id,
                "create_audit_request_id": audit_request_id,
            }
        ),
        app_name=app_name,
    )
    if _download(workspace, app_name=app_name) != record:
        raise RuntimeError("first-install journal changed immediately before identity claim")
    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    try:
        workspace.workspace.upload(
            _path(app_name),
            io.BytesIO(json.dumps(claimed, sort_keys=True).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=True,
        )
    except Exception as claim_error:
        after_error = _download(workspace, app_name=app_name)
        if after_error != claimed:
            if after_error == record:
                raise RuntimeError("first-install identity claim did not commit") from claim_error
            raise RuntimeError("first-install journal changed during identity claim") from claim_error
    if _download(workspace, app_name=app_name) != claimed:
        raise RuntimeError("first-install identity claim did not persist exactly")
    _assert_owned_app(workspace, app_name=app_name, record=claimed)


def claim_created_app(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    created_app: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Bind automatic cleanup to the immutable identity of the just-created App."""

    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    record = _download(workspace, app_name=app_name)
    if record is None:
        raise RuntimeError("first-install identity claim has no signed journal")
    if (
        record["prepared_lease_id"] != lease_id
        or record["source_git_sha"] != source_git_sha
        or record["app_id"]
        or record["app_service_principal_client_id"]
        or record["app_service_principal_scim_id"]
        or record["claimed_at"]
    ):
        raise RuntimeError("first-install identity claim is not owned by this creation lease")
    claim_time = now or datetime.now(UTC)
    if not (
        datetime.fromisoformat(record["prepared_at"])
        <= claim_time
        <= datetime.fromisoformat(record["create_authorized_until"])
    ):
        raise RuntimeError("first-install create-response claim window has expired")
    if _text(created_app.get("name")) != app_name:
        raise RuntimeError("first-install create response names another App")
    app_id = _text(created_app.get("id"))
    client_id = _text(created_app.get("service_principal_client_id"))
    scim_id = _text(created_app.get("service_principal_id"))
    if not app_id or not client_id or not scim_id:
        raise RuntimeError("first-install create response has no immutable App identity")
    app = _assert_app_metadata(workspace, app_name=app_name, record=record)
    if (
        _text(_field(app, "id")) != app_id
        or _text(_field(app, "service_principal_client_id")) != client_id
        or _text(_field(app, "service_principal_id")) != scim_id
    ):
        raise RuntimeError("live first-install App identity differs from the create response")
    _persist_identity_claim(
        workspace,
        app_name=app_name,
        record=record,
        app_id=app_id,
        client_id=client_id,
        scim_id=scim_id,
        proof_kind="create_response",
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=claim_time,
    )


def status(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    rollback_scope: str,
    expected_lakebase_instance: str,
) -> str:
    """Return the exact signed first-install recovery state."""

    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )
    record = _download(workspace, app_name=app_name)
    if record is None:
        return "absent"
    app = _app_or_none(workspace, app_name=app_name)
    if app is None:
        return "orphan_claimed" if record["app_id"] else "orphan_unclaimed"
    _assert_app_metadata(workspace, app_name=app_name, record=record)
    if not record["app_id"]:
        return "unclaimed"
    app = _assert_owned_app(workspace, app_name=app_name, record=record)
    try:
        rollback = _load_record(
            workspace,
            app_name=app_name,
            scope=rollback_scope,
            expected_lakebase_instance=expected_lakebase_instance,
        )
    except RuntimeError as exc:
        if str(exc).startswith("no server-owned last-good App rollback contract exists"):
            return "recover"
        raise
    client_id = _text(_field(app, "service_principal_client_id"))
    scim_id = _text(_field(app, "service_principal_id"))
    if (
        rollback["app_service_principal_client_id"] != client_id
        or rollback["app_service_principal_scim_id"] != scim_id
        or rollback["app_resources"] != record["app_resources"]
    ):
        raise RuntimeError("signed App rollback state conflicts with first-install ownership")
    return "signed"


def delete_recoverable(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    rollback_scope: str,
    expected_lakebase_instance: str,
) -> None:
    recovery_state = status(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        rollback_scope=rollback_scope,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    if recovery_state == "orphan_claimed":
        # The Apps delete may have committed before its response was lost. The
        # still-signed journal makes absence durable and authorizes only exact
        # record retirement under the current deployment lease.
        record = _download(workspace, app_name=app_name)
        if record is None:
            raise RuntimeError("first-install recovery has no signed journal")
        _delete_record_exact(
            workspace,
            app_name=app_name,
            expected=record,
            lease_id=lease_id,
            source_git_sha=source_git_sha,
        )
        return
    if recovery_state != "recover":
        raise RuntimeError("first-install App is not eligible for unsigned recovery")
    record = _download(workspace, app_name=app_name)
    if record is None:
        raise RuntimeError("first-install recovery has no signed journal")
    app = _assert_owned_app(workspace, app_name=app_name, record=record)
    compute = _text(_field(_field(app, "compute_status"), "state")).split(".")[-1].upper()
    if compute != "STOPPED" or _field(app, "pending_deployment") is not None:
        raise RuntimeError("journaled first-install App must be stopped without a pending deploy")
    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )
    try:
        workspace.apps.delete(app_name)
    except Exception as delete_error:
        remaining = _app_or_none(workspace, app_name=app_name)
        if remaining is not None:
            _assert_owned_app(workspace, app_name=app_name, record=record)
            raise RuntimeError("journaled first-install App deletion was ambiguous") from delete_error
    if _app_or_none(workspace, app_name=app_name) is not None:
        raise RuntimeError("journaled first-install App remained after deletion")
    _delete_record_exact(
        workspace,
        app_name=app_name,
        expected=record,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )


def complete(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    rollback_scope: str,
    expected_lakebase_instance: str,
) -> None:
    if status(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        rollback_scope=rollback_scope,
        expected_lakebase_instance=expected_lakebase_instance,
    ) != "signed":
        raise RuntimeError("first-install journal cannot complete without signed App state")
    record = _download(workspace, app_name=app_name)
    if record is None:
        raise RuntimeError("first-install journal disappeared before completion")
    _delete_record_exact(
        workspace,
        app_name=app_name,
        expected=record,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("first-install payload file is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("first-install payload file is not an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "prepare",
            "claim",
            "recover-claim",
            "status",
            "clear-absent",
            "delete",
            "complete",
        ),
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--lease-id")
    parser.add_argument("--source-git-sha")
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--created-app", type=Path)
    parser.add_argument("--out-payload", type=Path)
    parser.add_argument("--out-env", type=Path)
    parser.add_argument("--rollback-scope", default="mip-app-rollback")
    parser.add_argument("--lakebase-instance")
    parser.add_argument("--warehouse-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = WorkspaceClient()
    if not args.lease_id or not args.source_git_sha:
        raise RuntimeError(f"{args.action} requires --lease-id and --source-git-sha")
    common = {
        "workspace": workspace,
        "app_name": args.app_name,
        "lease_id": args.lease_id,
        "source_git_sha": args.source_git_sha,
    }
    if args.action == "prepare":
        if args.payload is None or args.out_payload is None:
            raise RuntimeError("prepare requires --payload and --out-payload")
        marked = prepare(**common, payload=_load_payload(args.payload))
        args.out_payload.write_text(json.dumps(marked, indent=2) + "\n", encoding="utf-8")
    elif args.action == "claim":
        if args.created_app is None:
            raise RuntimeError("claim requires --created-app")
        claim_created_app(**common, created_app=_load_payload(args.created_app))
    elif args.action == "recover-claim":
        from tools.databricks import app_first_install_recovery as recovery

        if not args.warehouse_id:
            raise RuntimeError("recover-claim requires --warehouse-id")
        recovery.recover_unclaimed_from_audit(
            **common, warehouse_id=args.warehouse_id
        )
    elif args.action == "status":
        if args.out_env is None or not args.lakebase_instance:
            raise RuntimeError("status requires --out-env and --lakebase-instance")
        current = status(
            **common,
            rollback_scope=args.rollback_scope,
            expected_lakebase_instance=args.lakebase_instance,
        )
        record = _download(workspace, app_name=args.app_name)
        app_id = record["app_id"] if record is not None else ""
        client_id = record["app_service_principal_client_id"] if record is not None else ""
        scim_id = record["app_service_principal_scim_id"] if record is not None else ""
        args.out_env.write_text(
            "".join(
                (
                    f"MIP_FIRST_INSTALL_JOURNAL_STATUS={shlex.quote(current)}\n",
                    f"MIP_FIRST_INSTALL_APP_ID={shlex.quote(app_id)}\n",
                    f"MIP_FIRST_INSTALL_APP_CLIENT_ID={shlex.quote(client_id)}\n",
                    f"MIP_FIRST_INSTALL_APP_SCIM_ID={shlex.quote(scim_id)}\n",
                )
            ),
            encoding="utf-8",
        )
    elif args.action == "clear-absent":
        from tools.databricks import app_first_install_recovery as recovery

        if not args.warehouse_id:
            raise RuntimeError("clear-absent requires --warehouse-id")
        recovery.clear_absent(**common, warehouse_id=args.warehouse_id)
    elif args.action == "delete":
        if not args.lakebase_instance:
            raise RuntimeError("delete requires --lakebase-instance")
        delete_recoverable(
            **common,
            rollback_scope=args.rollback_scope,
            expected_lakebase_instance=args.lakebase_instance,
        )
    else:
        if not args.lakebase_instance:
            raise RuntimeError("complete requires --lakebase-instance")
        complete(
            **common,
            rollback_scope=args.rollback_scope,
            expected_lakebase_instance=args.lakebase_instance,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
