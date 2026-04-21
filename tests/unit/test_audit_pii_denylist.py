"""Slice-6 governance guard: audit metadata PII denylist.

The append-only audit ledger must not contain raw borrower names,
addresses, emails, or phone numbers. The denylist fires at WRITE
time, not read time, so PII never lands in the JSONB column. Routers
that accidentally pass ``display_name`` / ``street_address`` / etc. in
``payload_json`` surface an ``AuditPIIError`` the reviewer can catch
before the regression ships.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.audit_store import (
    AuditPIIError,
    InMemoryAuditStore,
    LakebaseAuditStore,
    _assert_no_pii,
)

# ---------------------------------------------------------------------------
# _assert_no_pii
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "owner_name",
        "owner_full_name",
        "display_name",
        "street_address",
        "mailing_street",
        "borrower_name",
        "email",
        "phone",
    ],
)
def test_assert_no_pii_blocks_each_denylisted_key(forbidden_key: str) -> None:
    with pytest.raises(AuditPIIError) as info:
        _assert_no_pii({forbidden_key: "Jane Doe"})
    assert forbidden_key in info.value.forbidden_keys


def test_assert_no_pii_is_case_insensitive() -> None:
    # Upper-cased variants of the exact denylist keys still fire; we
    # do not try to canonicalize camelCase -> snake_case because
    # routers in this codebase already use snake_case consistently.
    with pytest.raises(AuditPIIError):
        _assert_no_pii({"DISPLAY_NAME": "Jane"})
    with pytest.raises(AuditPIIError):
        _assert_no_pii({"Email": "a@b.com"})


def test_assert_no_pii_permits_allowlist_adjacent_keys() -> None:
    # ``owner_link_id`` and ``display_lender`` must not false-positive
    # against the denylist -- whole-key match only.
    _assert_no_pii(
        {
            "owner_link_id": "ol-123",
            "display_lender": "Summit Mortgage",
            "action": "view_borrower_360",
            "clip": "clip-ref-abc",
            "score": 92,
        }
    )


def test_assert_no_pii_permits_empty_metadata() -> None:
    _assert_no_pii({})


def test_assert_no_pii_aggregates_multiple_hits() -> None:
    with pytest.raises(AuditPIIError) as info:
        _assert_no_pii({"display_name": "A", "email": "b@c"})
    assert sorted(info.value.forbidden_keys) == ["display_name", "email"]


# ---------------------------------------------------------------------------
# InMemoryAuditStore -- denylist enforced equally on the test path so
# unit tests prove the guard fires before any Lakebase work.
# ---------------------------------------------------------------------------


def test_in_memory_store_rejects_pii_payload() -> None:
    store = InMemoryAuditStore()
    with pytest.raises(AuditPIIError):
        store.write(
            actor="skyler@entrada.ai",
            action="view_borrower_360",
            entity_type="borrower",
            entity_id="B-1",
            payload_json={"display_name": "Alice Smith"},
        )


def test_in_memory_store_accepts_clean_payload() -> None:
    store = InMemoryAuditStore()
    event = store.write(
        actor="skyler@entrada.ai",
        action="view_borrower_360",
        entity_type="borrower",
        entity_id="B-1",
        payload_json={"score": 92, "clip": "clip-ref-abc"},
    )
    assert event.entity_id == "B-1"


# ---------------------------------------------------------------------------
# LakebaseAuditStore -- denylist runs BEFORE the INSERT so a poisoned
# payload never reaches Postgres.
# ---------------------------------------------------------------------------


def test_lakebase_store_rejects_pii_payload_before_insert() -> None:
    client = MagicMock()
    store = LakebaseAuditStore(client=client)
    with pytest.raises(AuditPIIError):
        store.write(
            actor="skyler@entrada.ai",
            action="view_borrower_360",
            entity_type="borrower",
            entity_id="B-1",
            payload_json={"email": "alice@example.com"},
        )
    # Critically: no INSERT was issued.
    client.fetchone.assert_not_called()
    client.execute.assert_not_called()
