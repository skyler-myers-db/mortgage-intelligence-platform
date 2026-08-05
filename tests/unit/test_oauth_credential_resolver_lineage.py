from __future__ import annotations

import hashlib
import json

import pytest

from tools.databricks import app_deployment_lease
from tools.databricks import oauth_credential_record_inventory as inventory
from tools.databricks import oauth_credential_records as records
from tools.databricks import oauth_credential_resolver_lineage as lineage

_ROOT = "11111111-1111-4111-8111-111111111111"
_BASE_GENERATION = "22222222-2222-4222-8222-222222222222"
_SUCCESSOR_LEASE = "33333333-3333-4333-8333-333333333333"
_SUCCESSOR_GENERATION = "44444444-4444-4444-8444-444444444444"
_SOURCE = "a" * 40


def _record(
    *,
    lease_id: str,
    generation_id: str,
    generation_seq: int,
) -> dict[str, str | int]:
    return {
        "lease_id": lease_id,
        "recovery_root_lease_id": _ROOT,
        "generation_id": generation_id,
        "generation_seq": generation_seq,
        "source_git_sha": _SOURCE,
    }


def _patch_chain(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, str | int], ...]:
    base = _record(
        lease_id=_ROOT,
        generation_id=_BASE_GENERATION,
        generation_seq=0,
    )
    successor = _record(
        lease_id=_SUCCESSOR_LEASE,
        generation_id=_SUCCESSOR_GENERATION,
        generation_seq=1,
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "_download",
        lambda _workspace, *, app_name: successor
        if app_name == "mip-oauth-credential-mutations"
        else pytest.fail("unexpected lease name"),
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "_path",
        lambda app_name: f"/{app_name}/base",
    )
    monkeypatch.setattr(
        app_deployment_lease,
        "_successor_path",
        lambda app_name, generation_id: (
            f"/{app_name}/after/{generation_id}"
        ),
    )

    def read(
        _workspace: object,
        *,
        path: str,
        app_name: str,
    ) -> dict[str, str | int] | None:
        if path == f"/{app_name}/base":
            return base
        if path == f"/{app_name}/after/{_BASE_GENERATION}":
            return successor
        return None

    monkeypatch.setattr(app_deployment_lease, "_read_record", read)
    monkeypatch.setattr(
        app_deployment_lease,
        "_validate_transition",
        lambda parent, child: None
        if (parent, child) == (base, successor)
        else pytest.fail("noncanonical transition"),
    )
    return base, successor


def _resolution(generation_id: str) -> dict[str, object]:
    return {
        "app_name": "mip-oauth-credential-mutations",
        "resolver_lease_generation_id": generation_id,
    }


def test_canonical_resolver_generation_must_exist_in_signed_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, successor = _patch_chain(monkeypatch)

    assert (
        lineage.canonical_resolver_lease_record(
            object(),
            _resolution(_SUCCESSOR_GENERATION),
        )
        == successor
    )
    with pytest.raises(RuntimeError, match="not in the signed lease chain"):
        lineage.canonical_resolver_lease_record(
            object(),
            _resolution("99999999-9999-4999-8999-999999999999"),
        )


def test_resolution_coordinates_must_equal_canonical_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, successor = _patch_chain(monkeypatch)
    digest = hashlib.sha256(
        json.dumps(
            successor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    intent = {
        "lease_id": _ROOT,
        "lease_recovery_root_id": _ROOT,
        "lease_generation_id": _BASE_GENERATION,
        "lease_generation_seq": 0,
        "lease_record_sha256": "c" * 64,
        "source_git_sha": _SOURCE,
    }
    resolution = {
        "resolver_lease_id": _SUCCESSOR_LEASE,
        "resolver_lease_recovery_root_id": _ROOT,
        "resolver_lease_generation_id": _SUCCESSOR_GENERATION,
        "resolver_lease_generation_seq": 1,
        "resolver_lease_record_sha256": digest,
        "resolver_source_git_sha": _SOURCE,
    }

    lineage.validate_resolution_resolver(resolution, intent, successor)
    resolution["resolver_lease_generation_seq"] = 999
    with pytest.raises(RuntimeError, match="not canonical"):
        lineage.validate_resolution_resolver(resolution, intent, successor)


def test_inventory_verifies_lease_chain_once_for_many_resolutions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent_paths = tuple(
        (
            "/.mip-deployment-leases/"
            f"history-{index}{records.INTENT_SUFFIX}"
        )
        for index in range(100)
    )
    resolution_paths = tuple(
        records.resolution_path(path) for path in intent_paths
    )
    monkeypatch.setattr(
        inventory,
        "record_paths",
        lambda _workspace: tuple(sorted(intent_paths + resolution_paths)),
    )

    def read_json(
        _workspace: object,
        path: str,
    ) -> tuple[dict[str, object], bytes]:
        if path in intent_paths:
            return {}, f"intent:{path}".encode()
        return (
            {
                "app_name": "mip-oauth-credential-mutations",
                "resolver_lease_generation_id": _SUCCESSOR_GENERATION,
            },
            f"resolution:{path}".encode(),
        )

    monkeypatch.setattr(inventory, "read_json", read_json)
    monkeypatch.setattr(inventory, "validate_intent", lambda *_args: None)
    monkeypatch.setattr(inventory, "validate_resolution", lambda *_args, **_kwargs: None)
    loads = 0
    successor = _record(
        lease_id=_SUCCESSOR_LEASE,
        generation_id=_SUCCESSOR_GENERATION,
        generation_seq=1,
    )

    def load_chain(
        _workspace: object,
        *,
        app_name: str,
    ) -> dict[str, dict[str, str | int]]:
        nonlocal loads
        assert app_name == "mip-oauth-credential-mutations"
        loads += 1
        return {_SUCCESSOR_GENERATION: successor}

    monkeypatch.setattr(
        inventory,
        "canonical_resolver_lease_records",
        load_chain,
    )
    monkeypatch.setattr(
        inventory,
        "canonical_resolver_lease_record",
        lineage.canonical_resolver_lease_record,
    )

    assert inventory.unresolved_record_paths(object()) == ()
    assert loads == 1
