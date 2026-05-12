"""Regression tests for the portfolio-builder GEO filter.

The Portfolio Builder geography contract is fully coverage-driven:
the valid broad option is ``All N states`` for the current live coverage
size, and the remaining reviewed labels are state names from
``StateFootprintResolver``. These tests pin that contract before any
warehouse call is made.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.portfolio import PortfolioCriteria
from backend.services.repositories.databricks_repo import (
    DatabricksPortfolioRepository,
)
from backend.services.state_footprint import (
    FootprintState,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
)

# Deterministic geography coverage used by this unit suite.
_COVERAGE_FOOTPRINT: list[FootprintState] = [
    FootprintState("IL", "Illinois",   1, True),
    FootprintState("CA", "California", 2, False),
    FootprintState("FL", "Florida",    3, False),
    FootprintState("WA", "Washington", 4, False),
]


def _install_footprint(rows: list[FootprintState]) -> None:
    """Wire the process-wide resolver to a deterministic footprint."""
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: rows  # type: ignore[method-assign]
    _reset_state_footprint_resolver_for_tests(resolver)


def setup_function(_func: object) -> None:
    _install_footprint(_COVERAGE_FOOTPRINT)


def teardown_function(_func: object) -> None:
    _reset_state_footprint_resolver_for_tests(None)


def _predicates(geography: str) -> tuple[str, dict[str, object]]:
    criteria = PortfolioCriteria(geography=geography, marketing_eligibility="Any")
    return _predicates_for(criteria)


def _predicates_for(criteria: PortfolioCriteria) -> tuple[str, dict[str, object]]:
    return DatabricksPortfolioRepository._build_preview_predicates(criteria)


def test_florida_produces_state_in_predicate() -> None:
    """The bug case: "Florida" must bind to FL, not fall through."""
    where, params = _predicates("Florida")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "FL"}


def test_california_produces_state_in_predicate() -> None:
    where, params = _predicates("California")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "CA"}


def test_illinois_produces_state_in_predicate() -> None:
    where, params = _predicates("Illinois")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "IL"}


def test_washington_produces_state_in_predicate() -> None:
    where, params = _predicates("Washington")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "WA"}


def test_lookup_is_case_insensitive() -> None:
    """Upper, lower, and mixed case all resolve to the same filter."""
    for label in ("florida", "FLORIDA", "Florida", "FlOrIdA"):
        where, params = _predicates(label)
        assert "state IN (:geo_state_0)" in where, label
        assert params == {"geo_state_0": "FL"}, label


def test_all_current_state_count_returns_full_footprint() -> None:
    """When live coverage has four states, ``All 4 states`` is broad."""
    where, params = _predicates("All 4 states")
    assert where == ""
    assert params == {}


def test_all_alias_returns_full_footprint_without_state_predicate() -> None:
    """Internal broad alias used during initial hydration is also no-op."""
    where, params = _predicates("All")
    assert where == ""
    assert params == {}


@pytest.mark.parametrize("value", [-10, 150])
def test_min_equity_pct_is_bounded_for_all_entry_points(value: float) -> None:
    with pytest.raises(ValidationError, match="min_equity_pct must be between 0 and 100"):
        PortfolioCriteria(min_equity_pct=value)


def test_all_n_states_computed_from_footprint() -> None:
    """The broad ``all N states`` key reflects the live footprint count."""
    _install_footprint(
        [
            FootprintState("NY", "New York",     1, True),
            FootprintState("NJ", "New Jersey",   2, False),
            FootprintState("PA", "Pennsylvania", 3, False),
        ]
    )
    where, params = _predicates("All 3 states")
    assert where == ""
    assert params == {}


def test_stale_state_count_label_is_rejected() -> None:
    """A stale broad-count URL must fail closed for the current coverage."""
    _install_footprint(
        [
            FootprintState("NY", "New York",     1, True),
            FootprintState("NJ", "New Jersey",   2, False),
            FootprintState("PA", "Pennsylvania", 3, False),
        ]
    )
    with pytest.raises(ValueError, match="geography must be one of"):
        PortfolioCriteria(geography="All 4 states")


def test_fixed_geography_shortcuts_are_rejected() -> None:
    """Non-footprint geography shortcuts are not part of the product contract."""
    for label in ("Chicago MSA", "CA + FL + TX", "IL + CA + WA"):
        with pytest.raises(ValueError, match="geography must be one of"):
            PortfolioCriteria(geography=label)


def test_unknown_geography_label_is_rejected() -> None:
    """Unreviewed geography labels must fail closed instead of broadening."""
    with pytest.raises(ValueError, match="geography must be one of"):
        PortfolioCriteria(geography="Atlantis")


def test_open_first_lien_excludes_second_position_helocs() -> None:
    """Portfolio Builder's 1st-lien filter must not include open HELOC rows."""
    where, params = _predicates_for(
        PortfolioCriteria(lien_status="Open 1st lien", marketing_eligibility="Any"),
    )

    assert "current_lien_balance > 0" in where
    assert "COALESCE(second_pos_amount, 0) = 0" in where
    assert params == {}


def test_open_heloc_uses_second_position_lien_signal() -> None:
    """Open HELOC is backed by gold.second_pos_amount, not any open lien."""
    where, params = _predicates_for(
        PortfolioCriteria(lien_status="Open HELOC", marketing_eligibility="Any"),
    )

    assert "COALESCE(second_pos_amount, 0) > 0" in where
    assert "current_lien_balance > 0" not in where
    assert params == {}


def test_former_customer_uses_backed_recapture_signal() -> None:
    """Former customer must use the backed historical relationship flag."""
    where, params = _predicates_for(
        PortfolioCriteria(lender_relationship="Former customer", marketing_eligibility="Any"),
    )

    assert "is_former_customer = TRUE" in where
    assert "is_competitor_lien = TRUE" not in where
    assert "is_current_customer = FALSE AND is_competitor_lien = FALSE" not in where
    assert params == {}


def test_competitor_customer_alias_uses_competitor_lien_signal() -> None:
    where, params = _predicates_for(
        PortfolioCriteria(lender_relationship="Competitor customer", marketing_eligibility="Any"),
    )

    assert "is_competitor_lien = TRUE" in where
    assert params == {}


def test_three_state_tenant_footprint_resolves_per_state() -> None:
    """A different live coverage set sees its own state names in the predicate map."""
    _install_footprint(
        [
            FootprintState("NY", "New York",     1, True),
            FootprintState("NJ", "New Jersey",   2, False),
            FootprintState("PA", "Pennsylvania", 3, False),
        ]
    )
    where, params = _predicates("New York")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "NY"}

    where, params = _predicates("Pennsylvania")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "PA"}

    # "All 3 states" is the active key for this tenant.
    where, params = _predicates("All 3 states")
    assert where == ""
    assert params == {}
