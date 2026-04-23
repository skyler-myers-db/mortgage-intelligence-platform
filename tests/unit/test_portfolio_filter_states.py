"""Regression tests for the portfolio-builder GEO filter.

Bug (2026-04-23): selecting "Florida" / "California" / etc. from the
portfolio-builder GEO dropdown returned the same marketable population
as "All 6 states" because the _STATIC_STATE_SETS map only contained
MSA combos and "Texas" — every other per-state label missed the map
and fell through to a criteria-free SELECT.

The fix merges per-state-name entries from
``StateFootprintResolver.state_name_to_codes()`` into ``_state_sets()``.
These tests assert the merged map produces a ``state IN (...)`` WHERE
clause with the correct 2-char USPS code bound as ``geo_state_0``, and
that the lookup is case-insensitive.
"""
from __future__ import annotations

from backend.schemas.portfolio import PortfolioCriteria
from backend.services.repositories.databricks_repo import (
    DatabricksPortfolioRepository,
)
from backend.services.state_footprint import (
    FootprintState,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
)

# Canonical 6-state Summit footprint. The resolver falls back to this
# when UC is unavailable so these tests don't need any warehouse stub.
_SIX_STATE_FOOTPRINT: list[FootprintState] = [
    FootprintState("IL", "Illinois",   1, True),
    FootprintState("CA", "California", 2, False),
    FootprintState("FL", "Florida",    3, False),
    FootprintState("TX", "Texas",      4, False),
    FootprintState("WA", "Washington", 5, False),
    FootprintState("CO", "Colorado",   6, False),
]


def _install_footprint(rows: list[FootprintState]) -> None:
    """Wire the process-wide resolver to a deterministic footprint."""
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: rows  # type: ignore[method-assign]
    _reset_state_footprint_resolver_for_tests(resolver)


def setup_function(_func: object) -> None:
    _install_footprint(_SIX_STATE_FOOTPRINT)


def teardown_function(_func: object) -> None:
    _reset_state_footprint_resolver_for_tests(None)


def _predicates(geography: str) -> tuple[str, dict[str, object]]:
    criteria = PortfolioCriteria(geography=geography)
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


def test_colorado_produces_state_in_predicate() -> None:
    where, params = _predicates("Colorado")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "CO"}


def test_texas_produces_state_in_predicate() -> None:
    """Texas was the one per-state entry that worked before the fix.
    Keep it asserted so the static-set + footprint-set merge can't
    regress it by accident."""
    where, params = _predicates("Texas")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "TX"}


def test_lookup_is_case_insensitive() -> None:
    """Upper, lower, and mixed case all resolve to the same filter."""
    for label in ("florida", "FLORIDA", "Florida", "FlOrIdA"):
        where, params = _predicates(label)
        assert "state IN (:geo_state_0)" in where, label
        assert params == {"geo_state_0": "FL"}, label


def test_all_6_states_returns_full_footprint() -> None:
    """Legacy "All 6 states" label keeps returning the union."""
    where, params = _predicates("All 6 states")
    assert "state IN (:geo_state_0, :geo_state_1, :geo_state_2, " \
           ":geo_state_3, :geo_state_4, :geo_state_5)" in where
    assert params == {
        "geo_state_0": "IL",
        "geo_state_1": "CA",
        "geo_state_2": "FL",
        "geo_state_3": "TX",
        "geo_state_4": "WA",
        "geo_state_5": "CO",
    }


def test_all_n_states_computed_from_footprint() -> None:
    """The ``all N states`` key reflects the live footprint count."""
    where, params = _predicates("All 6 states")
    # Same union as the legacy alias.
    assert "state IN (" in where
    assert set(params.values()) == {"IL", "CA", "FL", "TX", "WA", "CO"}


def test_msa_combo_still_resolves_to_il() -> None:
    """Chicago MSA is still a single-state shortcut backed by IL."""
    where, params = _predicates("Chicago MSA")
    assert "state IN (:geo_state_0)" in where
    assert params == {"geo_state_0": "IL"}


def test_unknown_geography_label_yields_no_predicate() -> None:
    """A string that isn't in the merged map produces an empty WHERE
    — the caller falls back to the criteria-free SELECT rather than a
    SQL error. Matches the prior behavior for 'Mars' / typos."""
    where, params = _predicates("Atlantis")
    assert "state IN" not in where
    # No state-bound parameters leak through.
    assert all(not k.startswith("geo_state_") for k in params)


def test_three_state_tenant_footprint_resolves_per_state() -> None:
    """A tenant with a different footprint (e.g. NY/NJ/PA) sees its own
    state names in the predicate map — the footprint isn't pinned to
    the 6-state default."""
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

    # "All 3 states" (not "All 6 states") is the active key for this tenant.
    where, params = _predicates("All 3 states")
    assert params == {
        "geo_state_0": "NY",
        "geo_state_1": "NJ",
        "geo_state_2": "PA",
    }
