"""Non-authoritative storage hints and public-key registry helpers."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks import app_deployment_lease_recovery_checkpoint as recovery_checkpoint

LEGACY_V2_FIELDS = {
    "version",
    "app_name",
    "lease_id",
    "source_git_sha",
    "holder",
    "writer_application_id",
    "acquired_at",
    "expires_at",
    "attestation_verify_key",
    "attestation_signature",
}
V4_BASE_FIELDS = {
    "version",
    "app_name",
    "state",
    "operation",
    "chain_id",
    "generation_id",
    "generation_seq",
    "parent_generation_id",
    "parent_digest",
    "lease_id",
    "recovery_root_lease_id",
    "source_git_sha",
    "holder",
    "writer_application_id",
    "acquired_at",
    "acl_attested_at",
    "expires_at",
    "key_epoch",
    "attestation_verify_key",
    "attestation_signature",
}
V5_BASE_FIELDS = V4_BASE_FIELDS | {
    "recovery_index_digest",
    "holder_recovery_heads",
}


def is_exact_integer(value: object, expected: int) -> bool:
    """Reject JSON booleans even though Python bool subclasses int."""

    return type(value) is int and value == expected


def is_exact_integer_member(value: object, expected: set[int]) -> bool:
    """Match an exact JSON integer against a closed protocol-version set."""

    return type(value) is int and value in expected


def metadata_is_exact(value: dict[str, Any], versions: set[int], epoch: int) -> bool:
    return is_exact_integer_member(value.get("version"), versions) and is_exact_integer(
        value.get("key_epoch"), epoch
    )


def json_without_duplicate_keys(raw: bytes, *, artifact: str) -> object:
    """Decode JSON without changing legacy whitespace compatibility."""

    def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RuntimeError(f"{artifact} contains duplicate JSON keys")
            value[key] = item
        return value

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{artifact} is not valid JSON") from exc


def expires_at(record: dict[str, Any]) -> datetime:
    try:
        expires = datetime.fromisoformat(str(record["expires_at"]))
    except (KeyError, ValueError) as exc:
        raise RuntimeError("App deployment lease expiration is invalid") from exc
    if expires.tzinfo is None:
        raise RuntimeError("App deployment lease expiration is invalid")
    return expires.astimezone(UTC)


def key_registry() -> list[str]:
    historical = os.environ.get("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", "").split(",")
    previous = os.environ.get("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", "")
    current = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "")
    ordered: list[str] = []
    for candidate in (*historical, previous, current):
        normalized = candidate.strip()
        if not normalized or normalized in ordered:
            continue
        try:
            decoded = base64.urlsafe_b64decode(normalized + "=" * (-len(normalized) % 4))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("App deployment lease registry key is invalid") from exc
        if len(decoded) != 32:
            raise RuntimeError("App deployment lease registry key is invalid")
        ordered.append(normalized)
    if not current.strip() or not ordered or ordered[-1] != current.strip():
        raise RuntimeError("App deployment lease key registry does not end at current key")
    return ordered


def verify_legacy_v2(lease: Any, record: dict[str, Any]) -> dict[str, Any]:
    if set(record) != LEGACY_V2_FIELDS:
        raise RuntimeError("legacy App deployment lease is incomplete")
    if not is_exact_integer(record.get("version"), 2):
        raise RuntimeError("legacy App deployment lease version is invalid")
    registry = key_registry()
    verify = str(record.get("attestation_verify_key") or "").strip()
    if verify not in registry:
        raise RuntimeError("legacy App deployment lease attestation identity is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(lease._decode(verify, length=32)).verify(
            lease._decode(str(record.get("attestation_signature") or ""), length=64),
            lease._message(record),
        )
        lease_id = str(UUID(str(record.get("lease_id") or "")))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("legacy App deployment lease signature is invalid") from exc
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    fingerprint = __import__("hashlib").sha256(canonical.encode()).hexdigest()
    return {
        "version": lease.LEGACY_LEASE_VERSION,
        "app_name": str(record["app_name"]),
        "state": "active",
        "operation": "acquire",
        "chain_id": str(uuid5(NAMESPACE_URL, f"mip-lease-v2:{record['app_name']}")),
        "generation_id": str(uuid5(NAMESPACE_URL, f"mip-lease-v2:{fingerprint}")),
        "generation_seq": 0,
        "parent_generation_id": "",
        "parent_digest": "",
        "lease_id": lease_id,
        "recovery_root_lease_id": lease_id,
        "source_git_sha": str(record["source_git_sha"]),
        "holder": str(record["holder"]),
        "writer_application_id": str(record["writer_application_id"]),
        "acquired_at": str(record["acquired_at"]),
        "acl_attested_at": str(record["acquired_at"]),
        "expires_at": str(record["expires_at"]),
        "key_epoch": registry.index(verify),
        "attestation_verify_key": verify,
        "attestation_signature": str(record["attestation_signature"]),
    }


def legacy_v2_at_path(workspace: Any, path: str) -> bool:
    try:
        stream = workspace.workspace.download(path)
    except (NotFound, ResourceDoesNotExist):
        return False
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("App deployment lease is not valid JSON") from exc
    return isinstance(value, dict) and is_exact_integer(value.get("version"), 2)


def is_legacy_head(workspace: Any, path: str, existing: dict[str, Any] | None) -> bool:
    """Return whether the retained v2 base is still the authoritative head."""

    return bool(
        existing is not None
        and int(existing.get("generation_seq") or 0) == 0
        and not str(existing.get("parent_generation_id") or "")
        and legacy_v2_at_path(workspace, path)
    )


def recovery_context(lease: Any, workspace: Any, *, app_name: str) -> tuple[str, list[str]]:
    """Return the durable root and same-authority lease lineage, newest first."""

    record = lease._download(workspace, app_name=app_name)
    if record is None:
        return "", []
    return recovery_context_for_record(
        lease,
        workspace,
        app_name=app_name,
        record=record,
    )


def recovery_context_for_record(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
) -> tuple[str, list[str]]:
    """Resolve recovery authority bound to one exact canonical observation."""

    if record.get("version") == lease.LEASE_VERSION:
        return recovery_checkpoint.recovery_context(
            lease,
            workspace,
            app_name=app_name,
            record=record,
        )
    holder = lease._holder(workspace)
    if record.get("state") == "active" and record.get("holder") != holder:
        raise RuntimeError("App deployment lease recovery actor is not its holder")
    current = lease._read_record(
        workspace,
        path=lease._path(app_name),
        app_name=app_name,
    )
    if current is None:
        raise RuntimeError("App deployment lease recovery lineage has no signed base")
    lineage: list[dict[str, Any]] = []
    for _ in range(lease.MAX_CANONICAL_GENERATIONS):
        lineage.append(current)
        successor = lease._read_record(
            workspace,
            path=lease._successor_path(app_name, str(current["generation_id"])),
            app_name=app_name,
        )
        if successor is None:
            break
        lease._validate_transition(current, successor)
        current = successor
    else:
        raise RuntimeError("App deployment lease recovery lineage exceeds its safety bound")
    if current != record:
        raise RuntimeError("App deployment lease recovery lineage does not reach its head")
    selected = next(
        (ancestor for ancestor in reversed(lineage) if ancestor.get("holder") == holder),
        None,
    )
    if selected is None:
        return "", []
    recovery = str(selected.get("recovery_root_lease_id") or "").strip()
    try:
        recovery = str(UUID(recovery))
    except ValueError as exc:
        raise RuntimeError("App deployment lease recovery root is invalid") from exc
    candidates: list[str] = []
    for ancestor in reversed(lineage):
        if ancestor.get("recovery_root_lease_id") != recovery or ancestor.get("holder") != holder:
            continue
        candidate = str(ancestor.get("lease_id") or "")
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    if not candidates or recovery not in candidates:
        raise RuntimeError("App deployment lease recovery lineage is incomplete")
    return recovery, candidates


def recovery_root(lease: Any, workspace: Any, *, app_name: str) -> str:
    """Return durable takeover authority only to the signed lease holder."""

    recovery, _candidates = recovery_context(lease, workspace, app_name=app_name)
    return recovery


def scan_canonical_head(lease: Any, workspace: Any, *, app_name: str) -> dict[str, Any]:
    """Explicitly authenticate the complete immutable generation chain."""

    current = lease._read_record(
        workspace,
        path=lease._path(app_name),
        app_name=app_name,
    )
    if current is None:
        raise RuntimeError("App deployment lease repair found no signed base")
    seen = {str(current["generation_id"])}
    for _ in range(lease.MAX_CANONICAL_GENERATIONS):
        successor = lease._read_record(
            workspace,
            path=lease._successor_path(app_name, str(current["generation_id"])),
            app_name=app_name,
        )
        if successor is None:
            return current
        lease._validate_transition(current, successor)
        generation_id = str(successor["generation_id"])
        if generation_id in seen:
            raise RuntimeError("App deployment lease generation cycle is invalid")
        seen.add(generation_id)
        current = successor
    raise RuntimeError("App deployment lease repair exceeded its safety bound")


def repair_protocol_marker(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    anchor: dict[str, Any],
) -> None:
    """Restore a missing/corrupt locator after full-scan authorization."""

    try:
        existing = recovery_checkpoint.read_protocol_anchor(
            lease,
            workspace,
            app_name=app_name,
        )
    except RuntimeError:
        existing = None
    if existing is not None:
        if existing["chain_id"] != anchor["chain_id"]:
            raise RuntimeError("App deployment lease v5 locator chain changed")
        return
    if (
        anchor.get("version") != lease.LEASE_VERSION
        or lease._read_record(
            workspace,
            path=lease._record_path(app_name, anchor),
            app_name=app_name,
        )
        != anchor
    ):
        raise RuntimeError("App deployment lease repair anchor is not canonical")
    value = {
        "version": recovery_checkpoint.PROTOCOL_MARKER_VERSION,
        "app_name": app_name,
        "chain_id": anchor["chain_id"],
        "anchor_generation_id": anchor["generation_id"],
        "anchor_record_digest": lease._record_digest(anchor),
        "anchor_record": anchor,
    }
    payload = recovery_checkpoint._canonical(value)
    if len(payload) > recovery_checkpoint.MAX_PROTOCOL_MARKER_BYTES:
        raise RuntimeError("App deployment lease v5 locator is oversized")
    workspace.workspace.upload(
        recovery_checkpoint._protocol_marker_path(lease, app_name),
        io.BytesIO(payload),
        format=ImportFormat.AUTO,
        overwrite=True,
    )
    if (
        recovery_checkpoint.read_protocol_anchor(
            lease,
            workspace,
            app_name=app_name,
        )
        != anchor
    ):
        raise RuntimeError("App deployment lease repaired v5 locator failed exact postflight")


def repair_head_hint(lease: Any, workspace: Any, *, app_name: str) -> dict[str, Any]:
    """Repair bounded v5 locator state after a full authenticated scan."""

    head = scan_canonical_head(lease, workspace, app_name=app_name)
    if is_legacy_head(workspace, lease._path(app_name), head):
        raise RuntimeError("Legacy v2 App deployment lease must migrate before head repair")
    holder = str(head.get("holder") or "").strip()
    writer = str(head.get("writer_application_id") or "").strip()
    if lease._holder(workspace) != holder:
        raise RuntimeError("Only the signed App deployment lease holder may repair its head")
    lease._assert_protected_root(
        workspace,
        holder=holder,
        writer_application_id=writer,
        object_id=lease._root_object_id(workspace),
    )
    if head.get("version") == lease.LEASE_VERSION:
        repair_protocol_marker(
            lease,
            workspace,
            app_name=app_name,
            anchor=head,
        )
    workspace.workspace.upload(
        lease._head_path(app_name),
        io.BytesIO(json.dumps(head, sort_keys=True).encode("utf-8")),
        format=ImportFormat.AUTO,
        overwrite=True,
    )
    persisted = lease._read_record(
        workspace,
        path=lease._head_path(app_name),
        app_name=app_name,
    )
    if persisted != head or lease._download(workspace, app_name=app_name) != head:
        raise RuntimeError("App deployment lease repaired head failed exact postflight")
    return head


def compensate_owned_failed_acquisition(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
    holder: str,
    writer_application_id: str,
    now: datetime,
) -> None:
    """Protect an exactly owned durable fence before signed compensation."""

    lease._ensure_protected_root(
        workspace,
        holder=holder,
        writer_application_id=writer_application_id,
    )
    lease._release_successor(
        workspace,
        app_name=app_name,
        record=record,
        now=now,
    )


def held_error(
    lease: Any,
    existing: dict[str, Any] | None,
    *,
    now: Any,
) -> RuntimeError:
    owner = str((existing or {}).get("holder") or "unknown")
    suffix = (
        " (expired but never auto-replaced)"
        if existing and lease._expires_at(existing) <= now
        else ""
    )
    return RuntimeError(
        f"App deployment lease is already held by {owner}{suffix}; wait for release"
    )


def authorize_expired_for_acquire(
    lease: Any,
    workspace: Any,
    *,
    existing: dict[str, Any],
    writer_application_id: str,
    recovery_lease_id: str,
    now: Any,
) -> str:
    """Authorize one expired signed fence for an atomic successor race."""

    if existing.get("state") != "active":
        raise RuntimeError("only an active App deployment lease can expire")
    if lease._expires_at(existing) > now:
        raise held_error(lease, existing, now=now)
    if existing.get("version") == lease.LEASE_VERSION:
        checkpoint_root, _candidates = recovery_context_for_record(
            lease,
            workspace,
            app_name=str(existing["app_name"]),
            record=existing,
        )
        if checkpoint_root != recovery_lease_id:
            raise RuntimeError(
                "expired App deployment lease is not authorized by its durable recovery root"
            )
    holder = str(existing.get("holder") or "").strip()
    writer = str(existing.get("writer_application_id") or "").strip()
    if (
        not holder
        or lease._holder(workspace) != holder
        or writer != writer_application_id
        or str(existing.get("recovery_root_lease_id") or "") != recovery_lease_id
    ):
        raise RuntimeError(
            "expired App deployment lease is not authorized by its durable recovery root"
        )
    return str(existing["recovery_root_lease_id"])


def assert_recent_writer_acl_attestation(
    lease: Any,
    record: dict[str, Any],
    *,
    now: Any,
) -> None:
    """Bound delegated-writer reliance on the holder's last exact ACL check."""

    attested_at = lease._parse_timestamp(record, "acl_attested_at")
    if attested_at > now or now - attested_at > lease.WRITER_ACL_ATTESTATION_MAX_AGE:
        raise RuntimeError("App deployment lease deployer ACL attestation is stale")


def validate_v4_timestamps(lease: Any, record: dict[str, Any]) -> None:
    """Validate bounded signed lifetime and explicit ACL-attestation time."""

    acquired = lease._parse_timestamp(record, "acquired_at")
    attested = lease._parse_timestamp(record, "acl_attested_at")
    expires = lease._parse_timestamp(record, "expires_at")
    active_limit = acquired + lease.MAX_ACTIVE_LEASE_LIFETIME
    active_valid = acquired <= attested <= expires <= active_limit
    released_valid = acquired <= attested <= active_limit and expires >= attested
    if not (active_valid if record.get("state") == "active" else released_valid):
        raise RuntimeError("App deployment lease timestamp contract is invalid")


def record_digest(lease: Any, record: dict[str, Any]) -> str:
    """Hash the exact signed shape, including pre-ACL-field v4 parents."""

    signed_shape = dict(record)
    if "acl_attested_at" in signed_shape:
        legacy_shape = dict(signed_shape)
        legacy_shape.pop("acl_attested_at")
        try:
            verify = str(record.get("attestation_verify_key") or "")
            signature = str(record.get("attestation_signature") or "")
            Ed25519PublicKey.from_public_bytes(lease._decode(verify, length=32)).verify(
                lease._decode(signature, length=64), lease._message(legacy_shape)
            )
            signed_shape = legacy_shape
        except (InvalidSignature, RuntimeError, ValueError):
            pass
    canonical = json.dumps(signed_shape, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def update_head_hint(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
) -> None:
    try:
        workspace.workspace.upload(
            lease._head_path(app_name),
            io.BytesIO(json.dumps(record, sort_keys=True).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=True,
        )
    except Exception:
        # The hint never authorizes a transition. Missing/stale state falls
        # back to the immutable chain and the next transition republishes it.
        return


def next_transition(
    lease: Any,
    parent: dict[str, Any],
    *,
    operation: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    unsigned = {
        key: value
        for key, value in parent.items()
        if key not in {"attestation_verify_key", "attestation_signature", "released_at"}
    }
    return {
        **unsigned,
        **changes,
        "operation": operation,
        "generation_id": str(uuid4()),
        "generation_seq": int(parent["generation_seq"]) + 1,
        "parent_generation_id": str(parent["generation_id"]),
        "parent_digest": lease._record_digest(parent),
        "key_epoch": len(lease._key_registry()) - 1,
    }


def same_lease_after_renewal(
    lease: Any,
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    mutable = {
        "operation",
        "generation_id",
        "generation_seq",
        "parent_generation_id",
        "parent_digest",
        "expires_at",
        "acl_attested_at",
        "key_epoch",
        "attestation_verify_key",
        "attestation_signature",
    }
    return not any(before.get(key) != after.get(key) for key in set(before) - mutable) and (
        after.get("state") == "active"
        and after.get("parent_generation_id") == before.get("generation_id")
        and lease._expires_at(after) >= lease._expires_at(before)
    )
