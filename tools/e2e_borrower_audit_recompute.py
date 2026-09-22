"""Independent Python re-computation of the gold borrower row from raw inputs.

Split out of ``tools/e2e_borrower_audit.py`` (2026-09-08); mirrors
``sql/transformations/gold_borrower_360.sql`` through the canonical scoring
primitives in ``backend.services.scoring``. Every function moved verbatim.
"""

from __future__ import annotations

from typing import Any

from backend.services.scoring import (
    NBO_PRODUCT_LABELS,
    in_the_money,
    lead_score,
    next_best_offer,
    rate_spread_bps,
)
from tools.e2e_borrower_audit_model import (
    DEFAULT_CASHOUT_EQUITY_MIN,
    DEFAULT_HELOC_EQUITY_MIN,
    DEFAULT_MIN_EQUITY_PCT,
    DEFAULT_MIN_SPREAD_BPS,
    DEFAULT_RETENTION_MIN_SPREAD,
)

# ---------------------------------------------------------------------------
# Independent Python re-computation (mirrors gold_borrower_360.sql)
# ---------------------------------------------------------------------------


def _safe_int(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def recompute_from_raw(
    raw: dict[str, Any],
    *,
    market_rate_fraction: float,
    related_property_count: int,
) -> dict[str, Any]:
    """Recompute every derived gold.borrower_360 column from silver inputs.

    The gold CTAS joins `mip.silver.lien_current` + `mip.silver.
    property_master`, so the *independent* recompute uses silver as the
    authoritative input. Silver itself is a 1:1 lift of the raw share
    (with deterministic coercions); any silver-vs-raw divergence is
    tracked as a separate "raw_vs_silver" drift class.

    Any divergence between this recompute and gold.borrower_360 is a
    bug in either the SQL CTAS or this Python.
    """
    silver_lc = raw.get("silver_lien") or {}
    silver_pm = raw.get("silver_property") or {}
    silver_listing = raw.get("silver_listing") or {}
    silver_heloc = raw.get("silver_heloc_propensity") or {}
    silver_refi = raw.get("silver_refi_propensity") or {}

    avm_value = _safe_int(silver_lc.get("avm_value"))
    lien_bal = _safe_int(silver_lc.get("total_open_lien_balance"))
    estimated_cltv = silver_lc.get("estimated_cltv")
    first_pos_rate = silver_lc.get("first_pos_rate")  # fractional or None
    loan_type = silver_lc.get("first_pos_loan_type")
    lender_current = (silver_lc.get("first_pos_lender_current") or "").strip()
    second_pos_amount = silver_lc.get("second_pos_amount")

    owner_occupancy = (silver_lc.get("owner_occupancy_code") or "").strip()
    is_owner_occupied = owner_occupancy == "O"
    is_absentee = bool(silver_pm.get("is_absentee"))
    is_corporate_owner = bool(silver_pm.get("owner_is_corporate"))
    bedrooms = _safe_int(silver_pm.get("bedrooms"))
    bathrooms = _safe_float(silver_pm.get("bathrooms"))

    # rate_spread_bps -- canonical primitive (banker's rounding).
    spread = rate_spread_bps(
        _safe_float(first_pos_rate) if first_pos_rate is not None else None,
        market_rate_fraction,
    )

    # equity_pct -- prefer Cotality-computed CLTV, fallback to avm math.
    # Mirrors the CTAS CASE statement including the clip to [0, 100].
    if estimated_cltv is not None and float(estimated_cltv) > 0:
        equity_pct_raw = 100.0 - float(estimated_cltv)
        equity_pct = int(max(0, min(100, round(equity_pct_raw))))
    elif avm_value and avm_value > 0:
        equity_pct_raw = 100.0 * (avm_value - lien_bal) / avm_value
        equity_pct = int(max(0, min(100, round(equity_pct_raw))))
    else:
        equity_pct = 0

    # equity_estimate = GREATEST(0, avm - liens).
    equity_estimate = max(0, avm_value - lien_bal)

    # ltv -- display truth. Mirror gold CTAS source preference: estimated
    # CLTV first, lien/AVM fallback. Do not upper-cap; underwater borrowers
    # can exceed 100 while equity_pct stays clamped for scoring.
    if estimated_cltv is not None and float(estimated_cltv) > 0:
        ltv = int(max(0, round(float(estimated_cltv))))
    elif avm_value and avm_value > 0:
        ltv = int(max(0, round(100.0 * lien_bal / avm_value)))
    else:
        ltv = 0

    listing_price_raw = (
        _safe_int(silver_listing.get("listing_price"))
        if silver_listing.get("listing_price") is not None
        else None
    )
    if (
        listing_price_raw is None
        or listing_price_raw < 25_000
        or (avm_value and avm_value > 0 and (
            listing_price_raw < avm_value * 0.15
            or listing_price_raw > avm_value * 5.0
        ))
    ):
        listing_price = None
    else:
        listing_price = listing_price_raw

    # Boolean features.
    is_investor = (
        (related_property_count >= 2)
        or is_corporate_owner
        or is_absentee
    )
    is_current_customer = (
        lender_current != "" and "SUMMIT" in lender_current.upper()
    )
    is_competitor_lien = (
        lender_current != "" and "SUMMIT" not in lender_current.upper()
    )
    has_permit = False
    listed_for_sale = bool(silver_listing.get("is_active_listing"))
    heloc_propensity_score = silver_heloc.get("heloc_propensity_score")
    refi_propensity_score = silver_refi.get("refi_propensity_score")
    heloc_score = _safe_int(heloc_propensity_score)
    refi_score = _safe_int(refi_propensity_score)
    has_heloc_propensity_trigger = heloc_propensity_score is not None and heloc_score >= 700
    has_refi_propensity_trigger = refi_propensity_score is not None and refi_score >= 700
    has_heloc_intent = has_permit or has_heloc_propensity_trigger

    # In the Money.
    itm = in_the_money(spread, equity_pct, DEFAULT_MIN_SPREAD_BPS, DEFAULT_MIN_EQUITY_PCT)

    # Next-best offer.
    offer_code = next_best_offer(
        spread,
        equity_pct,
        has_heloc_intent,
        listed_for_sale,
        is_investor,
        is_current_customer,
        is_competitor_lien,
        DEFAULT_MIN_SPREAD_BPS,
        DEFAULT_MIN_EQUITY_PCT,
        DEFAULT_HELOC_EQUITY_MIN,
        DEFAULT_CASHOUT_EQUITY_MIN,
        DEFAULT_RETENTION_MIN_SPREAD,
    )

    # Segment codes -- mirror gold CTAS ARRAY filter.
    segments: list[str] = []
    if itm:
        segments.append("itm")
    if listed_for_sale:
        segments.append("listed")
    if has_heloc_intent:
        segments.append("permit")
    if is_investor:
        segments.append("investor")
    if equity_pct >= 35 and second_pos_amount is None:
        segments.append("equity")
    if is_current_customer and (spread >= 50 or is_competitor_lien or listed_for_sale):
        segments.append("retention")

    # Sub-scores -- copied exactly from gold_borrower_360.sql.
    if spread >= 200 and equity_pct >= 35:
        economic_incentive = 98
    elif spread >= 150 and equity_pct >= 35:
        economic_incentive = 92
    elif spread >= 100 and equity_pct >= 25:
        economic_incentive = 85
    elif spread >= 75 and equity_pct >= 15:
        economic_incentive = 75
    elif spread >= 0 and equity_pct >= 25:
        economic_incentive = 55
    elif equity_pct >= 25:
        economic_incentive = 48
    else:
        economic_incentive = 30

    intent_trigger = min(
        100,
        15 * (1 if is_competitor_lien else 0)
        + min(20, related_property_count * 4)
        + (20 if spread >= DEFAULT_MIN_SPREAD_BPS else 0)
        + (15 if equity_pct >= 35 else 0)
        + (10 if is_current_customer else 0)
        + (18 if listed_for_sale else 0)
        + (min(18, round(heloc_score / 50.0)) if has_heloc_propensity_trigger else 0)
        + (min(12, round(refi_score / 85.0)) if has_refi_propensity_trigger else 0),
    )

    if is_owner_occupied and loan_type in ("CONV", "FHA", "VA"):
        fit_raw = 85 - (55 - min(55, bedrooms * 10 + int(bathrooms) * 5))
    elif is_owner_occupied:
        fit_raw = 75
    elif is_corporate_owner:
        fit_raw = 65
    else:
        fit_raw = 58
    fit_value = fit_raw

    if is_current_customer:
        relationship = 88
    elif is_competitor_lien:
        relationship = 60
    else:
        relationship = 45

    # evidence_count is fetched separately by the caller; pass zero
    # here and patch in. (We don't re-derive evidence_events from raw
    # share in this audit -- gold.evidence_events has its own UNION
    # which is out of scope for this pass.)
    evidence = 0

    opportunity_score = lead_score(
        economic_incentive, intent_trigger, fit_value, relationship, evidence
    )
    confidence = int(
        round(
            (economic_incentive + intent_trigger + fit_value + relationship + evidence)
            / 5.0
        )
    )

    recommended_offer = NBO_PRODUCT_LABELS.get(offer_code, NBO_PRODUCT_LABELS["nurture"])

    return {
        "avm_value": avm_value,
        "current_lien_balance": lien_bal,
        "first_pos_rate_fraction": first_pos_rate,
        "current_rate": float(first_pos_rate) * 100 if first_pos_rate else 0.0,
        "estimated_cltv": estimated_cltv,
        "rate_spread_bps": spread,
        "equity_pct": equity_pct,
        "equity_estimate": equity_estimate,
        "ltv": ltv,
        "is_owner_occupied": is_owner_occupied,
        "is_absentee": is_absentee,
        "is_corporate_owner": is_corporate_owner,
        "is_investor": is_investor,
        "is_current_customer": is_current_customer,
        "is_competitor_lien": is_competitor_lien,
        "has_permit": has_permit,
        "listed_for_sale": listed_for_sale,
        "listing_status_category": silver_listing.get("listing_status_category"),
        "listing_status_description": silver_listing.get("listing_status_description"),
        "listing_date": silver_listing.get("listing_date"),
        "listing_status_date": silver_listing.get("listing_status_date"),
        "listing_price": listing_price,
        "listing_days_on_market": _safe_int(silver_listing.get("listing_days_on_market")) if silver_listing.get("listing_days_on_market") is not None else None,
        "listing_service": silver_listing.get("listing_service"),
        "heloc_propensity_score": heloc_score if heloc_propensity_score is not None else None,
        "heloc_propensity_run_date": silver_heloc.get("heloc_propensity_run_date"),
        "has_heloc_propensity_trigger": has_heloc_propensity_trigger,
        "refi_propensity_score": refi_score if refi_propensity_score is not None else None,
        "refi_propensity_run_date": silver_refi.get("refi_propensity_run_date"),
        "has_refi_propensity_trigger": has_refi_propensity_trigger,
        "in_the_money": itm,
        "recommended_offer_code": offer_code,
        "recommended_offer": recommended_offer,
        "segment_codes": segments,
        "economic_incentive": economic_incentive,
        "intent_trigger": intent_trigger,
        "fit": fit_value,
        "relationship": relationship,
        "evidence_sub": evidence,
        "opportunity_score": opportunity_score,
        "confidence": confidence,
        "second_pos_amount": second_pos_amount,
        "first_pos_loan_type": loan_type,
        "related_property_count": related_property_count,
    }
