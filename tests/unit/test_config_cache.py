from __future__ import annotations

import backend.api.config as config_api
from backend.services.geography_scope import GeographyScope, GeographyScopeCounty
from backend.services.resilience import TTLCache
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
    assert first["lender_name"] == config_api.settings.mip_lender_name
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


def test_config_footprint_expired_live_cache_does_not_mask_unavailable(
    monkeypatch,
) -> None:
    clock = {"now": 0.0}
    state = {"live": True}

    class Resolver:
        def list(self) -> list[FootprintState]:
            return [FootprintState("IL", "Illinois", 1, True)]

        def using_fallback(self) -> bool:
            return not state["live"]

    def resolver() -> Resolver:
        return Resolver()

    def live_scope() -> GeographyScope | None:
        return _scope() if state["live"] else None

    monkeypatch.setattr(config_api, "_CONFIG_CACHE", TTLCache(now=lambda: clock["now"]))
    monkeypatch.setattr(config_api.settings, "mip_cache_ttl_s", 1.0)
    monkeypatch.setattr(config_api, "get_state_footprint_resolver", resolver)
    monkeypatch.setattr(config_api, "_live_geography_scope", live_scope)

    first = config_api.get_config_footprint()
    assert first["using_fallback"] is False
    assert first["geography_scope"] is not None

    clock["now"] = 2.0
    state["live"] = False
    second = config_api.get_config_footprint()

    assert second["using_fallback"] is True
    assert second["geography_scope"] is None


def test_config_options_expired_live_cache_does_not_mask_unavailable(
    monkeypatch,
) -> None:
    clock = {"now": 0.0}
    state = {"live": True}

    class Resolver:
        def list(self) -> list[FootprintState]:
            return [FootprintState("IL", "Illinois", 1, True)]

        def using_fallback(self) -> bool:
            return not state["live"]

    def target_lenders() -> tuple[list[str], str]:
        if state["live"]:
            return ["All", "Summit Mortgage"], "live"
        return ["All"], "unavailable"

    def live_scope() -> GeographyScope | None:
        return _scope() if state["live"] else None

    monkeypatch.setattr(config_api, "_CONFIG_CACHE", TTLCache(now=lambda: clock["now"]))
    monkeypatch.setattr(config_api.settings, "mip_cache_ttl_s", 1.0)
    monkeypatch.setattr(config_api, "_target_lender_options", target_lenders)
    monkeypatch.setattr(config_api, "get_state_footprint_resolver", lambda: Resolver())
    monkeypatch.setattr(config_api, "_live_geography_scope", live_scope)

    first = config_api.get_config_options()
    assert first["target_lender_refs_status"] == "live"
    assert first["geographies_status"] == "live"

    clock["now"] = 2.0
    state["live"] = False
    second = config_api.get_config_options()

    assert second["target_lender_refs_status"] == "unavailable"
    assert second["geographies_status"] == "metadata_only"
    assert second["geography_scope"] is None
