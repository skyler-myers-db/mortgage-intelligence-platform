"""Mismatch detection for the borrower E2E audit: raw vs silver, raw vs gold, gold vs API.

Split out of ``tools/e2e_borrower_audit.py`` (2026-09-08); every comparator moved
verbatim.
"""

from __future__ import annotations

import math
from typing import Any

from tools.e2e_borrower_audit_model import ClipAudit, Mismatch
from tools.e2e_borrower_audit_recompute import _safe_float, _safe_int

# ---------------------------------------------------------------------------
# Mismatch detection
# ---------------------------------------------------------------------------


def _equal_signed_int(a: Any, b: Any) -> bool:
    """Tolerant int compare that treats None and 0 as distinct.

    We do NOT absorb None/0 into each other -- a missing derived column
    IS a bug, not a soft fail.
    """
    if a is None and b is None:
        return True
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return a == b


def _equal_list_as_set(a: Any, b: Any) -> bool:
    la = list(a or [])
    lb = list(b or [])
    return sorted(la) == sorted(lb)


def compare_raw_vs_silver(audit: ClipAudit) -> list[Mismatch]:
    """Compare the raw-share projection against silver.

    Silver is the authoritative input to gold. Raw is ONLY checked so
    we can report when silver has drifted from the current share
    snapshot (e.g. stale daily merge, or a source-side column-type
    change that broke a coercion). A raw-vs-silver drift is NOT a gold
    arithmetic bug -- it's an ingestion freshness / coercion issue -
    but the audit surfaces it separately so a data engineer can see it
    in the same report.
    """
    raw = audit.raw_inputs or {}
    raw_lien = raw.get("lien") or {}
    raw_prop = raw.get("property") or {}
    silver_lc = raw.get("silver_lien") or {}
    silver_pm = raw.get("silver_property") or {}

    mismatches: list[Mismatch] = []

    def _add(field_: str, expected: Any, actual: Any, notes: str = "") -> None:
        # Normalize numerics for comparison.
        if isinstance(expected, float) or isinstance(actual, float):
            try:
                eq = math.isclose(float(expected or 0.0), float(actual or 0.0), abs_tol=1e-6)
            except (TypeError, ValueError):
                eq = expected == actual
        elif isinstance(expected, bool) or isinstance(actual, bool):
            eq = bool(expected) == bool(actual)
        elif isinstance(expected, int) or isinstance(actual, int):
            eq = _equal_signed_int(expected, actual)
        else:
            eq = (expected or "") == (actual or "")
        if not eq:
            mismatches.append(Mismatch(
                clip=audit.clip, borrower_id=audit.borrower_id,
                surface_a="raw", surface_b="silver",
                field=field_, expected=expected, actual=actual, notes=notes,
            ))

    # Raw lien AVM vs silver.lien_current.avm_value.
    _add("avm_value",
         _safe_int(raw_lien.get("avm_value")),
         _safe_int(silver_lc.get("avm_value")))
    _add("total_open_lien_balance",
         _safe_int(raw_lien.get("total_open_lien_balance")),
         _safe_int(silver_lc.get("total_open_lien_balance")))
    _add("first_pos_rate",
         raw_lien.get("first_pos_rate"),
         silver_lc.get("first_pos_rate"))
    _add("estimated_cltv",
         raw_lien.get("estimated_cltv"),
         silver_lc.get("estimated_cltv"))
    _add("first_pos_loan_type",
         raw_lien.get("first_pos_loan_type"),
         silver_lc.get("first_pos_loan_type"))

    # owner corporate indicator: raw 'Y'/'N' vs silver BOOLEAN.
    raw_ci = (raw_prop.get("owner_corporate_indicator_raw") or "").strip().upper()
    raw_corporate = raw_ci == "Y"  # empty/N/NULL -> False
    _add("owner_is_corporate",
         raw_corporate,
         bool(silver_pm.get("owner_is_corporate")),
         notes="raw share has STRING ('Y'/'N'); silver normalizes with UPPER(TRIM(...)) = 'Y'")

    # is_absentee: recompute from raw property mailing_state.
    # (Fetched separately -- not projected above; keep this check soft.)

    return mismatches


def compare_raw_vs_gold(audit: ClipAudit) -> list[Mismatch]:
    """Compare Python-recomputed values against the gold row.

    Evidence sub-score and opportunity/confidence scores depend on the
    evidence-event count, which we patch into the recomputed bundle
    before calling this function -- so by this point raw_recomputed
    reflects the same inputs gold consumed.
    """
    rec = audit.raw_recomputed
    gold = audit.gold
    mismatches: list[Mismatch] = []

    def _add(field_: str, expected: Any, actual: Any, notes: str = "") -> None:
        if isinstance(expected, list) or isinstance(actual, list):
            eq = _equal_list_as_set(expected, actual)
        elif isinstance(expected, bool) or isinstance(actual, bool):
            eq = bool(expected) == bool(actual)
        elif isinstance(expected, int) or isinstance(actual, int):
            eq = _equal_signed_int(expected, actual)
        elif isinstance(expected, float) or isinstance(actual, float):
            try:
                eq = math.isclose(float(expected or 0.0), float(actual or 0.0), abs_tol=1e-6)
            except (TypeError, ValueError):
                eq = expected == actual
        else:
            eq = (expected or "") == (actual or "")
        if not eq:
            mismatches.append(
                Mismatch(
                    clip=audit.clip,
                    borrower_id=audit.borrower_id,
                    surface_a="raw_recomputed",
                    surface_b="gold",
                    field=field_,
                    expected=expected,
                    actual=actual,
                    notes=notes,
                )
            )

    # Direct arithmetic.
    _add("avm_value", rec["avm_value"], _safe_int(gold.get("avm_value")))
    _add("current_lien_balance", rec["current_lien_balance"], _safe_int(gold.get("current_lien_balance")))
    _add("rate_spread_bps", rec["rate_spread_bps"], _safe_int(gold.get("rate_spread_bps")))
    _add("equity_pct", rec["equity_pct"], _safe_int(gold.get("equity_pct")))
    _add("equity_estimate", rec["equity_estimate"], _safe_int(gold.get("equity_estimate")))
    _add("ltv", rec["ltv"], _safe_int(gold.get("ltv")))
    _add("current_rate", rec["current_rate"], _safe_float(gold.get("current_rate")))

    # Booleans.
    _add("is_owner_occupied", rec["is_owner_occupied"], bool(gold.get("is_owner_occupied")))
    _add("is_absentee", rec["is_absentee"], bool(gold.get("is_absentee")))
    _add("is_corporate_owner", rec["is_corporate_owner"], bool(gold.get("is_corporate_owner")))
    _add("is_investor", rec["is_investor"], bool(gold.get("is_investor")))
    _add("is_current_customer", rec["is_current_customer"], bool(gold.get("is_current_customer")))
    _add("is_competitor_lien", rec["is_competitor_lien"], bool(gold.get("is_competitor_lien")))
    _add("in_the_money", rec["in_the_money"], bool(gold.get("in_the_money")))
    _add("has_permit", rec["has_permit"], bool(gold.get("has_permit")))
    _add("listed_for_sale", rec["listed_for_sale"], bool(gold.get("listed_for_sale")))
    _add(
        "has_heloc_propensity_trigger",
        rec["has_heloc_propensity_trigger"],
        bool(gold.get("has_heloc_propensity_trigger")),
    )
    _add(
        "has_refi_propensity_trigger",
        rec["has_refi_propensity_trigger"],
        bool(gold.get("has_refi_propensity_trigger")),
    )

    # Listing and propensity source attributes.
    _add("listing_status_category", rec["listing_status_category"], gold.get("listing_status_category"))
    _add("listing_status_description", rec["listing_status_description"], gold.get("listing_status_description"))
    _add("listing_date", rec["listing_date"], gold.get("listing_date"))
    _add("listing_status_date", rec["listing_status_date"], gold.get("listing_status_date"))
    _add(
        "listing_price",
        rec["listing_price"],
        None if gold.get("listing_price") is None else _safe_int(gold.get("listing_price")),
    )
    _add(
        "listing_days_on_market",
        rec["listing_days_on_market"],
        None if gold.get("listing_days_on_market") is None else _safe_int(gold.get("listing_days_on_market")),
    )
    _add("listing_service", rec["listing_service"], gold.get("listing_service"))
    _add(
        "heloc_propensity_score",
        rec["heloc_propensity_score"],
        None if gold.get("heloc_propensity_score") is None else _safe_int(gold.get("heloc_propensity_score")),
    )
    _add("heloc_propensity_run_date", rec["heloc_propensity_run_date"], gold.get("heloc_propensity_run_date"))
    _add(
        "refi_propensity_score",
        rec["refi_propensity_score"],
        None if gold.get("refi_propensity_score") is None else _safe_int(gold.get("refi_propensity_score")),
    )
    _add("refi_propensity_run_date", rec["refi_propensity_run_date"], gold.get("refi_propensity_run_date"))

    # Derived strings / labels.
    _add("recommended_offer_code", rec["recommended_offer_code"], gold.get("recommended_offer_code"))
    _add("recommended_offer", rec["recommended_offer"], gold.get("recommended_offer"))

    # Segment codes: set compare, ordering is gold-side only.
    _add("segment_codes", rec["segment_codes"], gold.get("segment_codes"))

    # Scores (depend on evidence_count patched in).
    _add("opportunity_score", rec["opportunity_score"], _safe_int(gold.get("opportunity_score")))
    _add("confidence", rec["confidence"], _safe_int(gold.get("confidence")))

    # Sanity: display LTV can exceed 100 for underwater borrowers. Equity_pct
    # stays in [0, 100] because it feeds scoring and offer gating.
    ltv_g = _safe_int(gold.get("ltv"))
    eq_g = _safe_int(gold.get("equity_pct"))
    if ltv_g < 0:
        mismatches.append(Mismatch(
            clip=audit.clip, borrower_id=audit.borrower_id,
            surface_a="raw_recomputed", surface_b="gold",
            field="ltv_range", expected=">=0", actual=ltv_g,
            notes="gold display ltv outside valid range",
        ))
    if not (0 <= eq_g <= 100):
        mismatches.append(Mismatch(
            clip=audit.clip, borrower_id=audit.borrower_id,
            surface_a="raw_recomputed", surface_b="gold",
            field="equity_pct_range", expected="[0..100]", actual=eq_g,
            notes="gold equity_pct outside valid range",
        ))

    return mismatches


def compare_gold_vs_api(audit: ClipAudit) -> list[Mismatch]:
    """Compare gold row vs ``/api/borrowers`` payload (post-redaction).

    The boundary projection drops some gold columns (owner_name_hash,
    trigger_timeline_json) and renames ``clip`` -> ``clip_id``; this
    check validates the numeric / categorical fields survive the
    boundary unchanged.
    """
    g = audit.gold
    a = audit.api
    m: list[Mismatch] = []

    def _add(field_: str, expected: Any, actual: Any) -> None:
        if isinstance(expected, list) or isinstance(actual, list):
            eq = _equal_list_as_set(expected, actual)
        elif isinstance(expected, bool) or isinstance(actual, bool):
            eq = bool(expected) == bool(actual)
        elif isinstance(expected, int) or isinstance(actual, int):
            eq = _equal_signed_int(expected, actual)
        elif isinstance(expected, float) or isinstance(actual, float):
            try:
                eq = math.isclose(float(expected or 0.0), float(actual or 0.0), abs_tol=1e-6)
            except (TypeError, ValueError):
                eq = expected == actual
        else:
            eq = (expected or "") == (actual or "")
        if not eq:
            m.append(Mismatch(
                clip=audit.clip, borrower_id=audit.borrower_id,
                surface_a="gold", surface_b="api",
                field=field_, expected=expected, actual=actual,
            ))

    _add("borrower_id",        g.get("borrower_id"),                a.get("borrower_id"))
    _add("city",               g.get("city"),                       a.get("city"))
    _add("state",              g.get("state"),                      a.get("state"))
    # zip: gold passes through silver's `situs_zip_code` (may be ZIP+4);
    # api preserves that verbatim -- so the gold vs api comparison is
    # pure passthrough.
    _add("zip",                g.get("zip"),                        a.get("zip"))
    _add("segment_codes",      g.get("segment_codes") or [],        a.get("segment_codes") or [])
    _add("equity_estimate",    _safe_int(g.get("equity_estimate")), _safe_int(a.get("equity_estimate")))
    _add("rate_spread_bps",    _safe_int(g.get("rate_spread_bps")), _safe_int(a.get("rate_spread_bps")))
    _add("opportunity_score",  _safe_int(g.get("opportunity_score")), _safe_int(a.get("opportunity_score")))
    _add("confidence",         _safe_int(g.get("confidence")),      _safe_int(a.get("confidence")))
    _add("recommended_offer",  g.get("recommended_offer"),          a.get("recommended_offer"))
    _add("why_now",            g.get("why_now"),                    a.get("why_now"))
    _add("approval_status",    g.get("approval_status"),            a.get("approval_status"))
    _add("subject_property",   g.get("subject_property"),           a.get("subject_property"))
    _add("avm_value",          _safe_int(g.get("avm_value")),       _safe_int(a.get("avm_value")))
    _add("current_lien_balance", _safe_int(g.get("current_lien_balance")),
         _safe_int(a.get("current_lien_balance")))
    _add("current_rate",       _safe_float(g.get("current_rate")),  _safe_float(a.get("current_rate")))
    _add("ltv",                _safe_int(g.get("ltv")),             _safe_int(a.get("ltv")))
    _add("related_property_count",
         _safe_int(g.get("related_property_count")),
         _safe_int(a.get("related_property_count")))
    # owner_link_id: gold BIGINT, api stringified; compare stringified.
    _add("owner_link_id",
         "" if g.get("owner_link_id") in (None, 0) else str(g.get("owner_link_id")),
         a.get("owner_link_id") or "")

    # PII leak check: api payload must NOT expose raw owner hash or
    # any raw PII column.
    for forbidden in (
        "owner_name_hash", "owner_name_hash_raw",
        "owner_1_full_name", "situs_street_address",
        "mailing_street_address", "trigger_timeline_json",
    ):
        if forbidden in a:
            m.append(Mismatch(
                clip=audit.clip, borrower_id=audit.borrower_id,
                surface_a="gold", surface_b="api",
                field=f"pii_leak:{forbidden}",
                expected="(absent)", actual=a.get(forbidden),
                notes="forbidden key present in api payload",
            ))

    return m
