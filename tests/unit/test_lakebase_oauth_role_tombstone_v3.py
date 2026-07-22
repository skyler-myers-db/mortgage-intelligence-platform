"""Focused contracts for durable Lakebase bootstrap tombstone identities."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from databricks.sdk.errors import NotFound

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from tools.databricks import lakebase_oauth_role_account_principal as account_principal
from tools.databricks import lakebase_oauth_role_tombstone as tombstone

_BASE_EXTERNAL_ID = "mip:lb:b:v1:" + "a" * 48
_APPLICATION_ID = "22222222-2222-4222-8222-222222222222"
_PRINCIPAL_ID = "78879891843203"
_SIGNING_KEY = base64.urlsafe_b64encode(b"v" * 32).decode().rstrip("=")
_VERIFY_KEY = derive_gateway_proof_verify_key(_SIGNING_KEY)


@pytest.fixture(autouse=True)
def _proof_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", raising=False)


def test_v3_contract_is_exactly_100_bytes_and_binds_both_original_ids() -> None:
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_BASE_EXTERNAL_ID,
        application_id=_APPLICATION_ID,
        principal_id=_PRINCIPAL_ID,
        signing_key=_SIGNING_KEY,
    )

    assert len(display_name) == len(display_name.encode()) == 100
    prefix = tombstone.orphan_tombstone_display_prefix(_BASE_EXTERNAL_ID)
    assert len(prefix) == 6
    payload = display_name.removeprefix(prefix)
    encoded_application_id = payload[:22]
    encoded_principal_id = payload[22:30]
    signature_tail = payload[30:]
    assert len(encoded_application_id) == 22
    assert len(encoded_principal_id) == 8
    assert len(signature_tail) == 64
    assert tombstone._decode_orphan_tombstone(
        display_name,
        base_external_id=_BASE_EXTERNAL_ID,
        marker_application_id=marker_application_id,
    ) == (_APPLICATION_ID, _PRINCIPAL_ID, marker_application_id)

    signature = Ed25519PrivateKey.from_private_bytes(
        tombstone._decode(_SIGNING_KEY, length=32)
    ).sign(
        tombstone._v3_message(
            base_external_id=_BASE_EXTERNAL_ID,
            application_id=_APPLICATION_ID,
            principal_id=_PRINCIPAL_ID,
        )
    )
    assert UUID(marker_application_id).bytes == signature[:16]


def test_v3_principal_id_tampering_invalidates_signature() -> None:
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_BASE_EXTERNAL_ID,
        application_id=_APPLICATION_ID,
        principal_id=_PRINCIPAL_ID,
        signing_key=_SIGNING_KEY,
    )
    prefix = tombstone.orphan_tombstone_display_prefix(_BASE_EXTERNAL_ID)
    payload = display_name.removeprefix(prefix)
    encoded_application_id = payload[:22]
    signature_tail = payload[30:]
    other_principal = tombstone._encode((int(_PRINCIPAL_ID) + 1).to_bytes(6, "big"))
    tampered = f"{prefix}{encoded_application_id}{other_principal}{signature_tail}"

    with pytest.raises(RuntimeError, match="signature is invalid"):
        tombstone._decode_orphan_tombstone(
            tampered,
            base_external_id=_BASE_EXTERNAL_ID,
            marker_application_id=marker_application_id,
        )


def test_same_prefix_marker_for_another_target_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_target = "mip:lb:b:v1:" + "b" * 48
    monkeypatch.setattr(tombstone, "_v3_target_digest", lambda _target: "AAAAA")
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=other_target,
        application_id=_APPLICATION_ID,
        principal_id=_PRINCIPAL_ID,
        signing_key=_SIGNING_KEY,
    )

    assert display_name.startswith(tombstone.orphan_tombstone_display_prefix(_BASE_EXTERNAL_ID))
    with pytest.raises(RuntimeError, match="signature is invalid"):
        tombstone._decode_orphan_tombstone(
            display_name,
            base_external_id=_BASE_EXTERNAL_ID,
            marker_application_id=marker_application_id,
        )


@pytest.mark.parametrize(
    "principal_id",
    ["", "0", "01", "-1", "78879891843203x", str(1 << 48)],
)
def test_v3_rejects_noncanonical_or_unencodable_principal_ids(principal_id: str) -> None:
    with pytest.raises(RuntimeError, match="principal id is invalid"):
        tombstone.orphan_tombstone_contract(
            base_external_id=_BASE_EXTERNAL_ID,
            application_id=_APPLICATION_ID,
            principal_id=principal_id,
            signing_key=_SIGNING_KEY,
        )


def test_v2_decodes_without_inventing_immutable_principal_identity() -> None:
    private = Ed25519PrivateKey.from_private_bytes(tombstone._decode(_SIGNING_KEY, length=32))
    signature = private.sign(
        tombstone._v2_message(
            base_external_id=_BASE_EXTERNAL_ID,
            application_id=_APPLICATION_ID,
        )
    )
    display_name, marker_application_id = tombstone._render_v2_contract(
        base_external_id=_BASE_EXTERNAL_ID,
        application_id=_APPLICATION_ID,
        signature=signature,
    )

    assert len(display_name) == len(display_name.encode()) == 100
    assert tombstone._decode_orphan_tombstone(
        display_name,
        base_external_id=_BASE_EXTERNAL_ID,
        marker_application_id=marker_application_id,
    ) == (_APPLICATION_ID, None, marker_application_id)


def test_tombstone_delete_retries_after_three_direct_misses_then_reappearance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_BASE_EXTERNAL_ID,
        application_id=_APPLICATION_ID,
        principal_id=_PRINCIPAL_ID,
        signing_key=_SIGNING_KEY,
    )
    tombstone_id = "78451793422042"
    marker = (
        tombstone_id,
        _APPLICATION_ID,
        display_name,
        marker_application_id,
        _PRINCIPAL_ID,
    )
    inventory_calls = 0

    def inventory(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        nonlocal inventory_calls
        inventory_calls += 1
        return [marker] if inventory_calls == 1 else []

    retire_calls = 0
    account_gets = 0

    retirement_deadlines: list[float] = []

    def retire(*_args: object, **kwargs: object) -> None:
        nonlocal retire_calls
        retire_calls += 1
        retirement_deadlines.append(float(kwargs["deadline_seconds"]))

    def account_get(principal_id: str) -> SimpleNamespace:
        nonlocal account_gets
        assert principal_id == tombstone_id
        account_gets += 1
        if retire_calls >= 2 or account_gets <= 3:
            raise NotFound("transient direct miss")
        return SimpleNamespace(
            id=tombstone_id,
            application_id=marker_application_id,
            display_name=display_name,
            external_id=None,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        )

    tick = 0

    def monotonic() -> float:
        nonlocal tick
        tick += 1
        return float(tick)

    client = MagicMock()
    account_client = MagicMock()
    account_client.service_principals.get.side_effect = account_get
    client.service_principals.get.side_effect = NotFound("workspace marker is absent")
    account_client.workspaces.list.side_effect = lambda: iter([])
    client.apps.list.side_effect = lambda: iter([])
    monkeypatch.setattr(tombstone, "orphan_tombstones", inventory)
    monkeypatch.setattr(account_principal, "retire_exact_account_principal", retire)
    monkeypatch.setattr(tombstone.time, "monotonic", monotonic)
    monkeypatch.setattr(tombstone.time, "sleep", lambda _seconds: None)

    tombstone.delete_orphan_tombstone(
        client,
        account_client=account_client,
        tombstone_id=tombstone_id,
        base_external_id=_BASE_EXTERNAL_ID,
        allow_unlocked_recovery_for_tests=True,
    )

    assert tombstone._DELETION_DEADLINE_SECONDS == 180.0
    assert tombstone._DELETION_STABILITY_SECONDS == 30.0
    assert retire_calls == 2
    assert all(30.0 <= deadline < 180.0 for deadline in retirement_deadlines)
    assert account_gets > 3
    assert inventory_calls == 1
