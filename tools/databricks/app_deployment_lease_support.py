"""Non-authoritative storage hints and public-key registry helpers."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat

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


def key_registry() -> list[str]:
    historical = os.environ.get(
        "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", ""
    ).split(",")
    previous = os.environ.get("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", "")
    current = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "")
    ordered: list[str] = []
    for candidate in (*historical, previous, current):
        normalized = candidate.strip()
        if not normalized or normalized in ordered:
            continue
        try:
            decoded = base64.urlsafe_b64decode(
                normalized + "=" * (-len(normalized) % 4)
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("App deployment lease registry key is invalid") from exc
        if len(decoded) != 32:
            raise RuntimeError("App deployment lease registry key is invalid")
        ordered.append(normalized)
    if not current.strip() or not ordered or ordered[-1] != current.strip():
        raise RuntimeError("App deployment lease key registry does not end at current key")
    return ordered


def verify_legacy_v2(lease: Any, record: dict[str, Any]) -> dict[str, str | int]:
    if set(record) != LEGACY_V2_FIELDS:
        raise RuntimeError("legacy App deployment lease is incomplete")
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
        "version": lease.LEASE_VERSION,
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
    return isinstance(value, dict) and value.get("version") == 2


def is_legacy_head(
    workspace: Any, path: str, existing: dict[str, str | int] | None
) -> bool:
    """Return whether the retained v2 base is still the authoritative head."""

    return bool(
        existing is not None
        and int(existing.get("generation_seq") or 0) == 0
        and not str(existing.get("parent_generation_id") or "")
        and legacy_v2_at_path(workspace, path)
    )


def recovery_context(
    lease: Any, workspace: Any, *, app_name: str
) -> tuple[str, list[str]]:
    """Return the durable root and same-authority lease lineage, newest first."""

    record = lease._download(workspace, app_name=app_name)
    if record is None:
        return "", []
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
    lineage: list[dict[str, str | int]] = []
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
        (
            ancestor
            for ancestor in reversed(lineage)
            if ancestor.get("holder") == holder
        ),
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
        if (
            ancestor.get("recovery_root_lease_id") != recovery
            or ancestor.get("holder") != holder
        ):
            continue
        candidate = str(ancestor.get("lease_id") or "")
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    if not candidates or recovery not in candidates:
        raise RuntimeError("App deployment lease recovery lineage is incomplete")
    return recovery, candidates


def recovery_root(lease: Any, workspace: Any, *, app_name: str) -> str:
    """Return durable takeover authority only to the signed lease holder."""

    recovery, _candidates = recovery_context(
        lease, workspace, app_name=app_name
    )
    return recovery


def validate_v4_timestamps(lease: Any, record: dict[str, str | int]) -> None:
    """Validate bounded signed lifetime and explicit ACL-attestation time."""

    acquired = lease._parse_timestamp(record, "acquired_at")
    attested = lease._parse_timestamp(record, "acl_attested_at")
    expires = lease._parse_timestamp(record, "expires_at")
    active_limit = acquired + lease.MAX_ACTIVE_LEASE_LIFETIME
    active_valid = acquired <= attested <= expires <= active_limit
    released_valid = acquired <= attested <= active_limit and expires >= attested
    if not (active_valid if record.get("state") == "active" else released_valid):
        raise RuntimeError("App deployment lease timestamp contract is invalid")


def record_digest(lease: Any, record: dict[str, str | int]) -> str:
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
    record: dict[str, str | int],
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
    parent: dict[str, str | int],
    *,
    operation: str,
    changes: dict[str, str | int],
) -> dict[str, str | int]:
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
    before: dict[str, str | int],
    after: dict[str, str | int],
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
    return not any(
        before.get(key) != after.get(key) for key in set(before) - mutable
    ) and (
        after.get("state") == "active"
        and after.get("parent_generation_id") == before.get("generation_id")
        and lease._expires_at(after) >= lease._expires_at(before)
    )
