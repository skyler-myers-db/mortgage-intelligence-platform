"""Regression tests for Module 0 portfolio-builder predicate pushdown.

These tests pin the SQL WHERE clauses produced from
``PortfolioCriteria`` before a warehouse call is made. They are narrow
by design: the QA contract here is "the UI-selected lender relationship,
lien, and product filters become the intended gold.borrower_360
predicate", not "the warehouse returns a particular live count".
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCriteria,
    PortfolioPreviewRequest,
)
from backend.services.repositories.databricks_repo import DatabricksPortfolioRepository
from backend.services.state_footprint import (
    FootprintState,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
)


def _where_for(criteria: PortfolioCriteria) -> tuple[str, dict[str, object]]:
    return DatabricksPortfolioRepository._build_preview_predicates(criteria)


def _any_contactability(**kwargs: object) -> PortfolioCriteria:
    return PortfolioCriteria(marketing_eligibility="Any", **kwargs)


def _install_test_coverage() -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: [  # type: ignore[method-assign]
        FootprintState("NY", "New York", 1, True),
        FootprintState("NJ", "New Jersey", 2, False),
        FootprintState("PA", "Pennsylvania", 3, False),
    ]
    _reset_state_footprint_resolver_for_tests(resolver)


@pytest.mark.parametrize(
    ("relationship", "expected_clause"),
    [
        ("Current customer", "is_current_customer = TRUE"),
        (
            "Former customer",
            "is_former_customer = TRUE",
        ),
        ("Competitor customer", "is_competitor_lien = TRUE"),
    ],
)
def test_lender_relationship_predicates_are_specific(
    relationship: str,
    expected_clause: str,
) -> None:
    where, params = _where_for(
        _any_contactability(lender_relationship=relationship),
    )

    assert where == f"WHERE {expected_clause}"
    assert params == {}


@pytest.mark.parametrize("relationship", [None, "All", ""])
def test_lender_relationship_all_or_missing_is_not_a_predicate(
    relationship: str | None,
) -> None:
    where, params = _where_for(
        _any_contactability(lender_relationship=relationship),
    )

    assert where == ""
    assert params == {}


def test_open_heloc_lien_filter_uses_second_position_predicate() -> None:
    """Open HELOC means an existing second-position lien, not any open first lien."""
    where, params = _where_for(
        _any_contactability(lien_status="Open HELOC"),
    )

    assert "COALESCE(second_pos_amount, 0) > 0" in where
    assert "current_lien_balance > 0" not in where
    assert params == {}


def test_target_lender_ref_predicate_is_parameterized() -> None:
    where, params = _where_for(
        _any_contactability(target_lender_ref="Competitor B"),
    )

    assert where == "WHERE current_lender_ref = :target_lender_ref"
    assert params == {"target_lender_ref": "Competitor B"}


def test_target_lender_ref_all_is_not_a_predicate() -> None:
    where, params = _where_for(
        _any_contactability(target_lender_ref="All"),
    )

    assert where == ""
    assert params == {}


def test_marketing_contactability_predicates_are_specific() -> None:
    where, params = _where_for(
        PortfolioCriteria(
            marketing_eligibility="Eligible only",
            consent_status="Opt-in",
            recency="Untouched 30d",
        ),
    )

    assert "marketing_eligible = TRUE" in where
    assert "consent_status = 'opt_in'" in where
    assert "last_touch_at IS NULL OR last_touch_at < CURRENT_TIMESTAMP() - INTERVAL 30 DAYS" in where
    assert params == {}


def test_portfolio_criteria_defaults_to_eligible_only_contactability() -> None:
    criteria = PortfolioCriteria()
    where, params = _where_for(criteria)

    assert criteria.marketing_eligibility == "Eligible only"
    assert where == "WHERE marketing_eligible = TRUE"
    assert params == {}


def test_marketing_suppressed_predicate_is_explicit() -> None:
    where, params = _where_for(
        PortfolioCriteria(marketing_eligibility="Suppressed only", consent_status="Opt-out"),
    )

    assert "marketing_eligible = FALSE" in where
    assert "consent_status = 'opt_out'" in where
    assert params == {}


def test_target_lender_ref_rejects_raw_lender_name() -> None:
    with pytest.raises(ValidationError):
        PortfolioCriteria(target_lender_ref="Wells Fargo Bank")


def test_portfolio_criteria_rejects_arbitrary_geography_text() -> None:
    with pytest.raises(ValidationError):
        PortfolioCriteria(geography="123 Main Street")


def test_portfolio_criteria_accepts_reviewed_geography_labels() -> None:
    _install_test_coverage()
    try:
        assert PortfolioCriteria(geography="All").geography == "All"
        assert PortfolioCriteria(geography="All 3 states").geography == "All 3 states"
        assert PortfolioCriteria(geography="New York").geography == "New York"
    finally:
        _reset_state_footprint_resolver_for_tests(None)


def test_portfolio_criteria_accepts_reviewed_multi_state_codes() -> None:
    _install_test_coverage()
    try:
        criteria = _any_contactability(states=["ny", "NJ", "NY"])
        assert criteria.states == ["NY", "NJ"]
        where, params = _where_for(criteria)
    finally:
        _reset_state_footprint_resolver_for_tests(None)

    assert where == "WHERE state IN (:geo_state_0, :geo_state_1)"
    assert params == {"geo_state_0": "NY", "geo_state_1": "NJ"}


def test_portfolio_criteria_rejects_unreviewed_state_codes() -> None:
    _install_test_coverage()
    try:
        with pytest.raises(ValidationError):
            PortfolioCriteria(states=["CA"])
    finally:
        _reset_state_footprint_resolver_for_tests(None)


def test_portfolio_requests_reject_unknown_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        PortfolioPreviewRequest(geography_states=["CA"])  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        PortfolioCreateRequest(name="Bad", geography_states=["CA"])  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "name",
    [
        "Alice Smith pilot",
        "Alice Smith",
        "Alice Q Smith pilot",
        "owner_name=Alice",
        "raw_clip=9154364327",
        "alice@example.com",
        "555-555-1212",
        "123 Main Street",
    ],
)
def test_portfolio_create_rejects_pii_like_names(name: str) -> None:
    with pytest.raises(ValidationError):
        PortfolioCreateRequest(name=name)


def test_portfolio_create_strips_and_accepts_business_name() -> None:
    request = PortfolioCreateRequest(name="  Q3 CA Pilot - competitor recapture  ")
    assert request.name == "Q3 CA Pilot - competitor recapture"


def test_portfolio_criteria_rejects_arbitrary_option_text() -> None:
    with pytest.raises(ValidationError):
        PortfolioCriteria(occupancy="Call center note")
