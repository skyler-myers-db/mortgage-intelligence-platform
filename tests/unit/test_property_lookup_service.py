"""Service-level tests for the governed property loan lookup.

Drives ``lookup_property_loan`` through a fake SQL client + an
``InMemoryAuditStore`` (the same policy-enforcing store the router uses in
tests). Asserts:
  * hit  -> masked CLIP/owner-link, generalized lender, borrower linkage,
            dossier deep-link, PROPERTY_LOOKUP audit row with hit=True.
  * miss -> matched=False, no identifiers, PROPERTY_LOOKUP audit row with
            hit=False.
  * the REQUIRED audit row is written in BOTH cases.
  * the raw street address NEVER appears in any audit payload value, and the
    audit carries only the first 16 hex of the address hash (never the full 64).
"""
from __future__ import annotations

from typing import Any

from backend.services.address_normalization import address_lookup_hash
from backend.services.property_lookup import lookup_property_loan
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

_RAW_ADDRESS = "742 Evergreen Terrace"
_ZIP = "62704"


class _FakeSqlClient:
    """Returns canned rows keyed on a substring match in the issued SQL."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[str, dict[str, Any] | None]] = []

    def execute_one(
        self, statement: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append((statement, parameters or {}))
        for marker, row in self.responses:
            if marker in statement:
                return row
        return None


def _address_row() -> dict[str, Any]:
    return {
        "clip": "1234567890ABCDEF",
        "owner_link_id": "OL-9988",
        "has_open_lien": True,
        "current_lien_balance": 325000,
        "ltv": 68,
        "first_pos_lender_current": "WELLS FARGO BK NA",
        "current_rate": 6.75,
    }


def _borrower_row() -> dict[str, Any]:
    return {
        "borrower_id": "B-000000000ABCD",
        "opportunity_score": 82,
        "segment_codes": ["itm", "equity"],
    }


def test_lookup_hit_masks_and_links_and_audits() -> None:
    sql = _FakeSqlClient()
    sql.responses = [
        ("address_lookup", _address_row()),
        ("borrower_360", _borrower_row()),
    ]
    audit = InMemoryAuditStore()

    result = lookup_property_loan(
        sql,
        audit,
        actor="skyler@entrada.ai",
        address_line=_RAW_ADDRESS,
        zip5=_ZIP,
    )

    assert result.matched is True
    assert result.match_basis == "exact_normalized_address_zip"
    # Masked identifiers -- never raw CLIP / owner link.
    assert result.clip_ref is not None and result.clip_ref.startswith("clip_ref_")
    assert result.clip_ref != _address_row()["clip"]
    assert result.owner_link_ref is not None and result.owner_link_ref.startswith(
        "owner_link_ref_"
    )
    # Lender generalized to a public alias, not the raw servicer string.
    assert result.loan is not None
    assert result.loan.lender_brand == "Competitor B"
    assert result.loan.current_rate == 6.75
    assert result.loan.ltv == 68
    assert result.loan.has_open_lien is True
    # Borrower linkage + dossier deep-link.
    assert result.borrower_id == "B-000000000ABCD"
    assert result.lead_score == 82
    assert result.segment == ["itm", "equity"]
    assert result.dossier_path == "/borrower-360/B-000000000ABCD"
    assert result.audit_event_id

    # Exactly one audit row, action PROPERTY_LOOKUP, hit=True.
    events = audit.list()
    assert len(events) == 1
    event = events[0]
    assert event.action == "property_lookup"
    assert event.entity_type == "property_lookup"
    assert event.event_type == "PROPERTY_LOOKUP"
    assert event.payload_json["hit"] is True
    assert event.payload_json["zip5"] == _ZIP
    # subject_clip is masked by the store.
    assert event.subject_clip is not None and event.subject_clip.startswith("clip_ref_")


def test_lookup_miss_returns_unmatched_and_audits() -> None:
    sql = _FakeSqlClient()
    sql.responses = []  # address spine returns nothing
    audit = InMemoryAuditStore()

    result = lookup_property_loan(
        sql,
        audit,
        actor="skyler@entrada.ai",
        address_line=_RAW_ADDRESS,
        zip5=_ZIP,
    )

    assert result.matched is False
    assert result.clip_ref is None
    assert result.owner_link_ref is None
    assert result.borrower_id is None
    assert result.lead_score is None
    assert result.segment is None
    assert result.loan is None
    assert result.dossier_path is None
    assert result.audit_event_id

    events = audit.list()
    assert len(events) == 1
    assert events[0].payload_json["hit"] is False
    assert events[0].subject_clip is None


def test_audit_never_contains_raw_address_and_hash_is_truncated() -> None:
    sql = _FakeSqlClient()
    sql.responses = [
        ("address_lookup", _address_row()),
        ("borrower_360", _borrower_row()),
    ]
    audit = InMemoryAuditStore()

    lookup_property_loan(
        sql,
        audit,
        actor="skyler@entrada.ai",
        address_line=_RAW_ADDRESS,
        zip5=_ZIP,
    )

    event = audit.list()[0]
    full_hash = address_lookup_hash(_RAW_ADDRESS, _ZIP)

    # Flatten every audit payload value to a string and assert the raw address
    # (and any token of it) never appears, and the full 64-char hash never
    # appears -- only its 16-hex prefix.
    serialized = " ".join(str(v) for v in event.payload_json.values())
    serialized += f" {event.entity_id} {event.subject_clip or ''}"
    assert _RAW_ADDRESS not in serialized
    assert "Evergreen" not in serialized
    assert "742" not in serialized
    assert full_hash not in serialized
    assert event.payload_json["address_hash"] == full_hash[:16]
    assert len(event.payload_json["address_hash"]) == 16


def test_lookup_hit_without_borrower_link_still_returns_loan() -> None:
    """A property in the share with no scored borrower row still returns loan
    facts (matched=True) but no dossier deep-link."""
    sql = _FakeSqlClient()
    sql.responses = [("address_lookup", _address_row())]  # no borrower_360 row
    audit = InMemoryAuditStore()

    result = lookup_property_loan(
        sql,
        audit,
        actor="skyler@entrada.ai",
        address_line=_RAW_ADDRESS,
        zip5=_ZIP,
    )

    assert result.matched is True
    assert result.loan is not None
    assert result.borrower_id is None
    assert result.dossier_path is None
    assert result.segment is None
    assert audit.list()[0].payload_json["hit"] is True
