"""Unit tests for `backend.services.state_footprint` + `/api/config/footprint`.

Covers the geography-coverage contract: live gold rollups are data-bearing
scope; reference rows are metadata/fallback only.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.config import _reset_config_cache_for_tests
from backend.main import app
from backend.services.genie_prompt_guardrails import (
    footprint_metadata_gap_match,
    outside_footprint_match,
)
from backend.services.state_footprint import (
    FootprintState,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
    get_state_footprint_resolver,
)


def _resolver_with_uc_rows(
    uc_rows: list[FootprintState] | None,
) -> StateFootprintResolver:
    """Build a resolver whose `_load_from_uc` returns the given rows.

    Pass ``None`` to simulate UC unreachable (forces fallback).
    """
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: uc_rows  # type: ignore[method-assign]
    return resolver


def test_fallback_uses_generic_us_state_dictionary() -> None:
    """UC unavailable => resolver yields generic geography metadata.

    Guard against a regression that silently pins an unhydrated workspace to
    the Summit evaluation-share footprint.
    """
    resolver = _resolver_with_uc_rows(None)
    codes = resolver.state_codes()
    assert len(codes) == 50
    assert codes[:5] == ["AL", "AK", "AZ", "AR", "CA"]
    assert "IL" in codes
    assert "DC" not in codes
    # 2026-08-07 platform audit: the generic fallback is the alphabetical
    # 50-state dictionary, so "first by display order" used to name Alabama
    # -- a state with zero borrowers in the current share -- as THE default
    # purely because it sorts first. There is no default state when there is
    # no footprint.
    assert resolver.default_state_code() is None
    assert resolver.using_fallback() is True


def test_uc_metadata_rows_override_generic_fallback_when_no_gold_coverage() -> None:
    """Metadata rows win over generic fallback but are still degraded scope."""
    uc_rows = [
        FootprintState("NY", "New York",     1, True),
        FootprintState("NJ", "New Jersey",   2, False),
        FootprintState("PA", "Pennsylvania", 3, False),
    ]
    resolver = _resolver_with_uc_rows(uc_rows)
    assert resolver.state_codes() == ["NY", "NJ", "PA"]
    assert resolver.default_state_code() == "NY"
    assert len(resolver.list()) == 3


def test_metadata_only_scope_is_treated_as_fallback(monkeypatch) -> None:
    """Metadata-only rows must not authorize data-bearing state answers."""

    class FakeClient:
        def execute(self, sql: str):
            if "ref.state_footprint" in sql:
                return [
                    {
                        "state_code": "NY",
                        "state_name": "New York",
                        "display_order": 1,
                        "is_default_state": True,
                    }
                ]
            if "gold.county_rollup" in sql:
                return []
            raise AssertionError(sql)

    import backend.services.databricks_sql as databricks_sql

    monkeypatch.setattr(databricks_sql, "get_sql_client", lambda: FakeClient())

    resolver = StateFootprintResolver(ttl_s=60.0)
    assert resolver.state_codes() == ["NY"]
    assert resolver.using_fallback() is True


def test_live_county_coverage_is_authoritative_over_metadata(monkeypatch) -> None:
    """Cotality coverage expands or contracts without code or seed edits.

    The ref table supplies names only. If coverage no longer contains a
    metadata state, that state is not returned.
    """

    class FakeClient:
        def execute(self, sql: str):
            if "ref.state_footprint" in sql:
                return [
                    {
                        "state_code": "IL",
                        "state_name": "Illinois",
                        "display_order": 1,
                        "is_default_state": True,
                    }
                ]
            if "gold.county_rollup" in sql:
                return [
                    {"state": "NY", "addressable_borrowers": 1000},
                    {"state": "GA", "addressable_borrowers": 500},
                ]
            raise AssertionError(sql)

    import backend.services.databricks_sql as databricks_sql

    monkeypatch.setattr(databricks_sql, "get_sql_client", lambda: FakeClient())

    resolver = StateFootprintResolver(ttl_s=60.0)
    rows = resolver.list()

    assert [row.state_code for row in rows] == ["NY", "GA"]
    assert [row.state_name for row in rows] == ["New York", "Georgia"]
    assert resolver.default_state_code() == "NY"
    assert resolver.using_fallback() is False


def test_county_coverage_can_drive_footprint_when_ref_table_empty(monkeypatch) -> None:
    """A fresh workspace should still expose discovered coverage states."""

    class FakeClient:
        def execute(self, sql: str):
            if "ref.state_footprint" in sql:
                return []
            if "gold.county_rollup" in sql:
                return [
                    {"state": "TX", "addressable_borrowers": 1000},
                    {"state": "CA", "addressable_borrowers": 900},
                ]
            raise AssertionError(sql)

    import backend.services.databricks_sql as databricks_sql

    monkeypatch.setattr(databricks_sql, "get_sql_client", lambda: FakeClient())

    resolver = StateFootprintResolver(ttl_s=60.0)
    rows = resolver.list()

    assert [row.state_code for row in rows] == ["TX", "CA"]
    assert rows[0].is_default_state is True
    assert resolver.default_state_code() == "TX"


def test_default_state_falls_back_to_first_when_no_row_flagged() -> None:
    """If `is_default_state` is FALSE on every UC row, fall through to the
    first row by display_order — never raise.

    The fall-through is still correct for UC-sourced rows: their order comes
    from ``ref.state_footprint.display_order`` (or live coverage ranked by
    population), so the first row is a deliberate choice by the data. It is
    only the generic outage fallback, whose order is the alphabet, where
    "first" means nothing — see
    ``test_fallback_uses_generic_us_state_dictionary``.
    """
    uc_rows = [
        FootprintState("TX", "Texas",    1, False),
        FootprintState("CA", "California", 2, False),
    ]
    resolver = _resolver_with_uc_rows(uc_rows)
    assert resolver.default_state_code() == "TX"


def test_outside_footprint_guard_is_unaffected_by_the_absent_default() -> None:
    """The fallback still exposes a full state list to the Genie guards.

    ``outside_footprint_match`` answers "is this state in scope?" from
    ``state_codes()``, and ``footprint_metadata_gap_match`` keys on
    ``using_fallback()``. Neither reads ``default_state_code()``, so dropping
    the fabricated default must not widen or narrow either guard.
    """
    resolver = _resolver_with_uc_rows(None)
    previous = get_state_footprint_resolver()
    _reset_state_footprint_resolver_for_tests(resolver)
    try:
        assert resolver.default_state_code() is None
        assert len(resolver.state_codes()) == 50
        assert resolver.using_fallback() is True
        # A non-US geography is still flagged as out of scope...
        flagged = outside_footprint_match("How many borrowers in Ontario?")
        assert flagged is not None
        assert flagged[1] == "Canada"
        # ...and a state inside the fallback list is not.
        assert outside_footprint_match("How many borrowers in Illinois?") is None
        # The degraded-scope disclosure still fires for that same question.
        assert footprint_metadata_gap_match("How many borrowers in Illinois?") == (
            "Illinois",
            "IL",
        )
    finally:
        _reset_state_footprint_resolver_for_tests(previous)


def test_config_footprint_endpoint_returns_resolver_payload() -> None:
    """`/api/config/footprint` serialises the resolver output.

    Uses `_reset_state_footprint_resolver_for_tests` to inject a
    deterministic 3-state footprint so the assertion doesn't depend on
    live UC.
    """
    uc_rows = [
        FootprintState("NY", "New York",     1, True),
        FootprintState("NJ", "New Jersey",   2, False),
        FootprintState("PA", "Pennsylvania", 3, False),
    ]
    fake = _resolver_with_uc_rows(uc_rows)
    _reset_state_footprint_resolver_for_tests(fake)
    _reset_config_cache_for_tests()
    try:
        client = TestClient(app)
        resp = client.get("/api/config/footprint")
        assert resp.status_code == 200
        payload = resp.json()
        codes = [row["state_code"] for row in payload["states"]]
        assert codes == ["NY", "NJ", "PA"]
        # Shape guard: schema contract the frontend hydration depends on.
        first = payload["states"][0]
        assert set(first.keys()) == {
            "state_code",
            "state_name",
            "display_order",
            "is_default_state",
        }
    finally:
        _reset_state_footprint_resolver_for_tests(None)
        _reset_config_cache_for_tests()


def test_singleton_get_state_footprint_resolver_is_stable() -> None:
    """Same resolver instance across calls (process-wide cache)."""
    _reset_state_footprint_resolver_for_tests(None)
    try:
        a = get_state_footprint_resolver()
        b = get_state_footprint_resolver()
        assert a is b
    finally:
        _reset_state_footprint_resolver_for_tests(None)
