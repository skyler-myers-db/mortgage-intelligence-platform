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

from backend.services.address_normalization import address_lookup_hash, normalize_address
from backend.services.pii_redaction import mask_address_for_audit
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
    expected_token = mask_address_for_audit(normalize_address(_RAW_ADDRESS), _ZIP)

    # Flatten every audit payload value to a string and assert the raw address
    # (and any token of it) never appears, and NO part of the plain sha2 join
    # key appears -- the ledger carries only the tenant-secret HMAC token
    # (governance review FU-1, 2026-07-07): payload [:16], miss entity [:32].
    serialized = " ".join(str(v) for v in event.payload_json.values())
    serialized += f" {event.entity_id} {event.subject_clip or ''}"
    assert _RAW_ADDRESS not in serialized
    assert "Evergreen" not in serialized
    assert "742" not in serialized
    assert full_hash not in serialized
    assert full_hash[:16] not in serialized
    assert event.payload_json["address_hash"] == expected_token[:16]
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


def test_miss_entity_id_uses_hmac_token_not_plain_hash() -> None:
    """FU-2 (governance review 2026-07-07): the miss entity id is
    auto-<token[:32]> derived from the HMAC audit token, so the ledger never
    carries any bits of the plain sha2 join key, at any length. Repeated
    identical misses share an id by design (re-probe linkage is an audit
    feature)."""
    import re

    from backend.schemas.common import PUBLIC_SERVER_ID_PATTERN

    sql = _FakeSqlClient()
    sql.responses = []  # miss
    audit = InMemoryAuditStore()
    lookup_property_loan(
        sql,
        audit,
        actor="skyler@entrada.ai",
        address_line=_RAW_ADDRESS,
        zip5=_ZIP,
    )
    event = audit.list()[0]
    token = mask_address_for_audit(normalize_address(_RAW_ADDRESS), _ZIP)
    plain = address_lookup_hash(_RAW_ADDRESS, _ZIP)
    assert event.entity_id == f"auto-{token[:32]}"
    assert PUBLIC_SERVER_ID_PATTERN.fullmatch(event.entity_id)
    assert plain[:16] not in event.entity_id
    assert re.fullmatch(r"auto-[a-f0-9]{32}", event.entity_id)


def test_unicode_zip_digits_normalize_identically_for_hash_and_audit() -> None:
    """FU-3 (governance review 2026-07-07): fullwidth/unicode digits must not
    diverge between the join-key ZIP and the audit ZIP -- both use ASCII-only
    extraction, so the audit row for a fullwidth ZIP is byte-identical to its
    ASCII twin (and always passes the ASCII-only value policy)."""
    sql = _FakeSqlClient()
    sql.responses = []  # miss
    audit = InMemoryAuditStore()
    lookup_property_loan(
        sql,
        audit,
        actor="skyler@entrada.ai",
        address_line=_RAW_ADDRESS,
        zip5="\uff17\uff15\uff10\uff14\uff13",  # fullwidth 75043
    )
    event = audit.list()[0]
    # ASCII extraction strips fullwidth digits entirely; with no valid ASCII
    # ZIP the audit row omits zip5 rather than storing a non-ASCII or empty
    # value. (The router rejects these before the service in production;
    # this pins the defense-in-depth layer.)
    assert "zip5" not in event.payload_json
    assert event.payload_json["hit"] is False
