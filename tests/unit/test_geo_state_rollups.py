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
            "?segment_codes=ITM,Investor"
            "&segment_mode=ALL"
            "&occupancy=Owner-occupied"
            "&lien_status=Open%201st%20lien"
            "&lender_relationship=Competitor%20customer"
            "&target_lender_ref=Competitor%20B"
            "&owner_link=Portfolio%20investor%20%285%2B%29"
            "&purchase_intent=Listed%20for%20sale"
            "&min_equity_pct_label=%E2%89%A5%2025%25"
            "&marketing_eligibility=Eligible%20only"
            "&consent_status=Opt-in"
            "&recency=Untouched%2030d"
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
    assert criteria.lender_relationship == "Competitor customer"
    assert criteria.target_lender_ref == "Competitor B"
    assert criteria.owner_link == "Portfolio investor (5+)"
    assert criteria.purchase_intent == "Listed for sale"
    assert criteria.min_equity_pct_label == "≥ 25%"
    assert criteria.marketing_eligibility == "Eligible only"
    assert criteria.consent_status == "Opt-in"
    assert criteria.recency == "Untouched 30d"


def test_state_rollups_reject_unknown_segment_codes():
    response = client.get("/api/geo/state-rollups?segment_codes=itm,unknown")

    assert response.status_code == 422
    assert "unknown segment" in response.text


def test_state_rollups_reject_raw_target_lender_ref():
    response = client.get("/api/geo/state-rollups?target_lender_ref=Wells%20Fargo%20Bank")

    assert response.status_code == 422
    assert "target_lender_ref" in response.text


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
            "&segment_codes=ITM,Investor"
            "&segment_mode=ALL"
            "&occupancy=Owner-occupied"
            "&lien_status=Open%201st%20lien"
            "&lender_relationship=Competitor%20customer"
            "&target_lender_ref=Competitor%20B"
            "&owner_link=Portfolio%20investor%20%285%2B%29"
            "&purchase_intent=Listed%20for%20sale"
            "&min_equity_pct_label=%E2%89%A5%2025%25"
            "&marketing_eligibility=Eligible%20only"
            "&consent_status=Opt-in"
            "&recency=Untouched%2030d"
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
    assert criteria.lender_relationship == "Competitor customer"
    assert criteria.target_lender_ref == "Competitor B"
    assert criteria.owner_link == "Portfolio investor (5+)"
    assert criteria.purchase_intent == "Listed for sale"
    assert criteria.min_equity_pct_label == "≥ 25%"
    assert criteria.marketing_eligibility == "Eligible only"
    assert criteria.consent_status == "Opt-in"
    assert criteria.recency == "Untouched 30d"


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


def test_zip_rollups_returns_zips_for_state():
    """The live drill key. `state` echoes back and `fips_5` stays null so a
    reader can tell which grain answered."""
    response = client.get("/api/geo/zip-rollups?state=il")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "IL"
    assert payload["fips_5"] is None
    assert len(payload["rollups"]) >= 1
    for r in payload["rollups"]:
        assert len(r["zip"]) == 5
        assert r["state"] == "IL"


def test_zip_rollups_requires_exactly_one_geography_key():
    """Neither key would scan the country; both name two geographies with
    no single honest answer. Both are 422."""
    assert client.get("/api/geo/zip-rollups").status_code == 422
    assert (
        client.get("/api/geo/zip-rollups?state=IL&county_fips=17031").status_code == 422
    )


def test_zip_rollups_validates_state_shape():
    assert client.get("/api/geo/zip-rollups?state=ILL").status_code == 422
    assert client.get("/api/geo/zip-rollups?state=12").status_code == 422


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
            fips=None,
            segment_codes=None,
            segment_mode="any",
            portfolio_criteria=None,
            *,
            state=None,
        ):
            self.seen = (fips, segment_codes, segment_mode, portfolio_criteria)
            return ZipRollupResponse(
                fips_5=fips, state=state, rollups=[], snapshot_date="2026-04-22"
            )

    spy = SpyGeoRepository()
    previous = app.dependency_overrides.get(get_geo_repository)
    app.dependency_overrides[get_geo_repository] = lambda: spy
    try:
        response = client.get(
            "/api/geo/zip-rollups"
            "?county_fips=17031"
            "&segment_codes=ITM,Equity"
            "&segment_mode=ALL"
            "&occupancy=Owner-occupied"
            "&lien_status=Open%201st%20lien"
            "&lender_relationship=Competitor%20customer"
            "&target_lender_ref=Competitor%20B"
            "&owner_link=Portfolio%20investor%20%285%2B%29"
            "&purchase_intent=Listed%20for%20sale"
            "&min_equity_pct_label=%E2%89%A5%2025%25"
            "&marketing_eligibility=Eligible%20only"
            "&consent_status=Opt-in"
            "&recency=Untouched%2030d"
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
    assert criteria.lender_relationship == "Competitor customer"
    assert criteria.target_lender_ref == "Competitor B"
    assert criteria.owner_link == "Portfolio investor (5+)"
    assert criteria.purchase_intent == "Listed for sale"
    assert criteria.min_equity_pct_label == "≥ 25%"
    assert criteria.marketing_eligibility == "Eligible only"
    assert criteria.consent_status == "Opt-in"
    assert criteria.recency == "Untouched 30d"


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


# ---------------------------------------------------------------------------
# 2026-08-08 UX walk: the state -> ZIP drill loses borrowers on the way down.
#
# gold.zip_rollup is keyed on a 5-digit ZIP and filters `LENGTH(zip) = 5`, but
# the Cotality share does not carry a usable ZIP for every property (live:
# CO 8.7%, WA 5.8%, IL 0.5%). Adding up a state's ZIP tiles therefore lands
# BELOW its state tile, and nothing on screen said why. The API now carries
# the gap as a machine-readable field so the UI can disclose it.
# ---------------------------------------------------------------------------


def test_state_rollups_disclose_zip_drill_coverage() -> None:
    response = client.get("/api/geo/state-rollups")
    assert response.status_code == 200, response.text
    for row in response.json()["rollups"]:
        assert "zip_unassigned_count" in row, (
            "every state row must disclose how many borrowers the ZIP layer "
            "will not show — a silent drop is what made the sums disagree"
        )
        assert row["zip_unassigned_count"] >= 0
        assert row["zip_unassigned_count"] <= row["addressable"]


def test_state_rollups_disclose_the_contactable_subset() -> None:
    """Every state tile states BOTH numbers.

    The tile's headline is the addressable population; the Lead Queue it
    links to applies the contact-eligibility predicate and shows a strict
    subset (live 2026-08-11: IL 76,711 of 1,851,040, a 24x gap). Reporting
    only the headline sends the reader to a much smaller queue with no
    explanation, so the wire carries both and the relationship must hold.
    """
    response = client.get("/api/geo/state-rollups")
    assert response.status_code == 200, response.text
    rows = response.json()["rollups"]
    assert rows
    for row in rows:
        assert "contactable" in row, (
            "every state row must state the contactable subset alongside "
            "addressable — one number on the tile and another in the queue "
            "is what made the map read as a broken link"
        )
        if row["contactable"] is None:
            continue
        assert row["contactable"] >= 0
        assert row["contactable"] <= row["addressable"]


def test_state_tile_contactable_is_computed_by_the_eligibility_rule() -> None:
    """The fixture must DERIVE the subset, not declare it.

    ``contactable`` used to be a hardcoded ~4% of ``addressable`` on every
    fixture row. Two things followed: the number could not move when the
    eligibility rule moved, and it was identical under every filter — so no
    route-level test could tell a tile that reports ``contactable`` from one
    that reports ``addressable``, which is exactly the map-tooltip bug
    (adversarial review 2026-08-11).
    """

    from backend.services.eligibility import GoldEligibilityService
    from tests.fixtures.in_process_repos import _FIXTURE_STATE_POPULATION

    eligibility = GoldEligibilityService()
    rows = {row["state"]: row for row in client.get("/api/geo/state-rollups").json()["rollups"]}
    assert rows
    for state, borrowers in _FIXTURE_STATE_POPULATION.items():
        row = rows[state]
        assert row["contactable"] == sum(
            1 for borrower in borrowers if eligibility.evaluate(borrower).eligible
        ), f"{state} reports a contactable count the eligibility rule did not produce"
        # Strictly smaller, so swapping the two fields is visible.
        assert 0 < row["contactable"] < row["addressable"]

    filtered = client.get("/api/geo/state-rollups?segment_codes=itm")
    assert filtered.status_code == 200, filtered.text
    filtered_rows = {row["state"]: row for row in filtered.json()["rollups"]}
    assert filtered_rows, "a segment filter must not empty the fixture map"
    moved = [
        state
        for state, row in filtered_rows.items()
        if row["addressable"] < rows[state]["addressable"]
        and row["contactable"] < rows[state]["contactable"]
    ]
    assert moved, "both numbers must narrow with the filter, not just the headline"
    for row in filtered_rows.values():
        assert 0 < row["contactable"] < row["addressable"]


def test_segment_summaries_disclose_the_contactable_subset() -> None:
    """Same contract on the segment cards, which have the same 23x gap."""
    response = client.get("/api/segments")
    assert response.status_code == 200, response.text
    segments = response.json()
    assert segments
    for segment in segments:
        assert "contactable" in segment, (
            "every segment card must state the contactable subset alongside "
            "its headline count"
        )
        if segment["contactable"] is None:
            continue
        assert segment["contactable"] >= 0
        assert segment["contactable"] <= segment["count"]


def test_state_rollup_zip_gap_is_derived_from_the_zip_layer_itself() -> None:
    """Pin the derivation, not a number.

    The disclosed gap is ``state total - SUM(zip_rollup.addressable_borrowers)``
    for the same snapshot, so it equals the drill gap by construction. A
    separately-computed "count of null ZIPs" could drift from the tiles the
    reader is actually adding up; this one cannot.
    """
    from backend.services.repositories.databricks_geo import DatabricksGeoRepository

    sql = " ".join(DatabricksGeoRepository._STATE_SQL.split())
    assert "AS zip_unassigned" in sql
    assert "CAST(SUM(addressable_borrowers) AS BIGINT) AS zip_covered" in sql
    assert "f.addressable_borrowers - COALESCE(zc.zip_covered, 0)" in sql
    # The filtered path must disclose the same thing against the filtered
    # universe — a segment filter that hides ZIP-less borrowers must not
    # silently reset the disclosure to zero.
    filtered = " ".join(DatabricksGeoRepository._STATE_FILTER_SQL_TPL.split())
    assert "zip IS NULL OR LENGTH(zip) <> 5" in filtered
    assert "AS zip_unassigned" in filtered
