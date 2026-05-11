from backend.services.geography_scope import build_geography_scope


def test_geography_scope_label_is_count_driven() -> None:
    scope = build_geography_scope(
        [
            {
                "state": "IL",
                "fips_5": "17031",
                "county_name": "Cook",
                "addressable_borrowers": 10,
                "snapshot_date": "2026-05-07",
            },
            {
                "state": "IL",
                "fips_5": "17043",
                "county_name": "DuPage",
                "addressable_borrowers": 5,
                "snapshot_date": "2026-05-07",
            },
            {
                "state": "TX",
                "fips_5": "48113",
                "county_name": "Dallas",
                "addressable_borrowers": 8,
                "snapshot_date": "2026-05-07",
            },
        ],
        zip_count=42,
    )

    assert scope.scope_label == "Cotality data coverage: 3 counties across 2 states"
    assert scope.state_scope_label("IL") == (
        "Cotality data coverage: 3 counties across 2 states; "
        "2 counties available in IL"
    )
    api_payload = scope.to_api_dict()
    assert api_payload["county_count"] == 3
    assert api_payload["state_count"] == 2
    assert api_payload["zip_count"] == 42
    assert api_payload["counties"][0]["fips_5"] == "17031"


def test_geography_scope_drops_malformed_rows() -> None:
    scope = build_geography_scope(
        [
            {"state": "IL", "fips_5": "17031", "addressable_borrowers": 10},
            {"state": "I", "fips_5": "17043", "addressable_borrowers": 10},
            {"state": "TX", "fips_5": "4811", "addressable_borrowers": 10},
        ],
        zip_count=None,
    )

    assert scope.county_count == 1
    assert scope.state_count == 1
    assert scope.zip_count is None
