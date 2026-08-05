"""Shared Databricks repository projections and row-shaping helpers.

The concrete repositories import these names, while ``databricks_repo.py``
continues to re-export them for compatibility with existing schema-guard
tests. Keep every projection explicit; these constants are part of the PII
boundary and must never drift into ``SELECT *``.
"""
from __future__ import annotations

import json
from typing import Any

from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import DimensionFacetCount
from backend.services.pii_redaction import redact_evidence_row

_BORROWER_360_COLUMNS: str = (
    "clip, borrower_id, owner_name_hash, city, state, zip, segment_codes, "
    "equity_estimate, equity_pct, rate_spread_bps, market_rate_fraction, "
    "opportunity_score, confidence, recommended_offer_code, recommended_offer, "
    "why_now, evidence_ids, approval_status, owner_link_id, subject_property, "
    "avm_value, current_lien_balance, current_lien_balance_low, "
    "current_lien_balance_high, current_rate, ltv, related_property_count, "
    "owner_count, has_unresolved_owner, primary_owner_entity_type, "
    "situs_cbsa_code, first_pos_loan_type, loan_product_type, origination_channel, "
    "is_owner_occupied, is_absentee, is_corporate_owner, is_investor, "
    "is_current_customer, is_former_customer, "
    "is_competitor_lien, has_permit, listed_for_sale, listing_status_category, "
    "listing_status_description, listing_date, listing_status_date, listing_price, "
    "listing_days_on_market, listing_service, heloc_propensity_score, "
    "heloc_propensity_run_date, has_heloc_propensity_trigger, refi_propensity_score, "
    "refi_propensity_run_date, has_refi_propensity_trigger, second_pos_amount, "
    "has_first_party_relationship, first_party_relationship_depth, "
    "first_party_recent_interactions, first_party_recent_application, "
    "first_party_synthetic_demo, "
    "marketing_eligible, consent_status, suppression_reason, last_touch_at, "
    "eligible_recontact_at, dnc, eligibility_source, "
    "min_spread_bps_applied, min_equity_pct_applied, in_the_money, "
    "current_lender_ref"
)

_BORROWER_DOSSIER_COLUMNS: str = (
    _BORROWER_360_COLUMNS
    + ", trigger_timeline_json, evidence_events, trigger_timeline, refreshed_at"
)

_LEAD_POPULATION_COLUMNS: str = (
    "clip, borrower_id, display_name, city, state, zip, segment_codes, "
    "equity_estimate, rate_spread_bps, opportunity_score, confidence, "
    "recommended_offer_code, recommended_offer, why_now, evidence_ids, approval_status, "
    "current_lender_ref, "
    "is_owner_occupied, is_investor, is_current_customer, "
    "is_former_customer, is_competitor_lien, related_property_count, "
    "owner_count, has_unresolved_owner, primary_owner_entity_type, "
    "current_lien_balance, second_pos_amount, has_permit, listed_for_sale, "
    "listing_status_category, listing_status_description, listing_date, "
    "listing_status_date, listing_price, listing_days_on_market, listing_service, "
    "heloc_propensity_score, heloc_propensity_run_date, has_heloc_propensity_trigger, "
    "refi_propensity_score, refi_propensity_run_date, has_refi_propensity_trigger, "
    "loan_product_type, origination_channel, "
    "marketing_eligible, consent_status, suppression_reason, last_touch_at, "
    "eligible_recontact_at, dnc, eligibility_source"
)

_LEAD_POPULATION_SELECT_FROM_LP: str = (
    "lp.clip, lp.borrower_id, lp.display_name, lp.city, lp.state, lp.zip, lp.segment_codes, "
    "lp.equity_estimate, lp.rate_spread_bps, lp.opportunity_score, lp.confidence, "
    "lp.recommended_offer_code, lp.recommended_offer, lp.why_now, lp.evidence_ids, "
    "COALESCE(ls.approval_status, lp.approval_status, 'pending') AS approval_status, "
    "COALESCE(ls.outreach_status, 'none') AS outreach_status, "
    "ls.approved_at, ls.outreach_at, "
    "lp.current_lender_ref, "
    "lp.is_owner_occupied, lp.is_investor, lp.is_current_customer, "
    "lp.is_former_customer, lp.is_competitor_lien, lp.related_property_count, "
    "lp.owner_count, lp.has_unresolved_owner, lp.primary_owner_entity_type, "
    "lp.current_lien_balance, lp.second_pos_amount, lp.has_permit, lp.listed_for_sale, "
    "lp.listing_status_category, lp.listing_status_description, lp.listing_date, "
    "lp.listing_status_date, lp.listing_price, lp.listing_days_on_market, lp.listing_service, "
    "lp.heloc_propensity_score, lp.heloc_propensity_run_date, lp.has_heloc_propensity_trigger, "
    "lp.refi_propensity_score, lp.refi_propensity_run_date, lp.has_refi_propensity_trigger, "
    "lp.loan_product_type, lp.origination_channel, "
    "lp.marketing_eligible, lp.consent_status, lp.suppression_reason, lp.last_touch_at, "
    "lp.eligible_recontact_at, lp.dnc, lp.eligibility_source"
)

_LEAD_POPULATION_SELECT_FROM_B360: str = (
    "b.clip, b.borrower_id, "
    "CONCAT('Owner ', SUBSTR(b.owner_name_hash, 1, 8)) AS display_name, "
    "b.city, b.state, b.zip, b.segment_codes, "
    "b.equity_estimate, b.rate_spread_bps, b.opportunity_score, b.confidence, "
    "b.recommended_offer_code, b.recommended_offer, b.why_now, b.evidence_ids, "
    "COALESCE(ls.approval_status, b.approval_status, 'pending') AS approval_status, "
    "COALESCE(ls.outreach_status, 'none') AS outreach_status, "
    "ls.approved_at, ls.outreach_at, "
    "b.current_lender_ref, "
    "b.is_owner_occupied, b.is_investor, b.is_current_customer, "
    "b.is_former_customer, b.is_competitor_lien, b.related_property_count, "
    "b.owner_count, b.has_unresolved_owner, b.primary_owner_entity_type, "
    "b.current_lien_balance, b.second_pos_amount, b.has_permit, b.listed_for_sale, "
    "b.listing_status_category, b.listing_status_description, b.listing_date, "
    "b.listing_status_date, b.listing_price, b.listing_days_on_market, b.listing_service, "
    "b.heloc_propensity_score, b.heloc_propensity_run_date, b.has_heloc_propensity_trigger, "
    "b.refi_propensity_score, b.refi_propensity_run_date, b.has_refi_propensity_trigger, "
    "b.loan_product_type, b.origination_channel, "
    "b.marketing_eligible, b.consent_status, b.suppression_reason, b.last_touch_at, "
    "b.eligible_recontact_at, b.dnc, b.eligibility_source"
)

_EVIDENCE_COLUMNS: str = (
    "evidence_id, source_product, source_table, signal_type, signal_value, "
    "display_text, confidence, `timestamp`, signal_rank"
)

_SEGMENT_COLUMNS: str = (
    "segment_code, name, count, delta_vs_prior, avg_score, description, color, "
    "loan_product_mix, origination_channel_mix"
)


def _parse_timeline(raw: Any) -> list[EvidenceEvent]:
    """Parse the ``trigger_timeline_json`` ARRAY<STRUCT> payload."""
    if raw is None:
        return []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, str):
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        return []
    events: list[EvidenceEvent] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        events.append(
            EvidenceEvent(
                evidence_id=r.get("evidence_id") or "",
                source_product=r.get("source_product") or "",
                source_table=r.get("source_table") or "",
                signal_type=r.get("signal_type") or "",
                signal_value=r.get("signal_value") or "",
                display_text=r.get("display_text") or "",
                confidence=float(r.get("confidence") or 0.0),
                timestamp=str(r.get("timestamp") or ""),
            )
        )
    return events


def _redact_evidence_list(raw: Any) -> list[EvidenceEvent]:
    """Redact + hydrate an ARRAY<STRUCT<...>> evidence column."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    events: list[EvidenceEvent] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        events.append(EvidenceEvent(**redact_evidence_row(r)))
    return events


def _parse_facet_mix(raw: Any) -> list[DimensionFacetCount]:
    """Parse an ARRAY<STRUCT<value, count>> facet-mix column (S1.6).

    The warehouse client returns Delta struct arrays as list-of-dicts;
    defensively accept a JSON string too (older client paths stringify
    complex columns). Malformed cells drop to [] -- a missing facet mix
    must never fail the whole segments read.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    cells: list[DimensionFacetCount] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        value = str(r.get("value") or "").strip()
        if not value:
            continue
        try:
            count = max(0, int(r.get("count") or 0))
        except (TypeError, ValueError):
            continue
        cells.append(DimensionFacetCount(value=value, count=count))
    return cells


def _coerce_bool(value: Any) -> bool:
    """Defensively coerce warehouse boolean-ish values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "t", "yes")
