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

from backend.services.pii_redaction import (
    _FORBIDDEN_OUTPUT_KEYS,
    _LENDER_REF_MAP,
    LenderRefResolver,
    _reset_lender_resolver_for_tests,
    generalize_lender,
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
        ("UNITED WHOLESALE MTG", "United Wholesale Mortgage"),
        ("WELLS FARGO BK NA", "Wells Fargo Bank"),
        ("JPMORGAN CHASE BK NA", "JPMorgan Chase"),
        ("ROCKET MTG LLC", "Rocket Mortgage"),
        ("QUICKEN LNS", "Quicken Loans"),
        ("BANK OF AMERICA NA", "Bank of America"),
        ("GUARANTEED RATE INC", "Guaranteed Rate"),
        ("LOANDEPOT.COM LLC", "loanDepot"),
        ("CALIBER HM LOANS INC", "Caliber Home Loans"),
        ("FAIRWAY INDEPENDENT MTG CORP", "Fairway Independent Mortgage"),
        ("SUMMIT MTG", "Summit Mortgage"),
        # Case-insensitive lookup
        ("wells fargo bk na", "Wells Fargo Bank"),
        # Whitespace handling
        ("  WELLS FARGO BK NA  ", "Wells Fargo Bank"),
    ],
)
def test_generalize_lender_known_vocabulary(raw: str, expected: str) -> None:
    assert generalize_lender(raw) == expected


def test_generalize_lender_unknown_is_title_cased() -> None:
    # "PENNYMAC LOAN SERVICES LLC" isn't in the vocabulary -- fall back
    # to title-case.
    assert generalize_lender("PENNYMAC LOAN SERVICES LLC") == "Pennymac Loan Services Llc"


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
        # Forbidden raw-PII columns we expect the redactor to strip --
        # these MUST NOT appear in the output.
        "owner_1_full_name": "Jane Q Public",
        "situs_street_address": "123 Elm St",
        "mailing_street_address": "PO Box 9",
        "trigger_timeline_json": '[{"evidence_id":"ev-001"}]',
    }
    row.update(overrides)
    return row


def test_redact_borrower_row_strips_forbidden_keys() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    leaks = _FORBIDDEN_OUTPUT_KEYS.intersection(out.keys())
    assert not leaks, f"forbidden keys leaked: {leaks}"


def test_redact_borrower_row_renames_clip_to_clip_id() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert out["clip_id"] == "clip-abc-123"
    assert "clip" not in out


def test_redact_borrower_row_synthesizes_display_name() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert out["display_name"] == "Owner abcd1234"


def test_redact_borrower_row_synthesizes_subject_property() -> None:
    out = redact_borrower_row(_sample_borrower_row())
    assert out["subject_property"] == "Synthetic property · Chicago, IL 60614"


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
        # Raw-PII columns the redactor must drop.
        "owner_1_full_name": "Jane Q Public",
        "situs_street_address": "123 Elm St",
    }


def test_redact_lead_row_strips_forbidden_keys() -> None:
    out = redact_lead_row(_sample_lead_row())
    leaks = _FORBIDDEN_OUTPUT_KEYS.intersection(out.keys())
    assert not leaks, f"forbidden keys leaked: {leaks}"


def test_redact_lead_row_prefers_hash_derived_display_name() -> None:
    row = _sample_lead_row()
    out = redact_lead_row(row)
    assert out["display_name"] == "Owner 0123abcd"


def test_redact_lead_row_falls_back_to_gold_display_name_when_no_hash() -> None:
    row = _sample_lead_row()
    row.pop("owner_name_hash")
    out = redact_lead_row(row)
    assert out["display_name"] == "Owner anon"


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
    assert out["signal_value"] == "Wells Fargo Bank"


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
        "WELLS FARGO BK NA": "Wells Fargo Bank",
        "PENNYMAC LOAN SVCS LLC": "PennyMac Loan Services",  # NOT in fallback
    }
    resolver = _resolver_with_uc_dict(uc_dict)
    # Known-to-UC-only entry resolves.
    assert resolver.resolve("PENNYMAC LOAN SVCS LLC") == "PennyMac Loan Services"
    # Known-to-both resolves to UC value (identical here).
    assert resolver.resolve("WELLS FARGO BK NA") == "Wells Fargo Bank"


def test_resolver_falls_back_to_ref_map_when_uc_unavailable() -> None:
    # `_load_from_uc` returns None == "UC load failed"; resolver uses the
    # fallback dict (defaults to _LENDER_REF_MAP).
    resolver = _resolver_with_uc_dict(None)
    assert resolver.resolve("WELLS FARGO BK NA") == "Wells Fargo Bank"
    assert resolver.resolve("SUMMIT MTG") == "Summit Mortgage"


def test_resolver_caches_uc_result_until_ttl() -> None:
    # Counting-load: prove the resolver does not re-call `_load_from_uc`
    # for every `resolve()` hit.
    call_count = {"n": 0}

    def counted_load() -> dict[str, str]:
        call_count["n"] += 1
        return {"WELLS FARGO BK NA": "Wells Fargo Bank"}

    resolver = LenderRefResolver(ttl_s=60.0)
    resolver._load_from_uc = counted_load  # type: ignore[method-assign]
    for _ in range(5):
        assert resolver.resolve("WELLS FARGO BK NA") == "Wells Fargo Bank"
    assert call_count["n"] == 1, "UC should be queried at most once within TTL"


def test_resolver_invalidate_forces_reload() -> None:
    call_count = {"n": 0}

    def counted_load() -> dict[str, str]:
        call_count["n"] += 1
        return {"WELLS FARGO BK NA": "Wells Fargo Bank"}

    resolver = LenderRefResolver(ttl_s=60.0)
    resolver._load_from_uc = counted_load  # type: ignore[method-assign]
    resolver.resolve("WELLS FARGO BK NA")
    resolver.invalidate()
    resolver.resolve("WELLS FARGO BK NA")
    assert call_count["n"] == 2


def test_resolver_unknown_key_titlecases() -> None:
    resolver = _resolver_with_uc_dict({})  # empty UC dict; empty fallback
    resolver._fallback = {}  # type: ignore[attr-defined]
    resolver.invalidate()
    resolver._load_from_uc = lambda: {}  # type: ignore[method-assign]
    assert resolver.resolve("PENNYMAC LOAN SERVICES LLC") == "Pennymac Loan Services Llc"


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
    # The in-process fallback must not shrink below the canonical 11 rows
    # (so dev/tests without UC creds still get the expected labels).
    assert _LENDER_REF_MAP["SUMMIT MTG"] == "Summit Mortgage"
    assert len(_LENDER_REF_MAP) >= 11
