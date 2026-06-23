"""Unit tests for ``backend.services.pii_redaction``.

These tests are the first-line enforcement of the
governance-security-reviewer §1 contract: real borrower PII never
reaches the ``/api/*`` surface. Every redactor output is asserted to
be free of forbidden keys; every branch of the lender-name generalizer
is pinned; the synthesized display_name shape is locked; the
generalized subject_property guarantees no digit appears in the
street-number slot.
"""
from __future__ import annotations

import re

import pytest

from backend.config.settings import settings
from backend.services.pii_redaction import (
    _FORBIDDEN_OUTPUT_KEYS,
    _LENDER_REF_MAP,
    LenderRefResolver,
    _reset_lender_resolver_for_tests,
    generalize_lender,
    mask_cotality_id,
    redact_borrower_row,
    redact_evidence_row,
    redact_lead_row,
    synthesize_display_name,
    synthesize_subject_property,
)

# ---------------------------------------------------------------------------
# Display name
# ---------------------------------------------------------------------------


def test_display_name_uses_first_8_hash_chars() -> None:
    assert synthesize_display_name("abcd1234ef56") == "Owner abcd1234"


def test_display_name_defaults_when_hash_missing() -> None:
    assert synthesize_display_name(None) == "Owner anon"
    assert synthesize_display_name("") == "Owner anon"


def test_display_name_strips_non_hex_bytes() -> None:
    # Defensive: whitespace, upper-case, and non-hex punctuation are
    # all tolerated. The "0x" prefix is preserved as '0' (a valid hex
    # char) + 'x' dropped; that's OK -- we just need a stable 8-char
    # suffix of the original bytes.
    name = synthesize_display_name("  abcd1234ef-56!  ")
    assert name == "Owner abcd1234"


# ---------------------------------------------------------------------------
# Subject property
# ---------------------------------------------------------------------------


def test_subject_property_has_city_state_zip_only() -> None:
    out = synthesize_subject_property("Chicago", "IL", "60614")
    assert out == "Synthetic property · Chicago, IL 60614"


def test_subject_property_street_portion_has_no_digits() -> None:
    # The regex guard is an anti-regression: if someone re-adds a
    # street number to the subject_property synthesizer, this test
    # fails.
    out = synthesize_subject_property("Austin", "TX", "78704")
    # Pattern: "Synthetic property · <street>, <state> <zip5>". Split
    # on ", " to isolate the city portion; only the zip should contain
    # digits.
    street_portion = out.split(",")[0]
    assert not re.search(r"\d", street_portion), (
        f"subject_property street portion has a digit: {street_portion!r}"
    )


def test_subject_property_truncates_long_zip() -> None:
    # ZIP+4 or accidental 9-digit zips must be clipped to 5.
    out = synthesize_subject_property("Denver", "CO", "80206-1234")
    assert out.endswith("80206")


# ---------------------------------------------------------------------------
# Lender generalizer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("UNITED WHOLESALE MTG", "Competitor A"),
        ("WELLS FARGO BK NA", "Competitor B"),
        ("JPMORGAN CHASE BK NA", "Competitor C"),
        ("ROCKET MTG LLC", "Competitor D"),
        ("QUICKEN LNS", "Competitor E"),
        ("BANK OF AMERICA NA", "Competitor F"),
        ("GUARANTEED RATE INC", "Competitor G"),
        ("LOANDEPOT.COM LLC", "Competitor H"),
        ("CALIBER HM LOANS INC", "Competitor I"),
        ("FAIRWAY INDEPENDENT MTG CORP", "Competitor J"),
        ("SUMMIT MTG", "Summit Mortgage"),
        ("SUMMIT MORTGAGE", "Summit Mortgage"),
        ("SUMMIT MTG CORP", "Summit Mortgage"),
        ("SUMMIT MORTGAGE CORP", "Summit Mortgage"),
        # Case-insensitive lookup
        ("wells fargo bk na", "Competitor B"),
        # Whitespace handling
        ("  WELLS FARGO BK NA  ", "Competitor B"),
    ],
)
def test_generalize_lender_known_vocabulary(raw: str, expected: str) -> None:
    assert generalize_lender(raw) == expected


def test_generalize_lender_unknown_is_public_safe_alias() -> None:
    assert generalize_lender("PENNYMAC LOAN SERVICES LLC") == "Competitor Other"


def test_generalize_lender_none_passthrough() -> None:
    assert generalize_lender(None) is None
    assert generalize_lender("") is None
    assert generalize_lender("   ") is None


# ---------------------------------------------------------------------------
# redact_borrower_row -- forbidden-key invariant + field shape
# ---------------------------------------------------------------------------


def _sample_borrower_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "clip": "clip-abc-123",
        "borrower_id": "B-12345",
        "owner_name_hash": "abcd1234ef5678",
        "city": "Chicago",
        "state": "IL",
        "zip": "60614",
        "segment_codes": ["itm", "equity"],
        "equity_estimate": 285000,
        "rate_spread_bps": 88,
        "opportunity_score": 82,
        "confidence": 82,
        "recommended_offer": "Refinance + HELOC",
        "why_now": "Lien spread 88 bps -- refi + HELOC pencils.",
        "evidence_ids": ["ev-001", "ev-002"],
        "approval_status": "pending",
        "owner_link_id": "ol-xxxx",
        "avm_value": 625000,
        "current_lien_balance": 340000,
        "current_rate": 5.75,
        "ltv": 54,
        "related_property_count": 1,
        "situs_cbsa_code": "16980",
        "first_pos_loan_type": "CONV",
        "is_owner_occupied": False,
        "is_absentee": True,
        "is_corporate_owner": False,
        "is_investor": True,
        "is_current_customer": False,
        "is_former_customer": False,
        "is_competitor_lien": True,
        "has_permit": False,
        "listed_for_sale": False,
        "second_pos_amount": 0,
        "has_first_party_relationship": True,
        "first_party_relationship_depth": 3,
        "first_party_recent_interactions": 2,
        "first_party_recent_application": True,
        "first_party_synthetic_demo": True,
        # Forbidden raw-PII columns we expect the redactor to strip --
        # these MUST NOT appear in the output.
        "owner_1_full_name": "Jane Q Public",
        "situs_street_address": "123 Elm St",
        "mailing_street_address": "PO Box 9",
        "trigger_timeline_json": '[{"evidence_id":"ev-001"}]',
        "current_lender_ref": "WELLS FARGO BK NA",
    }
    row.update(overrides)
    return row


def test_redact_borrower_row_strips_forbidden_keys() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    leaks = _FORBIDDEN_OUTPUT_KEYS.intersection(out.keys())
    assert not leaks, f"forbidden keys leaked: {leaks}"


def test_redact_borrower_row_renames_clip_to_clip_id() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", out["clip_id"])
    assert "clip" not in out


def test_redact_borrower_row_masks_owner_link_id() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert re.fullmatch(r"owner_link_ref_[0-9a-f]{12}", out["owner_link_id"])


def test_redact_borrower_row_falls_back_to_public_ids_when_raw_ids_missing() -> None:
    out = redact_borrower_row(_sample_borrower_row(clip=None, owner_link_id=None))

    assert out["clip_id"] == "clip_demo_B-12345"
    assert out["owner_link_id"] == "ol_demo_B-12345"


def test_mask_cotality_id_preserves_synthetic_demo_refs() -> None:
    assert mask_cotality_id("clip", "clip_demo_48291") == "clip_demo_48291"
    assert mask_cotality_id("owner_link", "ol_demo_48291") == "ol_demo_48291"


def test_mask_cotality_id_ignores_legacy_raw_id_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_EXPOSE_RAW_COTALITY_IDS", "1")
    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", mask_cotality_id("clip", "1234567890"))
    assert re.fullmatch(
        r"owner_link_ref_[0-9a-f]{12}",
        mask_cotality_id("owner_link", "9876543210"),
    )


def test_mask_cotality_id_requires_secret_outside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIP_COTALITY_ID_MASK_SECRET", raising=False)
    monkeypatch.delenv("MIP_GENIE_ACTION_SECRET", raising=False)
    monkeypatch.setattr(settings, "app_env", "customer")

    with pytest.raises(RuntimeError, match="MIP_COTALITY_ID_MASK_SECRET"):
        mask_cotality_id("clip", "1234567890")


def test_mask_cotality_id_ignores_genie_action_secret_for_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIP_COTALITY_ID_MASK_SECRET", raising=False)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET", "not-a-mask-secret")
    monkeypatch.setattr(settings, "app_env", "customer")

    with pytest.raises(RuntimeError, match="MIP_COTALITY_ID_MASK_SECRET"):
        mask_cotality_id("clip", "1234567890")


def test_mask_cotality_id_rejects_placeholder_secret_outside_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "REDACTED")
    monkeypatch.setattr(settings, "app_env", "customer")

    with pytest.raises(RuntimeError, match="MIP_COTALITY_ID_MASK_SECRET"):
        mask_cotality_id("clip", "1234567890")


def test_mask_cotality_id_accepts_customer_mask_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "customer-mask-secret")
    monkeypatch.setattr(settings, "app_env", "customer")

    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", mask_cotality_id("clip", "1234567890"))


def test_redact_borrower_row_synthesizes_display_name() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert out["display_name"] == "Owner abcd1234"


def test_redact_borrower_row_synthesizes_subject_property() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert out["subject_property"] == "Synthetic property · Chicago, IL 60614"


def test_redact_borrower_row_masks_current_lender_ref() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert out["current_lender_ref"] == "Competitor B"


def test_redact_borrower_row_preserves_module0_boolean_flags() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert out["is_owner_occupied"] is False
    assert out["is_investor"] is True
    assert out["is_absentee"] is True
    assert out["is_corporate_owner"] is False
    assert out["is_current_customer"] is False
    assert out["is_former_customer"] is False
    assert out["is_competitor_lien"] is True
    assert out["has_permit"] is False
    assert out["listed_for_sale"] is False
    assert out["second_pos_amount"] == 0
    assert out["situs_cbsa_code"] == "16980"
    assert out["first_pos_loan_type"] == "CONV"
    assert out["has_first_party_relationship"] is True
    assert out["first_party_relationship_depth"] == 3
    assert out["first_party_recent_interactions"] == 2
    assert out["first_party_recent_application"] is True
    assert out["first_party_synthetic_demo"] is True


# ---------------------------------------------------------------------------
# redact_lead_row
# ---------------------------------------------------------------------------


def _sample_lead_row() -> dict[str, object]:
    return {
        "clip": "clip-abc-123",
        "borrower_id": "B-12345",
        "owner_name_hash": "0123abcd4567ff",
        "display_name": "Owner anon",  # gold already carries synthesized label
        "city": "Chicago",
        "state": "IL",
        "zip": "60614",
        "segment_codes": ["itm"],
        "equity_estimate": 285000,
        "rate_spread_bps": 88,
        "opportunity_score": 82,
        "confidence": 82,
        "recommended_offer": "Refinance",
        "why_now": "ok",
        "evidence_ids": ["ev-001"],
        "approval_status": "pending",
        "current_lender_ref": "WELLS FARGO BK NA",
        # Raw-PII columns the redactor must drop.
        "owner_1_full_name": "Jane Q Public",
        "situs_street_address": "123 Elm St",
    }


def test_redact_lead_row_strips_forbidden_keys() -> None:
    out = redact_lead_row(_sample_lead_row())
    leaks = _FORBIDDEN_OUTPUT_KEYS.intersection(out.keys())
    assert not leaks, f"forbidden keys leaked: {leaks}"


def test_redact_lead_row_masks_clip_value() -> None:
    out = redact_lead_row(_sample_lead_row())
    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", out["clip"])


def test_redact_lead_row_prefers_hash_derived_display_name() -> None:
    row = _sample_lead_row()
    out = redact_lead_row(row)
    assert out["display_name"] == "Owner 0123abcd"


def test_redact_lead_row_falls_back_to_gold_display_name_when_no_hash() -> None:
    row = _sample_lead_row()
    row.pop("owner_name_hash")
    out = redact_lead_row(row)
    assert out["display_name"] == "Owner anon"


def test_redact_lead_row_masks_current_lender_ref() -> None:
    out = redact_lead_row(_sample_lead_row())
    assert out["current_lender_ref"] == "Competitor B"


# ---------------------------------------------------------------------------
# redact_evidence_row -- lender name passes through the generalizer for
# competitor_lien rows but leaves other signal types untouched.
# ---------------------------------------------------------------------------


def test_redact_evidence_row_strips_forbidden_keys() -> None:
    row = {
        "evidence_id": "ev-abc",
        "source_product": "Voluntary Lien",
        "source_table": "mip.silver.lien_current",
        "signal_type": "rate_spread",
        "signal_value": "+88 bps",
        "display_text": "spread",
        "confidence": 0.92,
        "timestamp": "2026-04-20T06:12:00Z",
        # Raw PII we don't expect but let's be sure the redactor blocks it.
        "owner_1_full_name": "Jane Q Public",
    }
    out = redact_evidence_row(row)
    leaks = _FORBIDDEN_OUTPUT_KEYS.intersection(out.keys())
    assert not leaks


def test_redact_evidence_row_generalizes_lender_string_for_competitor_lien() -> None:
    row = {
        "evidence_id": "ev-abc",
        "source_product": "Voluntary Lien",
        "source_table": "mip.silver.lien_current",
        "signal_type": "competitor_lien",
        "signal_value": "WELLS FARGO BK NA",  # raw lender string
        "display_text": "competitor",
        "confidence": 0.89,
        "timestamp": "2026-04-10T07:55:00Z",
    }
    out = redact_evidence_row(row)
    assert out["signal_value"] == "Competitor B"


def test_redact_evidence_row_passthrough_for_non_lender_signals() -> None:
    row = {
        "evidence_id": "ev-abc",
        "source_product": "AVM",
        "source_table": "mip.silver.lien_current",
        "signal_type": "equity",
        "signal_value": "$285K",
        "display_text": "equity",
        "confidence": 0.88,
        "timestamp": "2026-04-20T06:12:00Z",
    }
    out = redact_evidence_row(row)
    assert out["signal_value"] == "$285K"


# ---------------------------------------------------------------------------
# LenderRefResolver -- UC load happy path + fallback path + cache behavior.
# The resolver reads `mip.ref.lender_dictionary` via the Databricks SQL
# client, caches for 15 min, and falls back to `_LENDER_REF_MAP` on failure.
# Tests monkey-patch the internal `_load_from_uc` to avoid touching any
# real warehouse; the fallback dict is swappable via constructor.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_resolver_singleton() -> None:
    """Ensure each test starts with no process-wide resolver cached.

    `generalize_lender` lazily constructs a singleton; tests that stub a
    new resolver must be able to install it without leaking state between
    cases.
    """
    _reset_lender_resolver_for_tests(None)
    yield
    _reset_lender_resolver_for_tests(None)


def _resolver_with_uc_dict(
    uc_dict: dict[str, str] | None,
    fallback: dict[str, str] | None = None,
) -> LenderRefResolver:
    """Build a LenderRefResolver whose UC load is replaced with `uc_dict`.

    Pass `None` as `uc_dict` to simulate a UC outage (the resolver falls
    back to `fallback` or the default `_LENDER_REF_MAP`).
    """
    resolver = LenderRefResolver(ttl_s=60.0, fallback=fallback)
    resolver._load_from_uc = lambda: uc_dict  # type: ignore[method-assign]
    return resolver


def test_resolver_loads_from_uc_on_first_call() -> None:
    # UC carries a LARGER vocabulary than the fallback; we prove the
    # resolver prefers it over the in-process constant.
    uc_dict = {
        "WELLS FARGO BK NA": "Competitor B",
        "PENNYMAC LOAN SVCS LLC": "Competitor K",  # NOT in fallback
    }
    resolver = _resolver_with_uc_dict(uc_dict)
    # Known-to-UC-only entry resolves.
    assert resolver.resolve("PENNYMAC LOAN SVCS LLC") == "Competitor K"
    # Known-to-both resolves to UC value (identical here).
    assert resolver.resolve("WELLS FARGO BK NA") == "Competitor B"


def test_resolver_falls_back_to_ref_map_when_uc_unavailable() -> None:
    # `_load_from_uc` returns None == "UC load failed"; resolver uses the
    # fallback dict (defaults to _LENDER_REF_MAP).
    resolver = _resolver_with_uc_dict(None)
    assert resolver.resolve("WELLS FARGO BK NA") == "Competitor B"
    assert resolver.resolve("SUMMIT MTG") == "Summit Mortgage"


def test_resolver_caches_uc_result_until_ttl() -> None:
    # Counting-load: prove the resolver does not re-call `_load_from_uc`
    # for every `resolve()` hit.
    call_count = {"n": 0}

    def counted_load() -> dict[str, str]:
        call_count["n"] += 1
        return {"WELLS FARGO BK NA": "Competitor B"}

    resolver = LenderRefResolver(ttl_s=60.0)
    resolver._load_from_uc = counted_load  # type: ignore[method-assign]
    for _ in range(5):
        assert resolver.resolve("WELLS FARGO BK NA") == "Competitor B"
    assert call_count["n"] == 1, "UC should be queried at most once within TTL"


def test_resolver_invalidate_forces_reload() -> None:
    call_count = {"n": 0}

    def counted_load() -> dict[str, str]:
        call_count["n"] += 1
        return {"WELLS FARGO BK NA": "Competitor B"}

    resolver = LenderRefResolver(ttl_s=60.0)
    resolver._load_from_uc = counted_load  # type: ignore[method-assign]
    resolver.resolve("WELLS FARGO BK NA")
    resolver.invalidate()
    resolver.resolve("WELLS FARGO BK NA")
    assert call_count["n"] == 2


def test_resolver_unknown_key_uses_public_safe_alias() -> None:
    resolver = _resolver_with_uc_dict({})  # empty UC dict; empty fallback
    resolver._fallback = {}  # type: ignore[attr-defined]
    resolver.invalidate()
    resolver._load_from_uc = lambda: {}  # type: ignore[method-assign]
    assert resolver.resolve("PENNYMAC LOAN SERVICES LLC") == "Competitor Other"


def test_resolver_handles_none_and_empty() -> None:
    resolver = _resolver_with_uc_dict({})
    assert resolver.resolve(None) is None
    assert resolver.resolve("") is None
    assert resolver.resolve("   ") is None


def test_generalize_lender_uses_process_resolver_when_set() -> None:
    # Install a stub resolver with a custom vocabulary; prove the module-
    # level `generalize_lender` delegates to it.
    stub = _resolver_with_uc_dict({"FICTIONAL BK NA": "Fictional Bank"})
    _reset_lender_resolver_for_tests(stub)
    assert generalize_lender("FICTIONAL BK NA") == "Fictional Bank"
    # Clean-up happens via autouse fixture.


def test_fallback_ref_map_still_covers_canonical_11() -> None:
    # The in-process fallback must not shrink below the canonical rows
    # (so dev/tests without UC creds still get the expected labels).
    assert _LENDER_REF_MAP["SUMMIT MTG"] == "Summit Mortgage"
    assert _LENDER_REF_MAP["SUMMIT MORTGAGE"] == "Summit Mortgage"
    assert len(_LENDER_REF_MAP) >= 14
