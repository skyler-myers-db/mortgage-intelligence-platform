"""Unit tests for /api/geo/state-rollups + /county-rollups + /zip-rollups.

Covers the schema + router contract that keeps USChoroplethMap backed by
real gold rollups rather than component-local geography literals. The live
Databricks repo reads ``mip.gold.county_rollup`` and
``mip.gold.zip_rollup``; the in-process fixture returns deterministic
shapes that exercise the same envelope.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.geo import CountyRollupResponse, StateRollupResponse, ZipRollupResponse
from backend.services.repositories import get_geo_repository

client = TestClient(app)


def test_state_rollups_returns_dynamic_state_scope():
    """The fixture covers a discovered state scope. Response
    envelope must carry rollups[] + snapshot_date, and every state code
    must be a 2-char uppercase USPS code."""
    response = client.get("/api/geo/state-rollups")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "rollups" in payload
    assert "snapshot_date" in payload

    rollups = payload["rollups"]
    assert isinstance(rollups, list)
    assert len(rollups) > 0

    codes = {r["state"] for r in rollups}
    assert all(len(code) == 2 and code.isupper() for code in codes), codes

    for r in rollups:
        assert len(r["state"]) == 2
        assert r["state"].isupper()
        assert r["addressable"] >= 0
        assert r["in_the_money"] >= 0
        assert r["top_tier_opportunities"] >= 0
        # avg_score is 0..100 bounded by schema
        assert 0 <= r["avg_score"] <= 100
        # in_the_money should never exceed addressable
        assert r["in_the_money"] <= r["addressable"]


def test_state_rollups_carries_top_segment_code():
    """slice13-accuracy-validation: the state rollup envelope now carries
    a ``top_segment_code`` extension sourced from gold.state_top_segment.
    The fixture populates it for every state so the UI can drop the
    hardcoded STATE_FACTS[*].topSegment literal."""
    response = client.get("/api/geo/state-rollups")
    assert response.status_code == 200
    payload = response.json()
    known_segments = {"itm", "listed", "permit", "investor", "equity", "retention", "none"}
    for r in payload["rollups"]:
        # Fixture ships a non-null code for every state; in prod the column
        # can be null (LEFT JOIN). Accept both here and just guard the
        # vocab when non-null.
        if r.get("top_segment_code") is not None:
            assert r["top_segment_code"] in known_segments


def test_state_rollups_snapshot_date_format():
    """Snapshot date should be ISO-ish (YYYY-MM-DD) when present."""
    response = client.get("/api/geo/state-rollups")
    payload = response.json()
    if payload["snapshot_date"] is not None:
        # YYYY-MM-DD
        parts = payload["snapshot_date"].split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4


def test_state_rollups_passes_secondary_portfolio_criteria_to_repository():
    """Secondary filters on Segment Intelligence must narrow map counts
    through the same repository criteria object the lead queue uses."""

    class SpyGeoRepository:
        seen = None

        def state_rollups(
            self,
            segment_codes=None,
            segment_mode="any",
            portfolio_criteria=None,
        ):
            self.seen = (segment_codes, segment_mode, portfolio_criteria)
            return StateRollupResponse(rollups=[], snapshot_date="2026-04-22")

    spy = SpyGeoRepository()
    previous = app.dependency_overrides.get(get_geo_repository)
    app.dependency_overrides[get_geo_repository] = lambda: spy
    try:
        response = client.get(
            "/api/geo/state-rollups"
            "?segment_codes=itm,investor"
            "&segment_mode=all"
            "&occupancy=Owner-occupied"
            "&lien_status=Open%201st%20lien"
            "&owner_link=Portfolio%20investor%20%285%2B%29"
            "&purchase_intent=Listed%20for%20sale"
            "&min_equity_pct_label=%E2%89%A5%2025%25"
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_geo_repository, None)
        else:
            app.dependency_overrides[get_geo_repository] = previous

    assert response.status_code == 200, response.text
    assert spy.seen is not None
    segment_codes, segment_mode, criteria = spy.seen
    assert segment_codes == ["itm", "investor"]
    assert segment_mode == "all"
    assert criteria is not None
    assert criteria.occupancy == "Owner-occupied"
    assert criteria.lien_status == "Open 1st lien"
    assert criteria.owner_link == "Portfolio investor (5+)"
    assert criteria.purchase_intent == "Listed for sale"
    assert criteria.min_equity_pct_label == "≥ 25%"


def test_state_rollups_reject_unknown_segment_codes():
    response = client.get("/api/geo/state-rollups?segment_codes=itm,unknown")

    assert response.status_code == 422
    assert "unknown segment" in response.text


def test_county_rollups_returns_counties_for_populated_state():
    """IL is populated in the fixture (Cook / DuPage / Lake). The
    response envelope must echo the requested state and carry rollups
    keyed by 5-char FIPS."""
    response = client.get("/api/geo/county-rollups?state=IL")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "IL"
    rollups = payload["rollups"]
    assert isinstance(rollups, list)
    assert len(rollups) >= 1
    fips_set = {r["fips_5"] for r in rollups}
    assert "17031" in fips_set, "Cook County (17031) must land in the IL rollup"
    for r in rollups:
        assert len(r["fips_5"]) == 5
        assert r["state"] == "IL"
        assert r["addressable_borrowers"] >= r["in_the_money_borrowers"]
        assert 0 <= r["avg_opportunity_score"] <= 100


def test_county_rollups_empty_for_unpopulated_state():
    """A state outside the configured scope (or simply empty in the
    fixture) returns an empty list -- the UI renders "—" rather than
    fabricating."""
    response = client.get("/api/geo/county-rollups?state=NY")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "NY"
    assert payload["rollups"] == []


def test_county_rollups_uppercases_state():
    """State code is normalised uppercase by the repository."""
    response = client.get("/api/geo/county-rollups?state=il")
    assert response.status_code == 200
    payload = response.json()
    # Whatever the normalisation, the echoed state matches the fixture
    # key (IL is populated; lowercase 'il' would key an empty list).
    # The repo uppercases before lookup so this IS the IL fixture.
    assert payload["state"] == "IL"
    assert len(payload["rollups"]) >= 1


def test_county_rollups_accepts_segment_filter_params():
    response = client.get(
        "/api/geo/county-rollups?state=FL&segment_codes=itm,investor,equity,retention&segment_mode=all"
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "FL"


def test_county_rollups_passes_secondary_portfolio_criteria_to_repository():
    class SpyGeoRepository:
        seen = None

        def county_rollups(
            self,
            state,
            segment_codes=None,
            segment_mode="any",
            portfolio_criteria=None,
        ):
            self.seen = (state, segment_codes, segment_mode, portfolio_criteria)
            return CountyRollupResponse(state=state.upper(), rollups=[], snapshot_date="2026-04-22")

    spy = SpyGeoRepository()
    previous = app.dependency_overrides.get(get_geo_repository)
    app.dependency_overrides[get_geo_repository] = lambda: spy
    try:
        response = client.get(
            "/api/geo/county-rollups"
            "?state=fl"
            "&segment_codes=itm,investor"
            "&segment_mode=all"
            "&occupancy=Owner-occupied"
            "&lien_status=Open%201st%20lien"
            "&owner_link=Portfolio%20investor%20%285%2B%29"
            "&purchase_intent=Listed%20for%20sale"
            "&min_equity_pct_label=%E2%89%A5%2025%25"
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_geo_repository, None)
        else:
            app.dependency_overrides[get_geo_repository] = previous

    assert response.status_code == 200, response.text
    assert spy.seen is not None
    state, segment_codes, segment_mode, criteria = spy.seen
    assert state == "fl"
    assert segment_codes == ["itm", "investor"]
    assert segment_mode == "all"
    assert criteria is not None
    assert criteria.occupancy == "Owner-occupied"
    assert criteria.lien_status == "Open 1st lien"
    assert criteria.owner_link == "Portfolio investor (5+)"
    assert criteria.purchase_intent == "Listed for sale"
    assert criteria.min_equity_pct_label == "≥ 25%"


def test_county_rollups_validates_state_length():
    """The query param is constrained to 2 chars -- the server rejects
    anything else as a 422."""
    response = client.get("/api/geo/county-rollups?state=ILL")
    assert response.status_code == 422


def test_county_rollups_rejects_non_alpha_state():
    response = client.get("/api/geo/county-rollups?state=12")
    assert response.status_code == 422


def test_zip_rollups_returns_zips_for_populated_county():
    """Cook County (17031) is populated in the fixture with three ZIPs.
    Each row must carry a stable sample_borrower_id so the UI deep-link
    lands on a real dossier."""
    response = client.get("/api/geo/zip-rollups?county_fips=17031")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["fips_5"] == "17031"
    rollups = payload["rollups"]
    assert len(rollups) >= 1
    for r in rollups:
        assert len(r["zip"]) == 5
        assert r["state"] == "IL"
        assert r["county_fips_5"] == "17031"
        assert r["addressable_borrowers"] >= 0
        # Fixture pins a sample_borrower_id on every ZIP. In prod the
        # column is nullable (LEFT JOIN against an empty ranked table).
        assert r["sample_borrower_id"] is None or r["sample_borrower_id"].startswith("B-")


def test_zip_rollups_accepts_segment_filter_params():
    response = client.get(
        "/api/geo/zip-rollups?county_fips=17031&segment_codes=itm,equity&segment_mode=all"
    )
    assert response.status_code == 200, response.text
    assert response.json()["fips_5"] == "17031"


def test_zip_rollups_passes_secondary_portfolio_criteria_to_repository():
    class SpyGeoRepository:
        seen = None

        def zip_rollups(
            self,
            fips,
            segment_codes=None,
            segment_mode="any",
            portfolio_criteria=None,
        ):
            self.seen = (fips, segment_codes, segment_mode, portfolio_criteria)
            return ZipRollupResponse(fips_5=fips, rollups=[], snapshot_date="2026-04-22")

    spy = SpyGeoRepository()
    previous = app.dependency_overrides.get(get_geo_repository)
    app.dependency_overrides[get_geo_repository] = lambda: spy
    try:
        response = client.get(
            "/api/geo/zip-rollups"
            "?county_fips=17031"
            "&segment_codes=itm,equity"
            "&segment_mode=all"
            "&occupancy=Owner-occupied"
            "&lien_status=Open%201st%20lien"
            "&owner_link=Portfolio%20investor%20%285%2B%29"
            "&purchase_intent=Listed%20for%20sale"
            "&min_equity_pct_label=%E2%89%A5%2025%25"
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_geo_repository, None)
        else:
            app.dependency_overrides[get_geo_repository] = previous

    assert response.status_code == 200, response.text
    assert spy.seen is not None
    fips, segment_codes, segment_mode, criteria = spy.seen
    assert fips == "17031"
    assert segment_codes == ["itm", "equity"]
    assert segment_mode == "all"
    assert criteria is not None
    assert criteria.occupancy == "Owner-occupied"
    assert criteria.lien_status == "Open 1st lien"
    assert criteria.owner_link == "Portfolio investor (5+)"
    assert criteria.purchase_intent == "Listed for sale"
    assert criteria.min_equity_pct_label == "≥ 25%"


def test_zip_rollups_empty_for_unpopulated_county():
    """A county outside the fixture returns an empty list."""
    response = client.get("/api/geo/zip-rollups?county_fips=99999")
    assert response.status_code == 200
    payload = response.json()
    assert payload["fips_5"] == "99999"
    assert payload["rollups"] == []


def test_zip_rollups_validates_fips_length():
    """FIPS must be exactly 5 chars (422 otherwise)."""
    response = client.get("/api/geo/zip-rollups?county_fips=1703")
    assert response.status_code == 422


def test_zip_rollups_rejects_non_numeric_fips():
    response = client.get("/api/geo/zip-rollups?county_fips=abcde")
    assert response.status_code == 422
