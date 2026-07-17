#!/usr/bin/env python3
"""Acquire/release one signed workspace lease for a governed App deployment."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shlex
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from databricks.sdk import WorkspaceClient
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

LEASE_VERSION = 1
LEASE_TTL = timedelta(hours=4)
LEASE_ROOT = "/Shared/.mip-deployment-leases"
HEARTBEAT_INTERVAL_SECONDS = 60


def _path(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in normalized
    ):
        raise ValueError("App deployment lease name is invalid")
    return f"{LEASE_ROOT}/{normalized}.json"


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


def _assert_protected_root(workspace: Any, *, holder: str, object_id: str) -> None:
    permissions = workspace.workspace.get_permissions("directories", object_id)
    holder_manage = False
    for entry in _items(_field(permissions, "access_control_list")):
        levels = _permission_levels(entry)
        user_name = str(_field(entry, "user_name") or "").strip()
        group_name = str(_field(entry, "group_name") or "").strip().casefold()
        if user_name == holder and "CAN_MANAGE" in levels:
            holder_manage = True
        if "CAN_MANAGE" in levels and user_name != holder and group_name != "admins":
            raise RuntimeError("App deployment lease directory has an unexpected manager")
    if not holder_manage:
        raise RuntimeError("App deployment lease directory ACL did not converge")


def _ensure_protected_root(workspace: Any, *, holder: str) -> None:
    workspace.workspace.mkdirs(LEASE_ROOT)
    object_id = _root_object_id(workspace)
    workspace.workspace.set_permissions(
        "directories",
        object_id,
        access_control_list=[
            WorkspaceObjectAccessControlRequest(
                user_name=holder,
                permission_level=WorkspaceObjectPermissionLevel.CAN_MANAGE,
            )
        ],
    )
    _assert_protected_root(workspace, holder=holder, object_id=object_id)


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


def _message(record: dict[str, str | int]) -> bytes:
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


def _sign(record: dict[str, str | int]) -> dict[str, str | int]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing, length=32))
    derived = _encode(private.public_key().public_bytes_raw())
    if derived != verify:
        raise RuntimeError("App deployment lease signing and verification keys do not match")
    return {
        **record,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(record))),
    }


def _verify(record: object) -> dict[str, str | int]:
    if not isinstance(record, dict) or record.get("version") != LEASE_VERSION:
        raise RuntimeError("App deployment lease is invalid")
    current = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    previous = os.environ.get("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", "").strip()
    verify = str(record.get("attestation_verify_key") or "").strip()
    if verify not in {current, previous} - {""}:
        raise RuntimeError("App deployment lease attestation identity is invalid")
    normalized = {str(key): value for key, value in record.items()}
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(verify, length=32))
        signature = _decode(str(record.get("attestation_signature") or ""), length=64)
        public.verify(signature, _message(normalized))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("App deployment lease signature is invalid") from exc
    required = {
        "version",
        "app_name",
        "lease_id",
        "source_git_sha",
        "holder",
        "acquired_at",
        "expires_at",
        "attestation_verify_key",
        "attestation_signature",
    }
    if set(normalized) != required:
        raise RuntimeError("App deployment lease is incomplete")
    return normalized


def _download(workspace: Any, *, app_name: str) -> dict[str, str | int] | None:
    try:
        stream = workspace.workspace.download(_path(app_name))
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        return _verify(json.loads(stream.read().decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("App deployment lease is not valid JSON") from exc


def _expired(record: dict[str, str | int], *, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(str(record["expires_at"]))
    except (KeyError, ValueError) as exc:
        raise RuntimeError("App deployment lease expiration is invalid") from exc
    return expires <= now


def _held_error(
    existing: dict[str, str | int] | None,
    *,
    now: datetime,
) -> RuntimeError:
    owner = str((existing or {}).get("holder") or "unknown")
    suffix = (
        " (expired but never auto-replaced)"
        if existing and _expired(existing, now=now)
        else ""
    )
    return RuntimeError(f"App deployment lease is already held by {owner}{suffix}; wait for release")


def _delete_exact_record(
    workspace: Any,
    *,
    app_name: str,
    expected: dict[str, str | int],
) -> None:
    persisted = _download(workspace, app_name=app_name)
    if persisted != expected:
        raise RuntimeError("App deployment lease changed before exact deletion")
    try:
        workspace.workspace.delete(_path(app_name))
    except Exception as delete_error:
        try:
            after_error = _download(workspace, app_name=app_name)
        except Exception as read_error:
            raise RuntimeError(
                "App deployment lease state could not be authenticated after ambiguous deletion"
            ) from read_error
        if after_error is None:
            return
        if after_error != expected:
            raise RuntimeError(
                "App deployment lease changed during ambiguous exact deletion; "
                "refusing retry"
            ) from delete_error
        raise RuntimeError(
            "App deployment lease remained after ambiguous exact deletion; refusing retry"
        ) from delete_error
    remaining = _download(workspace, app_name=app_name)
    if remaining is None:
        return
    if remaining != expected:
        raise RuntimeError("App deployment lease changed during exact deletion")
    raise RuntimeError("App deployment lease remained after exact deletion")


def acquire(
    workspace: Any,
    *,
    app_name: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> str:
    if len(source_git_sha.strip()) != 40:
        raise ValueError("App deployment lease requires an exact source SHA")
    current = now or datetime.now(UTC)
    holder = _holder(workspace)
    record: dict[str, str | int] = {
        "version": LEASE_VERSION,
        "app_name": app_name,
        "lease_id": str(uuid4()),
        "source_git_sha": source_git_sha,
        "holder": holder,
        "acquired_at": current.isoformat(),
        "expires_at": (current + LEASE_TTL).isoformat(),
    }
    signed = _sign(record)
    path = _path(app_name)

    # A contender must inspect the immutable lease before touching the shared
    # directory ACL.  The non-overwriting upload is the acquisition fence; only
    # its winner may converge the directory ACL for its holder.
    existing = _download(workspace, app_name=app_name)
    if existing is not None:
        raise _held_error(existing, now=current)
    workspace.workspace.mkdirs(LEASE_ROOT)
    try:
        workspace.workspace.upload(
            path,
            io.BytesIO(json.dumps(signed, sort_keys=True).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except (AlreadyExists, ResourceAlreadyExists):
        existing = _download(workspace, app_name=app_name)
        raise _held_error(existing, now=current) from None
    except Exception as upload_error:
        try:
            persisted = _download(workspace, app_name=app_name)
        except Exception as read_error:
            raise RuntimeError(
                "App deployment lease upload failed and persisted state could not be authenticated"
            ) from read_error
        if persisted is None:
            raise
        if persisted != signed:
            raise RuntimeError(
                "App deployment lease upload failed with a different record present; "
                "refusing compensation"
            ) from upload_error
        try:
            _delete_exact_record(
                workspace,
                app_name=app_name,
                expected=signed,
            )
        except Exception as compensation_error:
            raise RuntimeError(
                "App deployment lease upload failed after commit and exact compensation "
                "did not complete"
            ) from compensation_error
        raise
    try:
        _ensure_protected_root(workspace, holder=holder)
        persisted = _download(workspace, app_name=app_name)
        if persisted != signed:
            raise RuntimeError("App deployment lease did not persist exactly")
    except Exception:
        try:
            _delete_exact_record(
                workspace,
                app_name=app_name,
                expected=signed,
            )
        except Exception as compensation_error:
            raise RuntimeError(
                "App deployment lease acquisition failed and exact compensation did not complete"
            ) from compensation_error
        raise
    return str(record["lease_id"])


def assert_held(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> dict[str, str | int]:
    holder = _holder(workspace)
    _assert_protected_root(
        workspace,
        holder=holder,
        object_id=_root_object_id(workspace),
    )
    record = _download(workspace, app_name=app_name)
    if record is None:
        raise RuntimeError("App deployment lease disappeared while deployment was active")
    if (
        record.get("lease_id") != lease_id
        or record.get("source_git_sha") != source_git_sha
        or record.get("holder") != holder
    ):
        raise RuntimeError("App deployment lease ownership or source changed")
    if _expired(record, now=now or datetime.now(UTC)):
        raise RuntimeError("App deployment lease expired while deployment was active")
    return record


def renew(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(UTC)
    record = assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=current,
    )
    refreshed = _sign({**record, "expires_at": (current + LEASE_TTL).isoformat()})
    # Workspace Files exposes no conditional If-Match upload.  Re-read the
    # complete signed record immediately before the overwrite so any change
    # since assert_held fails closed instead of being knowingly clobbered.
    if _download(workspace, app_name=app_name) != record:
        raise RuntimeError("App deployment lease changed immediately before renewal")
    workspace.workspace.upload(
        _path(app_name),
        io.BytesIO(json.dumps(refreshed, sort_keys=True).encode("utf-8")),
        format=ImportFormat.AUTO,
        overwrite=True,
    )
    if _download(workspace, app_name=app_name) != refreshed:
        raise RuntimeError("App deployment lease renewal did not persist exactly")


def release(workspace: Any, *, app_name: str, lease_id: str) -> None:
    record = _download(workspace, app_name=app_name)
    if record is None:
        raise RuntimeError("App deployment lease disappeared before release")
    holder = _holder(workspace)
    if record.get("lease_id") != lease_id or record.get("holder") != holder:
        raise RuntimeError("App deployment lease ownership changed before release")
    _delete_exact_record(
        workspace,
        app_name=app_name,
        expected=record,
    )


def _parent_is_expected(parent_pid: int) -> bool:
    """Return whether the heartbeat remains a child of the original deployer."""

    # Parent-child identity is stronger than kill(pid, 0): after parent death
    # this process is re-parented, so a later unrelated reuse of parent_pid
    # cannot be mistaken for the deployer that launched this heartbeat.
    return os.getppid() == parent_pid


def _heartbeat(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    parent_pid: int,
) -> None:
    while _parent_is_expected(parent_pid):
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)
        if not _parent_is_expected(parent_pid):
            return
        try:
            renew(
                workspace,
                app_name=app_name,
                lease_id=lease_id,
                source_git_sha=source_git_sha,
            )
        except Exception as exc:
            print(
                f"[mip-deployment-lease] heartbeat failed: {type(exc).__name__}",
                file=sys.stderr,
            )
            if _parent_is_expected(parent_pid):
                os.kill(parent_pid, signal.SIGTERM)
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("acquire", "heartbeat", "release"))
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--source-git-sha")
    parser.add_argument("--lease-id")
    parser.add_argument("--out-env", type=Path)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    workspace = WorkspaceClient()
    if args.action == "acquire":
        if not args.source_git_sha or args.out_env is None:
            parser.error("acquire requires --source-git-sha and --out-env")
        lease_id = acquire(
            workspace,
            app_name=args.app_name,
            source_git_sha=args.source_git_sha,
        )
        try:
            args.out_env.write_text(
                f"MIP_APP_DEPLOYMENT_LEASE_ID={shlex.quote(lease_id)}\n",
                encoding="utf-8",
            )
        except Exception:
            try:
                release(
                    workspace,
                    app_name=args.app_name,
                    lease_id=lease_id,
                )
                if _download(workspace, app_name=args.app_name) is not None:
                    raise RuntimeError("released handoff lease remained persisted")
            except Exception as compensation_error:
                raise RuntimeError(
                    "App deployment lease environment handoff failed and exact compensation "
                    "did not complete"
                ) from compensation_error
            raise
    elif args.action == "heartbeat":
        if not args.lease_id or not args.source_git_sha or not args.parent_pid:
            parser.error("heartbeat requires --lease-id, --source-git-sha, and --parent-pid")
        _heartbeat(
            workspace,
            app_name=args.app_name,
            lease_id=args.lease_id,
            source_git_sha=args.source_git_sha,
            parent_pid=args.parent_pid,
        )
    else:
        if not args.lease_id:
            parser.error("release requires --lease-id")
        release(workspace, app_name=args.app_name, lease_id=args.lease_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
