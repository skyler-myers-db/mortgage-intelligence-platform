from backend.services.county_names import county_name_for_fips


def test_county_name_for_fips_reads_shipped_topology() -> None:
    assert county_name_for_fips("12011") == "Broward"
    assert county_name_for_fips("17031") == "Cook"


def test_county_name_for_fips_handles_bad_values() -> None:
    assert county_name_for_fips(None) is None
    assert county_name_for_fips("999") is None
