from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.schemas.lead import Borrower360, LeadSummary
from backend.schemas.offer import OutreachApproveRequest, OutreachDraftRequest
from backend.schemas.workspace import SavedDraftInput, SavedLeadInput
from backend.services.repositories.databricks_repo import (
    _BORROWER_360_COLUMNS,
    _LEAD_POPULATION_COLUMNS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _lead_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "borrower_id": "B-12345",
        "display_name": "Owner abcd1234",
        "city": "Chicago",
        "state": "IL",
        "zip": "60614",
        "clip": "clip_ref_0123abcd4567",
        "segment_codes": ["itm"],
        "equity_estimate": 100000,
        "rate_spread_bps": 100,
        "opportunity_score": 80,
        "confidence": 80,
        "recommended_offer": "Refinance",
        "why_now": "Rate spread is positive.",
        "evidence_ids": ["ev-1"],
        "approval_status": "pending",
        "current_lender_ref": "Competitor B",
    }
    payload.update(overrides)
    return payload


def test_lead_summary_rejects_raw_display_name() -> None:
    with pytest.raises(ValidationError):
        LeadSummary.model_validate(_lead_payload(display_name="Jane Public"))


def test_lead_summary_rejects_raw_lender_name() -> None:
    with pytest.raises(ValidationError):
        LeadSummary.model_validate(_lead_payload(current_lender_ref="Wells Fargo Bank"))


def test_lead_summary_exposes_module0_relationship_flags() -> None:
    payload = _lead_payload(
        is_owner_occupied=False,
        is_investor=True,
        is_current_customer=False,
        is_former_customer=True,
        is_competitor_lien=False,
        has_permit=False,
        listed_for_sale=False,
        listing_status_category="A",
        listing_status_description="Active",
        listing_price=725000,
        listing_days_on_market=18,
        has_heloc_propensity_trigger=True,
        heloc_propensity_score=812,
        has_refi_propensity_trigger=True,
        refi_propensity_score=760,
        second_pos_amount=125000,
    )

    parsed = LeadSummary.model_validate(payload)

    assert parsed.is_owner_occupied is False
    assert parsed.is_investor is True
    assert parsed.is_current_customer is False
    assert parsed.is_former_customer is True
    assert parsed.is_competitor_lien is False
    assert parsed.has_permit is False
    assert parsed.listed_for_sale is False
    assert parsed.listing_status_category == "A"
    assert parsed.has_heloc_propensity_trigger is True
    assert parsed.heloc_propensity_score == 812
    assert parsed.has_refi_propensity_trigger is True
    assert parsed.second_pos_amount == 125000


def test_borrower_360_projection_selects_module0_flags() -> None:
    for column in (
        "is_owner_occupied",
        "is_absentee",
        "is_corporate_owner",
        "is_investor",
        "is_current_customer",
        "is_former_customer",
        "is_competitor_lien",
        "has_permit",
        "listed_for_sale",
        "listing_status_category",
        "listing_status_description",
        "listing_date",
        "listing_status_date",
        "listing_price",
        "listing_days_on_market",
        "listing_service",
        "heloc_propensity_score",
        "heloc_propensity_run_date",
        "has_heloc_propensity_trigger",
        "refi_propensity_score",
        "refi_propensity_run_date",
        "has_refi_propensity_trigger",
        "second_pos_amount",
        "situs_cbsa_code",
        "first_pos_loan_type",
        "has_first_party_relationship",
        "first_party_relationship_depth",
        "first_party_recent_interactions",
        "first_party_recent_application",
        "first_party_synthetic_demo",
        "current_lien_balance_low",
        "current_lien_balance_high",
    ):
        assert column in _BORROWER_360_COLUMNS


def test_borrower_360_accepts_governed_dossier_enrichment_fields() -> None:
    payload = {
        **_lead_payload(),
        "clip_id": "clip_ref_0123abcd4567",
        "owner_link_id": "owner_link_ref_0123abcd4567",
        "subject_property": "Synthetic property · Chicago, IL 60614",
        "avm_value": 500000,
        "current_lien_balance": 300000,
        "current_lien_balance_low": 290000,
        "current_lien_balance_high": 310000,
        "current_rate": 6.5,
        "ltv": 60,
        "related_property_count": 1,
        "situs_cbsa_code": "16980",
        "first_pos_loan_type": "CONV",
        "is_absentee": True,
        "is_corporate_owner": False,
        "has_first_party_relationship": True,
        "first_party_relationship_depth": 3,
        "first_party_recent_interactions": 2,
        "first_party_recent_application": True,
        "first_party_synthetic_demo": True,
        "trigger_timeline": [],
        "evidence_events": [],
        "why_panel": {
            "rate_spread_bps": 100,
            "market_rate": 6.0,
            "equity_pct": 30,
            "in_the_money": True,
            "in_the_money_reason": "ok",
            "min_spread_bps": 75,
            "min_equity_pct": 15,
            "sources": [],
        },
    }

    parsed = Borrower360.model_validate(payload)

    assert parsed.situs_cbsa_code == "16980"
    assert parsed.first_pos_loan_type == "CONV"
    assert parsed.is_absentee is True
    assert parsed.has_first_party_relationship is True
    assert parsed.first_party_recent_interactions == 2
    assert parsed.current_lien_balance_low == 290000
    assert parsed.current_lien_balance_high == 310000


def test_lead_population_projection_selects_module0_flags() -> None:
    for column in (
        "recommended_offer_code",
        "is_owner_occupied",
        "is_investor",
        "is_current_customer",
        "is_former_customer",
        "is_competitor_lien",
        "has_permit",
        "listed_for_sale",
        "listing_status_category",
        "listing_status_description",
        "listing_date",
        "listing_status_date",
        "listing_price",
        "listing_days_on_market",
        "listing_service",
        "heloc_propensity_score",
        "heloc_propensity_run_date",
        "has_heloc_propensity_trigger",
        "refi_propensity_score",
        "refi_propensity_run_date",
        "has_refi_propensity_trigger",
        "second_pos_amount",
    ):
        assert column in _LEAD_POPULATION_COLUMNS


def test_lead_population_sql_emits_canonical_offer_code() -> None:
    transform_sql = (
        REPO_ROOT / "sql" / "transformations" / "gold_lead_population.sql"
    ).read_text()
    ddl_sql = (REPO_ROOT / "sql" / "ddl" / "gold_lead_population.sql").read_text()

    assert "b.recommended_offer_code" in transform_sql
    assert "recommended_offer_code" in ddl_sql


def test_borrower_360_rejects_street_address_subject_property() -> None:
    payload = {
        **_lead_payload(),
        "clip_id": "clip_ref_0123abcd4567",
        "owner_link_id": "owner_link_ref_0123abcd4567",
        "subject_property": "123 Elm St, Chicago, IL 60614",
        "avm_value": 500000,
        "current_lien_balance": 300000,
        "current_rate": 6.5,
        "ltv": 60,
        "related_property_count": 1,
        "trigger_timeline": [],
        "evidence_events": [],
        "why_panel": {
            "rate_spread_bps": 100,
            "market_rate": 6.0,
            "equity_pct": 30,
            "in_the_money": True,
            "in_the_money_reason": "ok",
            "min_spread_bps": 75,
            "min_equity_pct": 15,
            "sources": [],
        },
    }
    with pytest.raises(ValidationError):
        Borrower360.model_validate(payload)


@pytest.mark.parametrize(
    "schema_cls",
    [SavedLeadInput, SavedDraftInput, OutreachDraftRequest, OutreachApproveRequest],
)
def test_state_changing_schemas_reject_raw_borrower_ids(schema_cls: type) -> None:
    payload: dict[str, object] = {"borrower_id": "1234567890"}
    if schema_cls is SavedLeadInput:
        payload.update({"city": "Chicago"})
    if schema_cls is SavedDraftInput:
        payload.update({"body": "Draft text."})
    if schema_cls is OutreachApproveRequest:
        payload.update({"offer_code": "refi"})

    with pytest.raises(ValidationError):
        schema_cls.model_validate(payload)


@pytest.mark.parametrize(
    "schema_cls",
    [SavedLeadInput, SavedDraftInput, OutreachDraftRequest, OutreachApproveRequest],
)
def test_state_changing_schemas_accept_public_borrower_ids(schema_cls: type) -> None:
    payload: dict[str, object] = {"borrower_id": "B-102FL7THC6Q3L"}
    if schema_cls is SavedDraftInput:
        payload.update({"body": "Draft text."})
    if schema_cls is OutreachApproveRequest:
        payload.update({"offer_code": "refi"})

    parsed = schema_cls.model_validate(payload)

    assert parsed.borrower_id == "B-102FL7THC6Q3L"
