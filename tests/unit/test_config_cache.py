from __future__ import annotations

import backend.api.config as config_api
from backend.services.geography_scope import GeographyScope, GeographyScopeCounty
from backend.services.state_footprint import FootprintState


def setup_function() -> None:
    config_api._reset_config_cache_for_tests()


def teardown_function() -> None:
    config_api._reset_config_cache_for_tests()


def _scope() -> GeographyScope:
    return GeographyScope(
        state_count=1,
        county_count=1,
        zip_count=2,
        snapshot_date="2026-05-13",
        counties=(
            GeographyScopeCounty(
                state="IL",
                fips_5="17031",
                county_name="Cook County",
                addressable_borrowers=100,
            ),
        ),
        source_table="mip.gold.county_rollup",
    )


def test_config_options_uses_short_ttl_cache(monkeypatch) -> None:
    calls = {"lenders": 0, "scope": 0}

    def target_lenders() -> tuple[list[str], str]:
        calls["lenders"] += 1
        return ["All", "Summit Mortgage"], "live"

    def live_scope() -> GeographyScope:
        calls["scope"] += 1
        return _scope()

    monkeypatch.setattr(config_api, "_target_lender_options", target_lenders)
    monkeypatch.setattr(config_api, "_live_geography_scope", live_scope)

    first = config_api.get_config_options()
    second = config_api.get_config_options()

    assert first == second
    assert calls == {"lenders": 1, "scope": 1}


def test_config_footprint_uses_short_ttl_cache(monkeypatch) -> None:
    calls = {"resolver": 0, "scope": 0}

    class Resolver:
        def list(self) -> list[FootprintState]:
            return [FootprintState("IL", "Illinois", 1, True)]

        def using_fallback(self) -> bool:
            return False

    def resolver() -> Resolver:
        calls["resolver"] += 1
        return Resolver()

    def live_scope() -> GeographyScope:
        calls["scope"] += 1
        return _scope()

    monkeypatch.setattr(config_api, "get_state_footprint_resolver", resolver)
    monkeypatch.setattr(config_api, "_live_geography_scope", live_scope)

    first = config_api.get_config_footprint()
    second = config_api.get_config_footprint()

    assert first == second
    assert calls == {"resolver": 1, "scope": 1}
