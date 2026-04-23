"""Unit tests for `backend.services.state_footprint` + `/api/config/footprint`.

Covers hole-finder round-2 #20: the tenant footprint moved from 5
hardcoded copies to a single UC-backed source of truth.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
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


def test_fallback_preserves_default_six_state_footprint() -> None:
    """UC unavailable => resolver yields the canonical Summit footprint.

    Guard against a regression that silently empties the dropdown when
    the seed hasn't run yet on first deploy.
    """
    resolver = _resolver_with_uc_rows(None)
    codes = resolver.state_codes()
    assert codes == ["IL", "CA", "FL", "TX", "WA", "CO"]
    assert resolver.default_state_code() == "IL"


def test_uc_rows_override_fallback_and_preserve_order() -> None:
    """Live UC rows win; `display_order` drives the list order."""
    uc_rows = [
        FootprintState("NY", "New York",     1, True),
        FootprintState("NJ", "New Jersey",   2, False),
        FootprintState("PA", "Pennsylvania", 3, False),
    ]
    resolver = _resolver_with_uc_rows(uc_rows)
    assert resolver.state_codes() == ["NY", "NJ", "PA"]
    assert resolver.default_state_code() == "NY"
    # A tenant with a 3-state footprint should see exactly 3 rows, not 6.
    assert len(resolver.list()) == 3


def test_default_state_falls_back_to_first_when_no_row_flagged() -> None:
    """If `is_default_state` is FALSE on every row, fall through to the
    first row by display_order — never raise."""
    uc_rows = [
        FootprintState("TX", "Texas",    1, False),
        FootprintState("CA", "California", 2, False),
    ]
    resolver = _resolver_with_uc_rows(uc_rows)
    assert resolver.default_state_code() == "TX"


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
    try:
        client = TestClient(app)
        resp = client.get("/api/config/footprint")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["default_state_code"] == "NY"
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


def test_singleton_get_state_footprint_resolver_is_stable() -> None:
    """Same resolver instance across calls (process-wide cache)."""
    _reset_state_footprint_resolver_for_tests(None)
    try:
        a = get_state_footprint_resolver()
        b = get_state_footprint_resolver()
        assert a is b
    finally:
        _reset_state_footprint_resolver_for_tests(None)
