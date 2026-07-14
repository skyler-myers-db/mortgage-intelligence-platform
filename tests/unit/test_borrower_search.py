"""Borrower search contract tests.

The global topbar search is an operator workflow, not just an ID lookup.
It must support partial ZIPs plus county/state geography terms so users
can get useful suggestions from the same field.
"""
from __future__ import annotations

from typing import Any

from backend.services.repositories.databricks_repo import DatabricksBorrowerRepository


class RecordingSqlClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def execute(
        self,
        statement: str,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((statement, params))
        return [
            {
                "clip": "123456789012",
                "borrower_id": "B-SEARCH3306",
                "display_name": "Owner abc12345",
                "city": "North Lauderdale",
                "state": "FL",
                "zip": "33068",
                "segment_codes": ["itm"],
                "equity_estimate": 425000,
                "rate_spread_bps": 225,
                "opportunity_score": 82,
                "confidence": 79,
                "recommended_offer_code": "refi_plus_heloc",
                "recommended_offer": "Refinance + HELOC",
                "why_now": "Search contract fixture",
                "evidence_ids": ["ev-search"],
                "approval_status": "pending",
                "outreach_status": "none",
                "current_lender_ref": "Competitor A",
                "is_owner_occupied": True,
                "is_investor": False,
                "is_current_customer": False,
                "is_former_customer": False,
                "is_competitor_lien": True,
                "related_property_count": 1,
                "current_lien_balance": 310000,
                "second_pos_amount": 0,
                "has_permit": False,
                "listed_for_sale": False,
                "marketing_eligible": True,
                "consent_status": "opt_in",
                "suppression_reason": None,
                "last_touch_at": None,
                "eligible_recontact_at": None,
            }
        ]


def _repo() -> tuple[DatabricksBorrowerRepository, RecordingSqlClient]:
    client = RecordingSqlClient()
    return DatabricksBorrowerRepository(client), client


def test_search_supports_partial_zip_prefix() -> None:
    repo, client = _repo()

    rows = repo.search("3306")

    assert rows and rows[0].zip == "33068"
    statement, params = client.calls[-1]
    assert "b.zip LIKE :zip_prefix" in statement
    assert params is not None
    assert params["zip_prefix"] == "3306%"
    assert params["zip_exact"] == "__NO_ZIP_MATCH__"


def test_search_supports_county_name_terms() -> None:
    repo, client = _repo()

    rows = repo.search("Broward County")

    assert rows and rows[0].city == "North Lauderdale"
    statement, params = client.calls[-1]
    assert "latest_counties" in statement
    assert "cr.county_name" in statement
    assert "b.county_fips_5 IN (:county_fips_0)" in statement
    assert params is not None
    assert params["county_contains"] == "%BROWARD%"
    assert params["county_fips_0"] == "12011"


def test_search_supports_state_names_and_codes() -> None:
    repo, client = _repo()

    rows = repo.search("Florida")

    assert rows and rows[0].state == "FL"
    statement, params = client.calls[-1]
    assert "b.state = :state_exact" in statement
    assert params is not None
    assert params["state_exact"] == "FL"
    assert DatabricksBorrowerRepository._state_search_code("WA") == "WA"
    assert DatabricksBorrowerRepository._state_search_code("wash") == "WA"


def test_search_never_matches_or_binds_raw_clip() -> None:
    repo, client = _repo()

    repo.search("123456789012")

    statement, params = client.calls[-1]
    assert "b.clip =" not in statement
    assert params is not None
    assert "clip_exact" not in params
