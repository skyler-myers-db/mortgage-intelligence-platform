"""Immutable digest-addressed recovery checkpoints for deployment lease v5."""

from __future__ import annotations

import hashlib
import io
import json
import os
from copy import deepcopy
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

CHECKPOINT_VERSION = 1
MAX_RECOVERY_HOLDERS = 64
MAX_RECOVERY_CANDIDATES = 4096
MAX_RECOVERY_HEADS_BYTES = 32 * 1024
MAX_CHECKPOINT_BYTES = 512 * 1024
MAX_PROTOCOL_MARKER_BYTES = 64 * 1024
PROTOCOL_MARKER_VERSION = 1
PROTOCOL_MARKER_FIELDS = {
    "version",
    "app_name",
    "chain_id",
    "anchor_generation_id",
    "anchor_record_digest",
    "anchor_record",
}

HEAD_FIELDS = {
    "recovery_root_lease_id",
    "recovery_index_digest",
    "candidate_count",
    "last_acquire_generation_seq",
    "last_acquire_generation_id",
    "last_acquire_lease_id",
    "previous_recovery_index_digest",
    "key_epoch",
}
CHECKPOINT_FIELDS = {
    "version",
    "app_name",
    "chain_id",
    "holder",
    "recovery_root_lease_id",
    "lease_candidates",
    "candidate_count",
    "last_acquire_generation_seq",
    "last_acquire_generation_id",
    "last_acquire_lease_id",
    "previous_recovery_index_digest",
    "key_epoch",
    "attestation_verify_key",
    "attestation_signature",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_json(raw: bytes, *, artifact: str) -> object:
    """Decode one exact canonical artifact while rejecting duplicate object keys."""

    def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RuntimeError(f"{artifact} contains duplicate JSON keys")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{artifact} is not valid JSON") from exc
    if _canonical(value) != raw:
        raise RuntimeError(f"{artifact} is not canonical JSON")
    return value


def _uuid(value: object, *, message: str) -> str:
    try:
        return str(UUID(str(value or "").strip()))
    except ValueError as exc:
        raise RuntimeError(message) from exc


def _digest(value: object, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip()
    if allow_empty and not normalized:
        return ""
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise RuntimeError("App deployment lease recovery index digest is invalid")
    return normalized


def _nonnegative_int(value: object, *, message: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError(message)
    return value


def validate_heads(value: object) -> dict[str, dict[str, Any]]:
    """Return the exact bounded v5 holder map or fail closed."""

    if not isinstance(value, dict) or len(value) > MAX_RECOVERY_HOLDERS:
        raise RuntimeError("App deployment lease recovery holder map is invalid")
    if len(_canonical(value)) > MAX_RECOVERY_HEADS_BYTES:
        raise RuntimeError("App deployment lease recovery holder map is oversized")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_holder, raw_head in value.items():
        holder = str(raw_holder).strip()
        if (
            not holder
            or holder != raw_holder
            or len(holder.encode("utf-8")) > 256
            or not isinstance(raw_head, dict)
            or set(raw_head) != HEAD_FIELDS
        ):
            raise RuntimeError("App deployment lease recovery holder entry is invalid")
        root = _uuid(
            raw_head["recovery_root_lease_id"],
            message="App deployment lease recovery root is invalid",
        )
        digest = _digest(raw_head["recovery_index_digest"])
        count = _nonnegative_int(
            raw_head["candidate_count"],
            message="App deployment lease recovery candidate count is invalid",
        )
        sequence = _nonnegative_int(
            raw_head["last_acquire_generation_seq"],
            message="App deployment lease recovery generation sequence is invalid",
        )
        generation = _uuid(
            raw_head["last_acquire_generation_id"],
            message="App deployment lease recovery generation is invalid",
        )
        last_lease = _uuid(
            raw_head["last_acquire_lease_id"],
            message="App deployment lease recovery acquisition lease is invalid",
        )
        previous_digest = _digest(
            raw_head["previous_recovery_index_digest"],
            allow_empty=True,
        )
        key_epoch = _nonnegative_int(
            raw_head["key_epoch"],
            message="App deployment lease recovery signing-key epoch is invalid",
        )
        if count < 1 or count > MAX_RECOVERY_CANDIDATES:
            raise RuntimeError("App deployment lease recovery candidate count is invalid")
        if (count == 1) != (not previous_digest):
            raise RuntimeError("App deployment lease recovery predecessor is invalid")
        normalized[holder] = {
            "recovery_root_lease_id": root,
            "recovery_index_digest": digest,
            "candidate_count": count,
            "last_acquire_generation_seq": sequence,
            "last_acquire_generation_id": generation,
            "last_acquire_lease_id": last_lease,
            "previous_recovery_index_digest": previous_digest,
            "key_epoch": key_epoch,
        }
    if normalized != value:
        raise RuntimeError("App deployment lease recovery holder map is not canonical")
    return normalized


def validate_lease_recovery_fields(record: dict[str, Any]) -> None:
    heads = validate_heads(record.get("holder_recovery_heads"))
    digest = _digest(record.get("recovery_index_digest"))
    holder = str(record.get("holder") or "").strip()
    current = heads.get(holder)
    if (
        current is None
        or current["recovery_index_digest"] != digest
        or current["recovery_root_lease_id"] != record.get("recovery_root_lease_id")
    ):
        raise RuntimeError("App deployment lease current recovery index is invalid")
    if record.get("operation") in {"acquire", "takeover"} and (
        current["last_acquire_generation_seq"] != record.get("generation_seq")
        or current["last_acquire_generation_id"] != record.get("generation_id")
        or current["last_acquire_lease_id"] != record.get("lease_id")
        or current["key_epoch"] != record.get("key_epoch")
    ):
        raise RuntimeError("App deployment lease acquisition recovery index is invalid")
    if record.get("generation_seq") == 0 and (
        record.get("operation") != "acquire"
        or current["recovery_root_lease_id"] != record.get("lease_id")
        or current["candidate_count"] != 1
        or current["previous_recovery_index_digest"]
    ):
        raise RuntimeError("App deployment lease initial recovery index is invalid")


def validate_transition(
    parent: dict[str, Any],
    child: dict[str, Any],
    *,
    lease_version: int,
) -> None:
    """Require an acquisition to change only its holder checkpoint pointer."""

    heads = validate_heads(child.get("holder_recovery_heads"))
    holder = str(child["holder"])
    current = heads.get(holder)
    if (
        current is None
        or current["recovery_index_digest"] != child.get("recovery_index_digest")
        or current["recovery_root_lease_id"] != child["recovery_root_lease_id"]
        or current["last_acquire_generation_seq"] != child["generation_seq"]
        or current["last_acquire_generation_id"] != child["generation_id"]
        or current["last_acquire_lease_id"] != child["lease_id"]
        or current["key_epoch"] != child["key_epoch"]
    ):
        raise RuntimeError("App deployment lease recovery index transition is invalid")
    if parent.get("version") != lease_version:
        return
    parent_heads = validate_heads(parent.get("holder_recovery_heads"))
    if any(
        value != heads.get(name) for name, value in parent_heads.items() if name != holder
    ) or any(name != holder and name not in parent_heads for name in heads):
        raise RuntimeError("App deployment lease unrelated recovery holder changed")
    previous = parent_heads.get(holder)
    expected_count = int(previous["candidate_count"]) + 1 if previous else 1
    if current["candidate_count"] != expected_count:
        raise RuntimeError("App deployment lease recovery candidate count did not advance")
    if previous is None:
        if (
            current["recovery_root_lease_id"] != child["lease_id"]
            or current["previous_recovery_index_digest"]
        ):
            raise RuntimeError("App deployment lease new-holder recovery root is invalid")
    elif (
        current["recovery_root_lease_id"] != previous["recovery_root_lease_id"]
        or current["previous_recovery_index_digest"] != previous["recovery_index_digest"]
        or current["key_epoch"] < previous["key_epoch"]
    ):
        raise RuntimeError("App deployment lease holder recovery authority changed")


def checkpoint_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def checkpoint_path(lease: Any, app_name: str, chain_id: str, digest: str) -> str:
    chain = _uuid(
        chain_id,
        message="App deployment lease recovery index chain is invalid",
    )
    canonical_digest = _digest(digest)
    return f"{lease._path(app_name)}.{chain}.{canonical_digest}.recovery-index"


def _protocol_marker_path(lease: Any, app_name: str) -> str:
    return f"{lease._path(app_name)}.protocol-v5"


def _validate_protocol_marker(
    lease: Any,
    value: object,
    *,
    app_name: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != PROTOCOL_MARKER_FIELDS
        or not lease.lease_support.is_exact_integer(value.get("version"), PROTOCOL_MARKER_VERSION)
    ):
        raise RuntimeError("App deployment lease v5 locator is invalid")
    anchor = lease._verify(value.get("anchor_record"))
    chain_id = _uuid(
        value.get("chain_id"),
        message="App deployment lease v5 locator chain is invalid",
    )
    generation_id = _uuid(
        value.get("anchor_generation_id"),
        message="App deployment lease v5 locator generation is invalid",
    )
    digest = _digest(value.get("anchor_record_digest"))
    if (
        value.get("app_name") != app_name.strip()
        or anchor.get("app_name") != app_name.strip()
        or anchor.get("version") != lease.LEASE_VERSION
        or anchor.get("chain_id") != chain_id
        or anchor.get("generation_id") != generation_id
        or lease._record_digest(anchor) != digest
    ):
        raise RuntimeError("App deployment lease v5 locator binding is invalid")
    return anchor


def read_protocol_anchor(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
) -> dict[str, Any] | None:
    """Read the immutable v5 locator used only when the mutable head is unavailable."""

    try:
        stream = workspace.workspace.download(_protocol_marker_path(lease, app_name))
    except (NotFound, ResourceDoesNotExist):
        return None
    raw = stream.read(MAX_PROTOCOL_MARKER_BYTES + 1)
    if len(raw) > MAX_PROTOCOL_MARKER_BYTES:
        raise RuntimeError("App deployment lease v5 locator is oversized")
    value = _canonical_json(raw, artifact="App deployment lease v5 locator")
    anchor = _validate_protocol_marker(lease, value, app_name=app_name)
    canonical = lease._read_record(
        workspace,
        path=lease._record_path(app_name, anchor),
        app_name=app_name,
    )
    if canonical != anchor:
        raise RuntimeError("App deployment lease v5 locator is not canonical")
    return anchor


def ensure_protocol_marker(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    anchor: dict[str, Any],
) -> None:
    """Publish the first committed v5 generation as an immutable fallback locator."""

    existing = read_protocol_anchor(lease, workspace, app_name=app_name)
    if existing is not None:
        if existing["chain_id"] != anchor["chain_id"]:
            raise RuntimeError("App deployment lease v5 locator chain changed")
        return
    value = {
        "version": PROTOCOL_MARKER_VERSION,
        "app_name": app_name,
        "chain_id": anchor["chain_id"],
        "anchor_generation_id": anchor["generation_id"],
        "anchor_record_digest": lease._record_digest(anchor),
        "anchor_record": anchor,
    }
    payload = _canonical(value)
    if len(payload) > MAX_PROTOCOL_MARKER_BYTES:
        raise RuntimeError("App deployment lease v5 locator is oversized")
    try:
        workspace.workspace.upload(
            _protocol_marker_path(lease, app_name),
            io.BytesIO(payload),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except (AlreadyExists, ResourceAlreadyExists):
        pass
    except Exception as upload_error:
        try:
            persisted = read_protocol_anchor(
                lease,
                workspace,
                app_name=app_name,
            )
        except Exception as read_error:
            raise RuntimeError(
                "App deployment lease v5 locator upload failed and commit state "
                "could not be authenticated"
            ) from read_error
        if persisted != anchor:
            raise RuntimeError(
                "App deployment lease v5 locator upload failed without an exact commit"
            ) from upload_error
    if read_protocol_anchor(lease, workspace, app_name=app_name) != anchor:
        raise RuntimeError("App deployment lease v5 locator is not authoritative")


def _message(value: dict[str, Any]) -> bytes:
    unsigned = {
        key: item
        for key, item in value.items()
        if key not in {"attestation_verify_key", "attestation_signature"}
    }
    return b"mip-app-deployment-lease-recovery-index\0" + _canonical(unsigned)


def _sign(lease: Any, value: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(lease._decode(signing, length=32))
    if lease._encode(private.public_key().public_bytes_raw()) != verify:
        raise RuntimeError(
            "App deployment lease recovery signing and verification keys do not match"
        )
    registry = lease._key_registry()
    if not lease.lease_support.metadata_is_exact(value, {1}, registry.index(verify)):
        raise RuntimeError("App deployment lease recovery signing metadata is invalid")
    return {
        **value,
        "attestation_verify_key": verify,
        "attestation_signature": lease._encode(private.sign(_message(value))),
    }


def _verify(lease: Any, value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != CHECKPOINT_FIELDS:
        raise RuntimeError("App deployment lease recovery index is incomplete")
    if not lease.lease_support.is_exact_integer(value.get("version"), CHECKPOINT_VERSION):
        raise RuntimeError("App deployment lease recovery index version is invalid")
    normalized = {str(key): item for key, item in value.items()}
    registry = lease._key_registry()
    verify = str(normalized.get("attestation_verify_key") or "").strip()
    if verify not in registry:
        raise RuntimeError("App deployment lease recovery attestation identity is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(lease._decode(verify, length=32)).verify(
            lease._decode(
                str(normalized.get("attestation_signature") or ""),
                length=64,
            ),
            _message(normalized),
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("App deployment lease recovery index signature is invalid") from exc
    app_name = str(normalized.get("app_name") or "").strip()
    holder = str(normalized.get("holder") or "").strip()
    if (
        not app_name
        or app_name != normalized.get("app_name")
        or not holder
        or holder != normalized.get("holder")
        or len(holder.encode("utf-8")) > 256
    ):
        raise RuntimeError("App deployment lease recovery index identity is invalid")
    chain_id = _uuid(
        normalized.get("chain_id"),
        message="App deployment lease recovery index chain is invalid",
    )
    root = _uuid(
        normalized.get("recovery_root_lease_id"),
        message="App deployment lease recovery root is invalid",
    )
    generation = _uuid(
        normalized.get("last_acquire_generation_id"),
        message="App deployment lease recovery generation is invalid",
    )
    last_lease = _uuid(
        normalized.get("last_acquire_lease_id"),
        message="App deployment lease recovery acquisition lease is invalid",
    )
    previous = _digest(
        normalized.get("previous_recovery_index_digest"),
        allow_empty=True,
    )
    sequence = _nonnegative_int(
        normalized.get("last_acquire_generation_seq"),
        message="App deployment lease recovery generation sequence is invalid",
    )
    key_epoch = _nonnegative_int(
        normalized.get("key_epoch"),
        message="App deployment lease recovery signing-key epoch is invalid",
    )
    candidates = normalized.get("lease_candidates")
    count = _nonnegative_int(
        normalized.get("candidate_count"),
        message="App deployment lease recovery candidate count is invalid",
    )
    if (
        key_epoch != registry.index(verify)
        or not isinstance(candidates, list)
        or count != len(candidates)
        or count < 1
        or count > MAX_RECOVERY_CANDIDATES
    ):
        raise RuntimeError("App deployment lease recovery candidate contract is invalid")
    canonical_candidates = [
        _uuid(
            candidate,
            message="App deployment lease recovery candidate is invalid",
        )
        for candidate in candidates
    ]
    if len(set(canonical_candidates)) != count or canonical_candidates != candidates:
        raise RuntimeError("App deployment lease recovery candidates are not canonical")
    if (
        canonical_candidates[0] != last_lease
        or canonical_candidates[-1] != root
        or (count == 1) != (not previous)
    ):
        raise RuntimeError("App deployment lease recovery continuity is invalid")
    normalized.update(
        {
            "chain_id": chain_id,
            "recovery_root_lease_id": root,
            "last_acquire_generation_id": generation,
            "last_acquire_lease_id": last_lease,
            "previous_recovery_index_digest": previous,
            "lease_candidates": canonical_candidates,
            "last_acquire_generation_seq": sequence,
            "candidate_count": count,
            "key_epoch": key_epoch,
        }
    )
    return normalized


def _read(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    chain_id: str,
    digest: str,
) -> dict[str, Any]:
    path = checkpoint_path(lease, app_name, chain_id, digest)
    try:
        stream = workspace.workspace.download(path)
    except (NotFound, ResourceDoesNotExist) as exc:
        raise RuntimeError("App deployment lease recovery index is missing") from exc
    raw = stream.read(MAX_CHECKPOINT_BYTES + 1)
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise RuntimeError("App deployment lease recovery index is oversized")
    value = _verify(
        lease,
        _canonical_json(raw, artifact="App deployment lease recovery index"),
    )
    if (
        value["app_name"] != app_name.strip()
        or value["chain_id"] != str(UUID(chain_id))
        or checkpoint_digest(value) != digest
    ):
        raise RuntimeError("App deployment lease recovery index path binding is invalid")
    return value


def _persist(
    lease: Any,
    workspace: Any,
    *,
    value: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    signed = _sign(lease, value)
    digest = checkpoint_digest(signed)
    path = checkpoint_path(lease, str(signed["app_name"]), str(signed["chain_id"]), digest)
    payload = _canonical(signed)
    if len(payload) > MAX_CHECKPOINT_BYTES:
        raise RuntimeError("App deployment lease recovery index is oversized")
    try:
        workspace.workspace.upload(
            path,
            io.BytesIO(payload),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except (AlreadyExists, ResourceAlreadyExists):
        pass
    except Exception as upload_error:
        try:
            persisted = _read(
                lease,
                workspace,
                app_name=str(signed["app_name"]),
                chain_id=str(signed["chain_id"]),
                digest=digest,
            )
        except Exception as read_error:
            raise RuntimeError(
                "App deployment lease recovery index upload failed and commit state "
                "could not be authenticated"
            ) from read_error
        if persisted != signed:
            raise RuntimeError(
                "App deployment lease recovery index upload failed without an exact commit"
            ) from upload_error
    persisted = _read(
        lease,
        workspace,
        app_name=str(signed["app_name"]),
        chain_id=str(signed["chain_id"]),
        digest=digest,
    )
    if persisted != signed:
        raise RuntimeError("App deployment lease recovery index is not authoritative")
    return digest, signed


def _entry(checkpoint: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "recovery_root_lease_id": checkpoint["recovery_root_lease_id"],
        "recovery_index_digest": digest,
        "candidate_count": checkpoint["candidate_count"],
        "last_acquire_generation_seq": checkpoint["last_acquire_generation_seq"],
        "last_acquire_generation_id": checkpoint["last_acquire_generation_id"],
        "last_acquire_lease_id": checkpoint["last_acquire_lease_id"],
        "previous_recovery_index_digest": checkpoint["previous_recovery_index_digest"],
        "key_epoch": checkpoint["key_epoch"],
    }


def _checkpoint_value(
    lease: Any,
    record: dict[str, Any],
    *,
    candidates: list[str],
    previous_digest: str,
) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "app_name": record["app_name"],
        "chain_id": record["chain_id"],
        "holder": record["holder"],
        "recovery_root_lease_id": record["recovery_root_lease_id"],
        "lease_candidates": candidates,
        "candidate_count": len(candidates),
        "last_acquire_generation_seq": record["generation_seq"],
        "last_acquire_generation_id": record["generation_id"],
        "last_acquire_lease_id": record["lease_id"],
        "previous_recovery_index_digest": previous_digest,
        "key_epoch": len(lease._key_registry()) - 1,
    }


def _legacy_lineage(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    expected_head: dict[str, Any],
) -> list[dict[str, Any]]:
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
    if current != expected_head:
        raise RuntimeError("App deployment lease recovery observation changed")
    return lineage


def _legacy_contexts(
    lineage: list[dict[str, Any]],
) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    holders = {str(record["holder"]) for record in lineage}
    contexts: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for holder in holders:
        selected = next(
            (record for record in reversed(lineage) if record["holder"] == holder),
            None,
        )
        if selected is None:  # pragma: no cover - holder came from the lineage
            continue
        root = str(selected["recovery_root_lease_id"])
        acquisitions = [
            record
            for record in reversed(lineage)
            if record["holder"] == holder
            and record["recovery_root_lease_id"] == root
            and record["operation"] in {"acquire", "takeover"}
        ]
        unique_acquisitions: list[dict[str, Any]] = []
        candidates: set[str] = set()
        for record in acquisitions:
            candidate = str(record["lease_id"])
            if candidate not in candidates:
                candidates.add(candidate)
                unique_acquisitions.append(record)
        if not unique_acquisitions or root not in candidates:
            raise RuntimeError("App deployment lease recovery lineage is incomplete")
        contexts[holder] = (root, unique_acquisitions)
    return contexts


def legacy_recovery_context(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Scan one exact legacy chain once for migration authorization and snapshotting."""

    holder = lease._holder(workspace)
    if record["state"] == "active" and record["holder"] != holder:
        raise RuntimeError("App deployment lease recovery actor is not its holder")
    lineage = _legacy_lineage(
        lease,
        workspace,
        app_name=app_name,
        expected_head=record,
    )
    context = _legacy_contexts(lineage).get(holder)
    if context is None:
        return "", [], lineage
    root, acquisitions = context
    return root, [str(item["lease_id"]) for item in acquisitions], lineage


def _persist_legacy_checkpoint_chain(
    lease: Any,
    workspace: Any,
    *,
    holder: str,
    root: str,
    acquisitions: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    previous_digest = ""
    candidates: list[str] = []
    checkpoint: dict[str, Any] | None = None
    for acquisition in reversed(acquisitions):
        candidates = [str(acquisition["lease_id"]), *candidates]
        value = _checkpoint_value(
            lease,
            {
                **acquisition,
                "holder": holder,
                "recovery_root_lease_id": root,
            },
            candidates=candidates,
            previous_digest=previous_digest,
        )
        previous_digest, checkpoint = _persist(lease, workspace, value=value)
    if checkpoint is None:  # pragma: no cover - contexts reject an empty lineage
        raise RuntimeError("App deployment lease recovery lineage is incomplete")
    return previous_digest, checkpoint


def prepare_acquisition(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    parent: dict[str, Any] | None,
    record: dict[str, Any],
    legacy_lineage: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Persist parent-plus-acquisition checkpoints and return v5 recovery fields."""

    holder = str(record["holder"])
    heads: dict[str, dict[str, Any]]
    previous_digest = ""
    prior_candidates: list[str] = []
    if parent is not None and parent.get("version") == lease.LEASE_VERSION:
        heads = deepcopy(validate_heads(parent.get("holder_recovery_heads")))
        prior = heads.get(holder)
        if prior is not None:
            previous_digest = str(prior["recovery_index_digest"])
            checkpoint = _read(
                lease,
                workspace,
                app_name=app_name,
                chain_id=str(parent["chain_id"]),
                digest=previous_digest,
            )
            _assert_matches_head(checkpoint, holder=holder, head=prior)
            prior_candidates = list(checkpoint["lease_candidates"])
    elif parent is not None:
        lineage = legacy_lineage or _legacy_lineage(
            lease, workspace, app_name=app_name, expected_head=parent
        )
        if lineage[-1] != parent:
            raise RuntimeError("App deployment lease recovery observation changed")
        contexts = _legacy_contexts(lineage)
        if len(contexts) > MAX_RECOVERY_HOLDERS:
            raise RuntimeError("App deployment lease recovery holder map is invalid")
        heads = {}
        for legacy_holder, (root, acquisitions) in contexts.items():
            digest, checkpoint = _persist_legacy_checkpoint_chain(
                lease,
                workspace,
                holder=legacy_holder,
                root=root,
                acquisitions=acquisitions,
            )
            heads[legacy_holder] = _entry(checkpoint, digest)
            if legacy_holder == holder:
                previous_digest = digest
                prior_candidates = list(checkpoint["lease_candidates"])
    else:
        heads = {}
    root = str(record["recovery_root_lease_id"])
    if holder in heads and heads[holder]["recovery_root_lease_id"] != root:
        raise RuntimeError("App deployment lease holder recovery authority changed")
    candidate = str(record["lease_id"])
    candidates = [candidate, *(item for item in prior_candidates if item != candidate)]
    if len(candidates) > MAX_RECOVERY_CANDIDATES:
        raise RuntimeError("App deployment lease recovery candidate count is invalid")
    value = _checkpoint_value(
        lease,
        record,
        candidates=candidates,
        previous_digest=previous_digest,
    )
    digest, checkpoint = _persist(lease, workspace, value=value)
    heads[holder] = _entry(checkpoint, digest)
    validate_heads(heads)
    return digest, heads


def _assert_matches_head(
    checkpoint: dict[str, Any],
    *,
    holder: str,
    head: dict[str, Any],
) -> None:
    if (
        checkpoint["holder"] != holder
        or checkpoint["recovery_root_lease_id"] != head["recovery_root_lease_id"]
        or checkpoint["candidate_count"] != head["candidate_count"]
        or checkpoint["last_acquire_generation_seq"] != head["last_acquire_generation_seq"]
        or checkpoint["last_acquire_generation_id"] != head["last_acquire_generation_id"]
        or checkpoint["last_acquire_lease_id"] != head["last_acquire_lease_id"]
        or checkpoint["previous_recovery_index_digest"] != head["previous_recovery_index_digest"]
        or checkpoint["key_epoch"] != head["key_epoch"]
    ):
        raise RuntimeError("App deployment lease recovery index diverges from its signed head")


def recovery_context(
    lease: Any,
    workspace: Any,
    *,
    app_name: str,
    record: dict[str, Any],
) -> tuple[str, list[str]]:
    """Resolve one holder checkpoint bound to the exact canonical v5 record."""

    validate_lease_recovery_fields(record)
    holder = lease._holder(workspace)
    if record["state"] == "active" and record["holder"] != holder:
        raise RuntimeError("App deployment lease recovery actor is not its holder")
    heads = validate_heads(record["holder_recovery_heads"])
    head = heads.get(holder)
    if head is None:
        return "", []
    checkpoint = _read(
        lease,
        workspace,
        app_name=app_name,
        chain_id=str(record["chain_id"]),
        digest=str(head["recovery_index_digest"]),
    )
    _assert_matches_head(checkpoint, holder=holder, head=head)
    candidates = list(checkpoint["lease_candidates"])
    root = str(checkpoint["recovery_root_lease_id"])
    if not candidates or candidates[-1] != root:
        raise RuntimeError("App deployment lease recovery index is incomplete")
    if len(candidates) > 1:
        previous = _read(
            lease,
            workspace,
            app_name=app_name,
            chain_id=str(record["chain_id"]),
            digest=str(checkpoint["previous_recovery_index_digest"]),
        )
        if (
            previous["holder"] != holder
            or previous["recovery_root_lease_id"] != root
            or previous["lease_candidates"] != candidates[1:]
            or previous["candidate_count"] != len(candidates) - 1
            or previous["last_acquire_lease_id"] != candidates[1]
            or previous["last_acquire_generation_seq"] >= checkpoint["last_acquire_generation_seq"]
            or previous["key_epoch"] > checkpoint["key_epoch"]
        ):
            raise RuntimeError("App deployment lease recovery predecessor checkpoint is invalid")
    return root, candidates
