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

from backend.services.audit_lakebase_store import LakebaseAuditStore
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    AuditMetadataViolation,
    AuditPIIError,
    _assert_allowlisted,
    _assert_no_pii,
    _assert_public_safe_values,
)
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

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
        # Use allowlist-approved keys (``opportunity_score`` + ``confidence``
        # are on the reviewed R6-20 allowlist; raw ``score`` / ``clip`` are
        # not, because they shadow PII-adjacent vocabulary we deliberately
        # excluded).
        payload_json={"opportunity_score": 92, "confidence": 0.87},
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


# ---------------------------------------------------------------------------
# R6-20 allowlist -- complement to the denylist. Unknown keys raise
# AuditMetadataViolation so a router accidentally adding an unreviewed
# field fails loudly before the row hits the append-only ledger.
# ---------------------------------------------------------------------------


def test_allowlist_rejects_unknown_key() -> None:
    """A payload with a key outside ``_ALLOWED_METADATA_KEYS`` must raise.

    Canonical trigger: a dev adds a new field to an approve/reject
    payload without auditing whether it's PII-adjacent. The denylist
    wouldn't catch ``contact_preference`` (not a PII name), but the
    allowlist does.
    """
    with pytest.raises(AuditMetadataViolation) as info:
        _assert_allowlisted({"action": "outreach.approve", "contact_preference": "sms"})
    assert "contact_preference" in info.value.unexpected_keys


def test_allowlist_permits_known_keys() -> None:
    """A payload made entirely of reviewed keys passes cleanly."""
    _assert_allowlisted(
        {
            "action": "outreach.approve",
            "approval_id": "uuid-123",
            "offer_code": "refi",
            "borrower_id": "B-00042",
            "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "opportunity_score": 92,
            "confidence": 0.87,
            "thresholds_applied": {"min_spread_bps": 75},
        }
    )


def test_allowlist_is_case_insensitive() -> None:
    """Upper-cased known keys still pass -- matches the denylist policy."""
    _assert_allowlisted({"OFFER_CODE": "refi"})


def test_audit_target_lender_ref_rejects_raw_lender_name() -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"target_lender_ref": "Wells Fargo Bank"})


def test_audit_target_lender_ref_accepts_public_alias() -> None:
    _assert_public_safe_values({"target_lender_ref": "Competitor B"})


def test_audit_portfolio_criteria_accepts_reviewed_safe_keys() -> None:
    _assert_allowlisted(
        {
            "portfolio_criteria": {
                "geography": "All",
                "occupancy": "Owner-occupied",
                "lien_status": "Open 1st lien",
                "lender_relationship": "All",
                "product": "Refi",
                "target_lender_ref": "Competitor B",
                "min_equity_pct_label": "≥ 25%",
            }
        }
    )
    _assert_public_safe_values(
        {
            "portfolio_criteria": {
                "geography": "All",
                "target_lender_ref": "Competitor B",
            }
        }
    )


def test_audit_portfolio_criteria_rejects_raw_nested_lender_name() -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values(
            {
                "portfolio_criteria": {
                    "geography": "All",
                    "target_lender_ref": "Wells Fargo Bank",
                }
            }
        )


def test_audit_portfolio_criteria_rejects_unreviewed_nested_key() -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values(
            {
                "portfolio_criteria": {
                    "geography": "All",
                    "owner_name": "Alice Borrower",
                }
            }
        )


def test_audit_portfolio_criteria_rejects_arbitrary_reviewed_key_value() -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values(
            {
                "portfolio_criteria": {
                    "geography": "123 Main Street",
                }
            }
        )


def test_audit_public_safe_values_reject_raw_borrower_id() -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"borrower_id": "1234567890"})


def test_audit_public_safe_values_reject_unknown_offer_code() -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"offer_code": "jane@example.com"})


def test_audit_public_safe_values_reject_pii_like_bulk_id() -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"bulk_id": "jane@example.com"})
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"bulk_id": "555-212-3333"})
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"request_id": "req-123-45-6789"})


@pytest.mark.parametrize(
    "payload",
    [
        {"BORROWER_ID": "1234567890"},
        {"BULK_ID": "jane@example.com"},
        {"Request_ID": "req-123-45-6789"},
        {"CAMPAIGN_ID": "jane@example.com"},
        {"Variant_Name": "Call 212-555-1212"},
    ],
)
def test_audit_public_safe_values_are_case_insensitive(payload: dict[str, str]) -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values(payload)


def test_audit_stores_reject_case_variant_campaign_metadata_before_persisting() -> None:
    store = InMemoryAuditStore()
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="draft_outreach",
            entity_type="outreach_draft",
            entity_id="B-48291",
            payload_json={"CAMPAIGN_ID": "jane@example.com"},
        )
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="draft_outreach",
            entity_type="outreach_draft",
            entity_id="B-48291",
            payload_json={"VARIANT_NAME": "Call 212-555-1212"},
        )

    client = MagicMock()
    lakebase_store = LakebaseAuditStore(client=client)
    with pytest.raises(AuditMetadataValueViolation):
        lakebase_store.write(
            actor="skyler@entrada.ai",
            action="draft_outreach",
            entity_type="outreach_draft",
            entity_id="B-48291",
            payload_json={"CAMPAIGN_ID": "jane@example.com"},
        )
    client.fetchone.assert_not_called()


def test_audit_store_rejects_name_shaped_top_level_columns() -> None:
    store = InMemoryAuditStore()
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="view.custom",
            entity_type="lead_queue",
            entity_id="Jane Smith",
            payload_json={},
        )
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="John Smith",
            entity_type="lead_queue",
            entity_id="manual",
            payload_json={},
        )


def test_audit_store_server_action_overrides_payload_action() -> None:
    store = InMemoryAuditStore()

    event = store.write(
        actor="skyler@entrada.ai",
        action="view.custom",
        entity_type="lead_queue",
        entity_id="manual",
        payload_json={"action": "Jane Smith", "row_count": 1},
    )

    assert event.action == "view.custom"
    assert event.event_type == "VIEW_CUSTOM"
    assert "action" not in event.payload_json


def test_audit_store_accepts_uuid_with_phone_like_digit_run() -> None:
    store = InMemoryAuditStore()

    event = store.write(
        actor="skyler@entrada.ai",
        action="outreach.reject",
        entity_type="approval",
        entity_id="94e455bb-5fed-4745-82db-0b8606194175",
        payload_json={"approval_id": "94e455bb-5fed-4745-82db-0b8606194175"},
        event_type="OUTREACH_REJECT",
    )

    assert event.entity_id == "94e455bb-5fed-4745-82db-0b8606194175"


def test_in_memory_store_scrubs_allowed_free_text_before_persisting() -> None:
    store = InMemoryAuditStore()

    event = store.write(
        actor="skyler@entrada.ai",
        action="outreach.approve",
        entity_type="approval",
        entity_id="94e455bb-5fed-4745-82db-0b8606194175",
        payload_json={
            "approval_id": "94e455bb-5fed-4745-82db-0b8606194175",
            "borrower_id": "B-12345",
            "offer_code": "refi",
            "draft_body": "Call 555-212-3333 or email jane@example.com about 123 Elm St.",
        },
    )

    assert "555-212-3333" not in str(event.payload_json)
    assert "jane@example.com" not in str(event.payload_json)
    assert "123 Elm St" not in str(event.payload_json)
    assert "[PHONE-REDACTED]" in event.payload_json["draft_body"]
    assert "[EMAIL-REDACTED]" in event.payload_json["draft_body"]
    assert "[ADDRESS-REDACTED]" in event.payload_json["draft_body"]


def test_in_memory_store_rejects_unlisted_metadata_key() -> None:
    """The InMemoryAuditStore must run the allowlist check. ``owner_address``
    is NOT on the denylist (not one of the classic name/phone/email keys)
    but is also NOT on the allowlist -- the exact gap R6-20 closes."""
    store = InMemoryAuditStore()
    with pytest.raises(AuditMetadataViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="outreach.approve",
            entity_type="approval",
            entity_id="uuid-123",
            payload_json={"owner_address": "123 Main St"},
        )


def test_lakebase_store_rejects_unlisted_key_before_insert() -> None:
    """Allowlist runs before the INSERT so poisoned payloads never
    reach Postgres."""
    client = MagicMock()
    store = LakebaseAuditStore(client=client)
    with pytest.raises(AuditMetadataViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="outreach.approve",
            entity_type="approval",
            entity_id="uuid-123",
            payload_json={"owner_address": "123 Main St"},
        )
    client.fetchone.assert_not_called()
    client.execute.assert_not_called()


def test_historical_payload_shapes_all_pass_allowlist() -> None:
    """Regression test: every audit.write() call site in backend/api/*
    as of 2026-04-23 must pass the allowlist cleanly. If this test
    breaks, the allowlist inventory drifted from the call sites."""
    store = InMemoryAuditStore()
    # borrowers.py::read_borrower_360
    store.write(
        actor="a@b.com", action="view_borrower_360",
        entity_type="borrower", entity_id="B-1",
        payload_json={
            "opportunity_score": 92,
            "confidence": 0.87,
            "segment_codes": ["itm"],
            "recommended_offer": "refi",
        },
    )
    # outreach.py::draft_outreach
    store.write(
        actor="a@b.com", action="draft_outreach",
        entity_type="outreach_draft", entity_id="B-1",
        payload_json={"channel": "email", "offer_code": "refi"},
    )
    # outreach.py::approve_outreach
    store.write(
        actor="a@b.com", action="outreach.approve",
        entity_type="approval", entity_id="94e455bb-5fed-4745-82db-0b8606194175",
        payload_json={
            "approval_id": "94e455bb-5fed-4745-82db-0b8606194175",
            "offer_code": "refi",
            "borrower_id": "B-1",
            "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "draft_body": "Hi there...",
        },
    )
    # outreach.py::reject_outreach
    store.write(
        actor="a@b.com", action="outreach.reject",
        entity_type="approval", entity_id="2ee3b92a-8910-4dc1-b72a-5ef0e0413f37",
        payload_json={
            "approval_id": "2ee3b92a-8910-4dc1-b72a-5ef0e0413f37",
            "offer_code": "refi",
            "borrower_id": "B-1",
            "request_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "rationale": "not a fit",
        },
    )
    # leads.py::list_leads_ranked
    store.write(
        actor="a@b.com", action="view_leads_ranked",
        entity_type="lead_queue", entity_id="itm",
        payload_json={
            "rendered_borrower_ids": ["B-1", "B-2"],
            "portfolio_id": "p-1",
            "segment": "itm",
            "portfolio_criteria": {
                "geography": "All",
                "target_lender_ref": "Competitor B",
            },
            "limit": 50,
        },
    )
    # offers.py::recommend_offer
    store.write(
        actor="a@b.com", action="recommend_offer",
        entity_type="borrower", entity_id="B-1",
        payload_json={
            "offer_code": "refi",
            "confidence": 0.87,
            "thresholds_applied": {"min_spread_bps": 75},
        },
    )
