#!/usr/bin/env python3
"""Acquire/release one signed workspace lease for a governed App deployment."""

from __future__ import annotations

import base64
import io
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from databricks.sdk import WorkspaceClient  # noqa: F401 - injected into CLI adapter
from databricks.sdk.errors import (
    AlreadyExists,
    NotFound,
    ResourceAlreadyExists,
    ResourceDoesNotExist,
)
from databricks.sdk.service.workspace import (
    ImportFormat,
    WorkspaceObjectAccessControlRequest,
    WorkspaceObjectPermissionLevel,
)
from tools.databricks import app_deployment_lease_recovery_checkpoint as recovery_checkpoint
from tools.databricks import app_deployment_lease_support as lease_support
from tools.databricks.app_deployment_lease_cli import (
    parent_is_expected as _parent_is_expected,  # noqa: F401
)
from tools.databricks.app_deployment_lease_cli import (
    source_sha as _source_sha,
)
from tools.databricks.probe_deadlines import bounded_workspace_read

LEGACY_LEASE_VERSION = 4
LEASE_VERSION = 5
SIGNED_LEASE_VERSIONS = {LEGACY_LEASE_VERSION, LEASE_VERSION}
LEASE_TTL = timedelta(hours=4)
MAX_ACTIVE_LEASE_LIFETIME = timedelta(hours=6)
LEGACY_TAKEOVER_GRACE = timedelta(minutes=5)
# /Shared grants `users` inherited CAN_MANAGE, which cannot be removed on a
# child. Keep the fence at the root, where only `admins` inherits management.
LEASE_ROOT = "/.mip-deployment-leases"
HEARTBEAT_INTERVAL_SECONDS = 60
WRITER_ACL_ATTESTATION_MAX_AGE = timedelta(seconds=3 * HEARTBEAT_INTERVAL_SECONDS)
MAX_SUCCESSORS_AFTER_HINT = 64
MAX_CANONICAL_GENERATIONS = 100_000


def _path(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in normalized
    ):
        raise ValueError("App deployment lease name is invalid")
    return f"{LEASE_ROOT}/{normalized}.json"


def _successor_path(app_name: str, generation_id: str) -> str:
    try:
        normalized_generation = str(UUID(generation_id.strip()))
    except (AttributeError, ValueError) as exc:
        raise RuntimeError("App deployment lease generation is invalid") from exc
    return f"{_path(app_name)}.{normalized_generation}.next"


def _head_path(app_name: str) -> str:
    return f"{_path(app_name)}.head"


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def _holder(workspace: Any) -> str:
    holder = str(_field(workspace.current_user.me(), "user_name") or "").strip()
    if not holder:
        raise RuntimeError("App deployment lease holder identity is unavailable")
    return holder


def _now() -> datetime:
    return datetime.now(UTC)


def _permission_levels(entry: object) -> set[str]:
    return {
        str(_field(permission, "permission_level") or "").split(".")[-1].upper()
        for permission in _items(_field(entry, "all_permissions"))
    } - {""}


def _root_object_id(workspace: Any) -> str:
    status = workspace.workspace.get_status(LEASE_ROOT)
    object_id = str(_field(status, "object_id") or "").strip()
    if not object_id:
        raise RuntimeError("App deployment lease directory has no immutable object ID")
    return object_id


def _assert_protected_root(
    workspace: Any,
    *,
    holder: str,
    writer_application_id: str,
    object_id: str,
) -> None:
    permissions = workspace.workspace.get_permissions("directories", object_id)
    holder_manage = False
    writer_read = False
    for entry in _items(_field(permissions, "access_control_list")):
        levels = _permission_levels(entry)
        user_name = str(_field(entry, "user_name") or "").strip()
        service_principal_name = str(_field(entry, "service_principal_name") or "").strip()
        group_name = str(_field(entry, "group_name") or "").strip().casefold()
        if user_name == holder and not service_principal_name and not group_name:
            if levels != {"CAN_MANAGE"}:
                raise RuntimeError("App deployment lease holder permission drifted")
            holder_manage = True
        elif service_principal_name == writer_application_id and not user_name and not group_name:
            if levels != {"CAN_READ"}:
                raise RuntimeError("App deployment lease writer permission drifted")
            writer_read = True
        elif group_name == "admins" and not user_name and not service_principal_name:
            if levels != {"CAN_MANAGE"}:
                raise RuntimeError("App deployment lease administrator permission drifted")
        elif levels:
            raise RuntimeError("App deployment lease directory has an unexpected accessor")
    if not holder_manage:
        raise RuntimeError("App deployment lease directory ACL did not converge")
    if not writer_read:
        raise RuntimeError("App deployment lease writer read boundary did not converge")


def _ensure_protected_root(
    workspace: Any,
    *,
    holder: str,
    writer_application_id: str,
) -> None:
    workspace.workspace.mkdirs(LEASE_ROOT)
    object_id = _root_object_id(workspace)
    workspace.workspace.set_permissions(
        "directories",
        object_id,
        access_control_list=[
            WorkspaceObjectAccessControlRequest(
                user_name=holder,
                permission_level=WorkspaceObjectPermissionLevel.CAN_MANAGE,
            ),
            WorkspaceObjectAccessControlRequest(
                service_principal_name=writer_application_id,
                permission_level=WorkspaceObjectPermissionLevel.CAN_READ,
            ),
        ],
    )
    _assert_protected_root(
        workspace,
        holder=holder,
        writer_application_id=writer_application_id,
        object_id=object_id,
    )


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("App deployment lease key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("App deployment lease key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {
        key: value
        for key, value in record.items()
        if key not in {"attestation_verify_key", "attestation_signature"}
    }
    return b"mip-app-deployment-lease\0" + json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_digest(record: dict[str, Any]) -> str:
    return lease_support.record_digest(__import__(__name__, fromlist=["*"]), record)


def _key_registry() -> list[str]:
    return lease_support.key_registry()


def _sign(record: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing, length=32))
    derived = _encode(private.public_key().public_bytes_raw())
    if derived != verify:
        raise RuntimeError("App deployment lease signing and verification keys do not match")
    registry = _key_registry()
    key_epoch = registry.index(verify)
    if not lease_support.metadata_is_exact(record, SIGNED_LEASE_VERSIONS, key_epoch):
        raise RuntimeError("App deployment lease signing metadata is invalid")
    return {
        **record,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(record))),
    }


def _verify(record: object) -> dict[str, Any]:
    if isinstance(record, dict) and lease_support.is_exact_integer(record.get("version"), 2):
        return lease_support.verify_legacy_v2(__import__(__name__, fromlist=["*"]), record)
    if not isinstance(record, dict) or not lease_support.is_exact_integer_member(
        record.get("version"), SIGNED_LEASE_VERSIONS
    ):
        raise RuntimeError("App deployment lease is invalid")
    registry = _key_registry()
    verify = str(record.get("attestation_verify_key") or "").strip()
    if verify not in registry:
        raise RuntimeError("App deployment lease attestation identity is invalid")
    normalized = {str(key): value for key, value in record.items()}
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(verify, length=32))
        signature = _decode(str(record.get("attestation_signature") or ""), length=64)
        public.verify(signature, _message(normalized))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("App deployment lease signature is invalid") from exc
    if not lease_support.is_exact_integer(normalized.get("key_epoch"), registry.index(verify)):
        raise RuntimeError("App deployment lease signing-key epoch is invalid")
    required = (
        lease_support.V5_BASE_FIELDS
        if normalized.get("version") == LEASE_VERSION
        else lease_support.V4_BASE_FIELDS
    )
    state = str(normalized.get("state") or "").strip()
    expected = required | ({"released_at"} if state == "released" else set())
    if normalized.get("version") == LEGACY_LEASE_VERSION and set(normalized) == expected - {
        "acl_attested_at"
    }:
        normalized["acl_attested_at"] = (
            _parse_timestamp(normalized, "expires_at") - LEASE_TTL
        ).isoformat()
    if set(normalized) != expected:
        raise RuntimeError("App deployment lease is incomplete")
    try:
        chain_id = str(UUID(str(normalized.get("chain_id") or "")))
        generation_id = str(UUID(str(normalized.get("generation_id") or "")))
        parent = str(normalized.get("parent_generation_id") or "").strip()
        parent_generation_id = str(UUID(parent)) if parent else ""
        lease_id = str(UUID(str(normalized.get("lease_id") or "")))
        recovery_root = str(UUID(str(normalized.get("recovery_root_lease_id") or "")))
    except ValueError as exc:
        raise RuntimeError("App deployment lease immutable identity is invalid") from exc
    if state not in {"active", "released"}:
        raise RuntimeError("App deployment lease state is invalid")
    operation = str(normalized.get("operation") or "").strip()
    generation_seq = normalized.get("generation_seq")
    parent_digest = str(normalized.get("parent_digest") or "").strip()
    if (
        operation not in {"acquire", "takeover", "renew", "release"}
        or not isinstance(generation_seq, int)
        or isinstance(generation_seq, bool)
        or generation_seq < 0
        or (parent_digest and len(parent_digest) != 64)
        or any(character not in "0123456789abcdef" for character in parent_digest)
        or (generation_seq == 0) != (not parent_generation_id and not parent_digest)
        or (generation_seq == 0 and operation != "acquire")
    ):
        raise RuntimeError("App deployment lease generation contract is invalid")
    if state == "released" and normalized.get("expires_at") != normalized.get("released_at"):
        raise RuntimeError("App deployment lease release timestamp is invalid")
    if state == "active" and "released_at" in normalized:
        raise RuntimeError("active App deployment lease has a release timestamp")
    normalized["chain_id"] = chain_id
    normalized["generation_id"] = generation_id
    normalized["parent_generation_id"] = parent_generation_id
    normalized["lease_id"] = lease_id
    normalized["recovery_root_lease_id"] = recovery_root
    if normalized["version"] == LEASE_VERSION:
        recovery_checkpoint.validate_lease_recovery_fields(normalized)
    lease_support.validate_v4_timestamps(__import__(__name__, fromlist=["*"]), normalized)
    return normalized


def _read_record(
    workspace: Any,
    *,
    path: str,
    app_name: str,
) -> dict[str, Any] | None:
    try:
        # Bounded, retried download — the streaming read stalls on held-open
        # responses (2026-08-10 capture); see probe_deadlines.
        encoded = bounded_workspace_read(workspace, path)
    except (NotFound, ResourceDoesNotExist):
        return None
    record = _verify(
        lease_support.json_without_duplicate_keys(
            encoded,
            artifact="App deployment lease",
        )
    )
    if record.get("app_name") != app_name.strip():
        raise RuntimeError("App deployment lease path binding is invalid")
    return record


def _record_path(app_name: str, record: dict[str, Any]) -> str:
    parent = str(record.get("parent_generation_id") or "")
    return _successor_path(app_name, parent) if parent else _path(app_name)


def _parse_timestamp(record: dict[str, Any], field: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(record[field]))
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"App deployment lease {field} is invalid") from exc
    if value.tzinfo is None:
        raise RuntimeError(f"App deployment lease {field} is invalid")
    return value.astimezone(UTC)


def _validate_transition(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> None:
    if (
        child["parent_generation_id"] != parent["generation_id"]
        or child["parent_digest"] != _record_digest(parent)
        or child["chain_id"] != parent["chain_id"]
        or int(child["generation_seq"]) != int(parent["generation_seq"]) + 1
        or int(child["key_epoch"]) < int(parent["key_epoch"])
        or int(child["version"]) < int(parent["version"])
        or (
            child["version"] != parent["version"]
            and child["operation"] not in {"acquire", "takeover"}
        )
    ):
        raise RuntimeError("App deployment lease successor lineage is invalid")
    stable = {
        "lease_id",
        "recovery_root_lease_id",
        "source_git_sha",
        "holder",
        "writer_application_id",
        "acquired_at",
    }
    operation = child["operation"]
    if operation in {"renew", "release"}:
        if parent["state"] != "active" or any(child[field] != parent[field] for field in stable):
            raise RuntimeError("App deployment lease same-owner transition is invalid")
        if parent.get("version") == LEASE_VERSION and (
            child.get("recovery_index_digest") != parent.get("recovery_index_digest")
            or child.get("holder_recovery_heads") != parent.get("holder_recovery_heads")
        ):
            raise RuntimeError("App deployment lease recovery index changed during heartbeat")
        if operation == "renew" and (
            child["state"] != "active"
            or _expires_at(child) < _expires_at(parent)
            or _parse_timestamp(child, "acl_attested_at")
            < _parse_timestamp(parent, "acl_attested_at")
        ):
            raise RuntimeError("App deployment lease renewal transition is invalid")
        if operation == "release" and (
            child["state"] != "released" or child["acl_attested_at"] != parent["acl_attested_at"]
        ):
            raise RuntimeError("App deployment lease release transition is invalid")
    elif operation == "takeover":
        if (
            parent["state"] != "active"
            or child["state"] != "active"
            or child["lease_id"] == parent["lease_id"]
            or child["holder"] != parent["holder"]
            or child["recovery_root_lease_id"] != parent["recovery_root_lease_id"]
            or child["writer_application_id"] != parent["writer_application_id"]
            or _parse_timestamp(child, "acquired_at") < _expires_at(parent)
        ):
            raise RuntimeError("App deployment lease takeover transition is invalid")
    elif operation == "acquire":
        if (
            parent["state"] != "released"
            or child["state"] != "active"
            or child["lease_id"] == parent["lease_id"]
            or _parse_timestamp(child, "acquired_at") < _parse_timestamp(parent, "released_at")
        ):
            raise RuntimeError("App deployment lease acquisition transition is invalid")
    else:  # pragma: no cover - record validation already closes this branch
        raise RuntimeError("App deployment lease transition operation is invalid")
    if operation in {"acquire", "takeover"} and child.get("version") == LEASE_VERSION:
        recovery_checkpoint.validate_transition(
            parent,
            child,
            lease_version=LEASE_VERSION,
        )


def _download(workspace: Any, *, app_name: str) -> dict[str, Any] | None:
    """Resolve the append-only signed generation chain to its unique head."""

    try:
        hint = _read_record(workspace, path=_head_path(app_name), app_name=app_name)
    except RuntimeError:
        hint = None
    record: dict[str, Any] | None
    if hint is not None:
        canonical = _read_record(
            workspace,
            path=_record_path(app_name, hint),
            app_name=app_name,
        )
        if canonical != hint:
            raise RuntimeError("App deployment lease head hint is not canonical")
        record = hint
        limit = MAX_SUCCESSORS_AFTER_HINT
    else:
        record = recovery_checkpoint.read_protocol_anchor(
            __import__(__name__, fromlist=["*"]),
            workspace,
            app_name=app_name,
        )
        from_locator = record is not None
        if not from_locator:
            record = _read_record(workspace, path=_path(app_name), app_name=app_name)
        limit = (
            MAX_CANONICAL_GENERATIONS
            if record is not None and record.get("version") != LEASE_VERSION
            else MAX_SUCCESSORS_AFTER_HINT
        )
    if record is None:
        return None
    if hint is None and not from_locator and record["parent_generation_id"]:
        raise RuntimeError("base App deployment lease unexpectedly names a parent")
    seen = {str(record["generation_id"])}
    for _ in range(limit):
        successor = _read_record(
            workspace,
            path=_successor_path(app_name, str(record["generation_id"])),
            app_name=app_name,
        )
        if successor is None:
            return record
        _validate_transition(record, successor)
        generation_id = str(successor["generation_id"])
        if generation_id in seen:
            raise RuntimeError("App deployment lease generation cycle is invalid")
        seen.add(generation_id)
        record = successor
    raise RuntimeError("App deployment lease generation chain exceeds its safety bound")


_expires_at = lease_support.expires_at


def _next_transition(
    parent: dict[str, Any],
    *,
    operation: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    return lease_support.next_transition(
        __import__(__name__, fromlist=["*"]),
        parent,
        operation=operation,
        changes=changes,
    )


def _update_head_hint(
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
) -> None:
    lease_support.update_head_hint(
        __import__(__name__, fromlist=["*"]),
        workspace,
        app_name=app_name,
        record=record,
    )


def _create_generation(
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
    publish_hint: bool = True,
) -> dict[str, Any]:
    parent = str(record.get("parent_generation_id") or "")
    path = _successor_path(app_name, parent) if parent else _path(app_name)
    signed = _sign(record)
    try:
        workspace.workspace.upload(
            path,
            io.BytesIO(json.dumps(signed, sort_keys=True).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except (AlreadyExists, ResourceAlreadyExists):
        raise RuntimeError("App deployment lease generation race was lost") from None
    except Exception as upload_error:
        try:
            persisted = _read_record(workspace, path=path, app_name=app_name)
        except Exception as read_error:
            raise RuntimeError(
                "App deployment lease generation upload failed and commit state "
                "could not be authenticated"
            ) from read_error
        if persisted != signed:
            raise RuntimeError(
                "App deployment lease generation upload failed without an exact commit"
            ) from upload_error
    if _read_record(workspace, path=path, app_name=app_name) != signed:
        raise RuntimeError("App deployment lease generation is not authoritative")
    if signed.get("version") == LEASE_VERSION and signed.get("operation") in {
        "acquire",
        "takeover",
    }:
        recovery_checkpoint.ensure_protocol_marker(
            __import__(__name__, fromlist=["*"]),
            workspace,
            app_name=app_name,
            anchor=signed,
        )
    if publish_hint:
        _update_head_hint(workspace, app_name=app_name, record=signed)
    return signed


def _release_successor(
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
    now: datetime,
) -> None:
    if record.get("version") == LEASE_VERSION:
        recovery_checkpoint.ensure_protocol_marker(
            __import__(__name__, fromlist=["*"]),
            workspace,
            app_name=app_name,
            anchor=record,
        )
    released_at = now.isoformat()
    _create_generation(
        workspace,
        app_name=app_name,
        record=_next_transition(
            record,
            operation="release",
            changes={
                "state": "released",
                "expires_at": released_at,
                "released_at": released_at,
            },
        ),
    )


def acquire(
    workspace: Any,
    *,
    app_name: str,
    source_git_sha: str,
    writer_application_id: str | None = None,
    expired_recovery_lease_id: str | None = None,
    now: datetime | None = None,
) -> str:
    app_name = app_name.strip()
    _path(app_name)
    source_git_sha = _source_sha(source_git_sha)
    current = now or _now()
    holder = _holder(workspace)
    writer = (
        writer_application_id or os.environ.get("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "")
    ).strip()
    if not writer or writer == holder:
        raise ValueError("App deployment lease requires a distinct writer application ID")
    existing = _download(workspace, app_name=app_name)
    legacy_lineage: list[dict[str, Any]] | None = None
    # A retained v2 base is a migration parent only while it remains the head.
    legacy_parent = lease_support.is_legacy_head(workspace, _path(app_name), existing)
    requested_recovery_root = (expired_recovery_lease_id or "").strip()
    if existing is not None and existing.get("state") == "active":
        if not requested_recovery_root:
            raise lease_support.held_error(
                __import__(__name__, fromlist=["*"]),
                existing,
                now=current,
            )
        if legacy_parent and current < _expires_at(existing) + LEGACY_TAKEOVER_GRACE:
            raise lease_support.held_error(
                __import__(__name__, fromlist=["*"]),
                existing,
                now=current,
            )
        recovery_root = lease_support.authorize_expired_for_acquire(
            __import__(__name__, fromlist=["*"]),
            workspace,
            existing=existing,
            writer_application_id=writer,
            recovery_lease_id=requested_recovery_root,
            now=current,
        )
    elif existing is not None:
        if existing.get("version") == LEASE_VERSION:
            authorized_root, _candidates = lease_support.recovery_context_for_record(
                __import__(__name__, fromlist=["*"]),
                workspace,
                app_name=app_name,
                record=existing,
            )
        else:
            authorized_root, _candidates, legacy_lineage = (
                recovery_checkpoint.legacy_recovery_context(
                    __import__(__name__, fromlist=["*"]),
                    workspace,
                    app_name=app_name,
                    record=existing,
                )
            )
        if requested_recovery_root and authorized_root != requested_recovery_root:
            raise RuntimeError("released App deployment lease does not accept this recovery root")
        recovery_root = requested_recovery_root or authorized_root
    else:
        recovery_root = ""
    lease_id = str(uuid4())
    generation_id = str(uuid4())
    operation = "takeover" if existing and existing.get("state") == "active" else "acquire"
    record: dict[str, Any] = {
        "version": LEASE_VERSION,
        "app_name": app_name,
        "state": "active",
        "operation": operation,
        "chain_id": str((existing or {}).get("chain_id") or uuid4()),
        "generation_id": generation_id,
        "generation_seq": (int(existing["generation_seq"]) + 1 if existing is not None else 0),
        "parent_generation_id": str((existing or {}).get("generation_id") or ""),
        "parent_digest": _record_digest(existing) if existing else "",
        "lease_id": lease_id,
        "recovery_root_lease_id": recovery_root or lease_id,
        "source_git_sha": source_git_sha,
        "holder": holder,
        "writer_application_id": writer,
        "acquired_at": current.isoformat(),
        "acl_attested_at": current.isoformat(),
        "expires_at": (current + LEASE_TTL).isoformat(),
        "key_epoch": len(_key_registry()) - 1,
    }
    # After the immutable read, create the checkpoint parent; mkdirs is
    # idempotent under contention and grants no shared ACL authority.
    workspace.workspace.mkdirs(LEASE_ROOT)
    recovery_index_digest, holder_recovery_heads = recovery_checkpoint.prepare_acquisition(
        __import__(__name__, fromlist=["*"]),
        workspace,
        app_name=app_name,
        parent=existing,
        record=record,
        legacy_lineage=legacy_lineage,
    )
    record.update(
        {
            "recovery_index_digest": recovery_index_digest,
            "holder_recovery_heads": holder_recovery_heads,
        }
    )

    # Every transition appends at the one deterministic generation path;
    # create-without-overwrite selects one winner under concurrent retries.
    try:
        candidate = _create_generation(
            workspace,
            app_name=app_name,
            record=record,
            publish_hint=not legacy_parent,
        )
        if legacy_parent:
            if _read_record(workspace, path=_path(app_name), app_name=app_name) != existing:
                raise RuntimeError("legacy App deployment lease changed during migration")
            _update_head_hint(workspace, app_name=app_name, record=candidate)
        _ensure_protected_root(
            workspace,
            holder=holder,
            writer_application_id=writer,
        )
        persisted = _download(workspace, app_name=app_name)
        if persisted != _verify(_sign(record)):
            raise RuntimeError("App deployment lease did not persist exactly")
    except Exception:
        persisted = _download(workspace, app_name=app_name)
        if persisted is None or persisted.get("lease_id") != lease_id:
            raise
        try:
            lease_support.compensate_owned_failed_acquisition(
                __import__(__name__, fromlist=["*"]),
                workspace,
                app_name=app_name,
                record=persisted,
                holder=holder,
                writer_application_id=writer,
                now=current,
            )
        except Exception as compensation_error:
            raise RuntimeError(
                "App deployment lease acquisition failed and signed compensation did not complete"
            ) from compensation_error
        raise
    return lease_id


def assert_held(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    record = _download(workspace, app_name=app_name)
    if record is None:
        raise RuntimeError("App deployment lease disappeared while deployment was active")
    current = now or _now()
    actor = _holder(workspace)
    holder = str(record.get("holder") or "").strip()
    writer = str(record.get("writer_application_id") or "").strip()
    if record.get("state") != "active":
        raise RuntimeError("App deployment lease was released")
    if actor not in {holder, writer}:
        raise RuntimeError("App deployment lease actor is not its holder or delegated writer")
    if actor == holder:
        _assert_protected_root(
            workspace,
            holder=holder,
            writer_application_id=writer,
            object_id=_root_object_id(workspace),
        )
    else:
        lease_support.assert_recent_writer_acl_attestation(
            __import__(__name__, fromlist=["*"]), record, now=current
        )
    authoritative = _download(workspace, app_name=app_name)
    if authoritative is None:
        raise RuntimeError("App deployment lease disappeared during validation")
    if authoritative != record and not lease_support.same_lease_after_renewal(
        __import__(__name__, fromlist=["*"]), record, authoritative
    ):
        raise RuntimeError("App deployment lease changed during validation")
    record = authoritative
    holder = str(record.get("holder") or "").strip()
    writer = str(record.get("writer_application_id") or "").strip()
    if record.get("state") != "active":
        raise RuntimeError("App deployment lease changed during validation")
    if actor not in {holder, writer}:
        raise RuntimeError("App deployment lease actor changed during validation")
    if record.get("lease_id") != lease_id or record.get("source_git_sha") != source_git_sha:
        raise RuntimeError("App deployment lease ownership or source changed")
    final_now = now or _now()
    if actor == writer:
        lease_support.assert_recent_writer_acl_attestation(
            __import__(__name__, fromlist=["*"]), record, now=final_now
        )
    if _expires_at(record) <= final_now:
        raise RuntimeError("App deployment lease expired while deployment was active")
    return record


def held_assertion(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
) -> Callable[[], None]:
    """Bind exact lease evidence into a reusable fail-closed assertion."""
    from tools.databricks.app_deployment_lease_cli import held_assertion as build

    return build(
        __import__(__name__, fromlist=["*"]),
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )


def renew(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> None:
    current = now or _now()
    actor = _holder(workspace)
    for _ in range(4):
        record = assert_held(
            workspace,
            app_name=app_name,
            lease_id=lease_id,
            source_git_sha=source_git_sha,
            now=current,
        )
        if record.get("holder") != actor:
            raise RuntimeError("Only the App deployment lease holder may renew its ACL attestation")
        absolute_expiry = _parse_timestamp(record, "acquired_at") + MAX_ACTIVE_LEASE_LIFETIME
        if current >= absolute_expiry:
            raise RuntimeError("App deployment lease reached its maximum active lifetime")
        requested_expiry = min(current + LEASE_TTL, absolute_expiry)
        if _parse_timestamp(record, "acl_attested_at") >= current:
            return
        try:
            _create_generation(
                workspace,
                app_name=app_name,
                record=_next_transition(
                    record,
                    operation="renew",
                    changes={
                        "expires_at": max(_expires_at(record), requested_expiry).isoformat(),
                        "acl_attested_at": current.isoformat(),
                    },
                ),
            )
            return
        except RuntimeError as exc:
            if str(exc) != "App deployment lease generation race was lost":
                raise
    raise RuntimeError("App deployment lease renewal did not converge")


def release(workspace: Any, *, app_name: str, lease_id: str) -> None:
    holder = _holder(workspace)
    for _ in range(4):
        record = _download(workspace, app_name=app_name)
        if record is None:
            raise RuntimeError("App deployment lease disappeared before release")
        if record.get("lease_id") != lease_id or record.get("holder") != holder:
            raise RuntimeError("App deployment lease ownership changed before release")
        if record.get("state") == "released":
            return
        try:
            _release_successor(
                workspace,
                app_name=app_name,
                record=record,
                now=_now(),
            )
            return
        except RuntimeError as exc:
            if str(exc) != "App deployment lease generation race was lost":
                raise
    raise RuntimeError("App deployment lease release did not converge")


def _heartbeat(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    parent_pid: int,
) -> None:
    from tools.databricks.app_deployment_lease_cli import heartbeat

    heartbeat(
        __import__(__name__, fromlist=["*"]),
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        parent_pid=parent_pid,
    )


def main(argv: list[str] | None = None) -> int:
    from tools.databricks.app_deployment_lease_cli import main as cli_main

    return cli_main(__import__(__name__, fromlist=["*"]), argv)


if __name__ == "__main__":
    raise SystemExit(main())
