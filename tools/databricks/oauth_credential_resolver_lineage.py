"""Canonical lease-chain proof for OAuth credential terminal resolutions."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from tools.databricks import app_deployment_lease


def _string(record: dict[str, object], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or value != value.strip():
        raise RuntimeError(
            "OAuth credential resolution resolver lease is malformed"
        )
    return value


def _canonical_digest(record: dict[str, object]) -> str:
    encoded = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_resolver_lease_records(
    workspace: Any,
    *,
    app_name: str,
) -> dict[str, dict[str, str | int]]:
    """Verify the append-only chain once and index every canonical generation."""

    head = app_deployment_lease._download(  # noqa: SLF001
        workspace,
        app_name=app_name,
    )
    current = app_deployment_lease._read_record(  # noqa: SLF001
        workspace,
        path=app_deployment_lease._path(app_name),  # noqa: SLF001
        app_name=app_name,
    )
    if head is None or current is None:
        raise RuntimeError(
            "OAuth credential resolver lease lineage is unavailable"
        )
    generations: dict[str, dict[str, str | int]] = {}
    for _ in range(app_deployment_lease.MAX_CANONICAL_GENERATIONS):
        generation_id = str(current.get("generation_id") or "")
        if generation_id in generations:
            raise RuntimeError(
                "OAuth credential resolver lease chain repeats a generation"
            )
        generations[generation_id] = current
        if current == head:
            break
        successor = app_deployment_lease._read_record(  # noqa: SLF001
            workspace,
            path=app_deployment_lease._successor_path(  # noqa: SLF001
                app_name,
                str(current["generation_id"]),
            ),
            app_name=app_name,
        )
        if successor is None:
            raise RuntimeError(
                "OAuth credential resolver lease chain does not reach its head"
            )
        app_deployment_lease._validate_transition(  # noqa: SLF001
            current,
            successor,
        )
        current = successor
    else:
        raise RuntimeError(
            "OAuth credential resolver lease chain exceeds its safety bound"
        )
    return generations


def canonical_resolver_lease_record(
    workspace: Any,
    resolution: dict[str, object],
    *,
    canonical_records: dict[str, dict[str, str | int]] | None = None,
) -> dict[str, str | int]:
    """Return the exact signed resolver generation from one verified chain."""

    app_name = _string(resolution, "app_name")
    target_generation_id = _string(
        resolution,
        "resolver_lease_generation_id",
    )
    try:
        target_generation_id = str(UUID(target_generation_id))
    except ValueError as exc:
        raise RuntimeError(
            "OAuth credential resolver generation identity is invalid"
        ) from exc
    generations = (
        canonical_records
        if canonical_records is not None
        else canonical_resolver_lease_records(
            workspace,
            app_name=app_name,
        )
    )
    matched = generations.get(target_generation_id)
    if matched is None:
        raise RuntimeError(
            "OAuth credential resolver generation is not in the signed lease chain"
        )
    return matched


def validate_resolution_resolver(
    resolution: dict[str, object],
    intent: dict[str, object],
    canonical_lease_record: dict[str, str | int],
) -> None:
    """Bind every resolver coordinate to one canonical signed lease generation."""

    resolver_generation_seq = resolution.get("resolver_lease_generation_seq")
    intent_generation_seq = intent.get("lease_generation_seq")
    canonical_generation_seq = canonical_lease_record.get("generation_seq")
    if (
        not isinstance(resolver_generation_seq, int)
        or isinstance(resolver_generation_seq, bool)
        or not isinstance(intent_generation_seq, int)
        or isinstance(intent_generation_seq, bool)
        or not isinstance(canonical_generation_seq, int)
        or isinstance(canonical_generation_seq, bool)
    ):
        raise RuntimeError(
            "OAuth credential resolution resolver generation is malformed"
        )
    try:
        resolver_lease_id = str(
            UUID(_string(resolution, "resolver_lease_id"))
        )
        resolver_recovery_root_id = str(
            UUID(_string(resolution, "resolver_lease_recovery_root_id"))
        )
        resolver_generation_id = str(
            UUID(_string(resolution, "resolver_lease_generation_id"))
        )
    except ValueError as exc:
        raise RuntimeError(
            "OAuth credential resolution resolver identity is malformed"
        ) from exc
    canonical = {
        "lease_id": canonical_lease_record.get("lease_id"),
        "recovery_root_lease_id": canonical_lease_record.get(
            "recovery_root_lease_id"
        ),
        "generation_id": canonical_lease_record.get("generation_id"),
        "generation_seq": canonical_generation_seq,
        "source_git_sha": canonical_lease_record.get("source_git_sha"),
        "record_sha256": _canonical_digest(
            {str(key): value for key, value in canonical_lease_record.items()}
        ),
    }
    actual = {
        "lease_id": resolver_lease_id,
        "recovery_root_lease_id": resolver_recovery_root_id,
        "generation_id": resolver_generation_id,
        "generation_seq": resolver_generation_seq,
        "source_git_sha": _string(
            resolution,
            "resolver_source_git_sha",
        ),
        "record_sha256": _string(
            resolution,
            "resolver_lease_record_sha256",
        ),
    }
    original = (
        resolver_lease_id == _string(intent, "lease_id")
        and resolver_recovery_root_id
        == _string(intent, "lease_recovery_root_id")
        and resolver_generation_id == _string(intent, "lease_generation_id")
        and resolver_generation_seq == intent_generation_seq
        and actual["record_sha256"] == _string(intent, "lease_record_sha256")
        and actual["source_git_sha"] == _string(intent, "source_git_sha")
    )
    successor = (
        resolver_lease_id != _string(intent, "lease_id")
        and resolver_recovery_root_id
        == _string(intent, "lease_recovery_root_id")
        and resolver_generation_id != _string(intent, "lease_generation_id")
        and resolver_generation_seq > intent_generation_seq
    )
    if actual != canonical or not (original or successor):
        raise RuntimeError(
            "OAuth credential resolution resolver lineage is not canonical"
        )
