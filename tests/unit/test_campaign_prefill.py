"""Contract tests for the geo → campaigns prefill schema (S9).

The query-parameter encoding is the wire contract the S10 campaign
builder will consume; these tests pin the round-trip and the rejection
behaviour so a drive-by rename breaks loudly.
"""
from __future__ import annotations

import pytest

from backend.schemas.campaign_prefill import (
    PARAM_SOURCE,
    PREFILL_SOURCE,
    CampaignGeoPrefill,
)


def _zip_prefill(**overrides: object) -> CampaignGeoPrefill:
    fields: dict[str, object] = {
        "level": "zip",
        "state": "IL",
        "county_fips": "17031",
        "county_name": "Cook",
        "zip": "60611",
        "segment_codes": ["itm", "equity"],
        "segment_mode": "all",
        "lead_count": 64,
        "unattended_count": 43,
    }
    fields.update(overrides)
    return CampaignGeoPrefill(**fields)  # type: ignore[arg-type]


class TestRoundTrip:
    def test_zip_level_round_trips_through_query_params(self) -> None:
        original = _zip_prefill()
        decoded = CampaignGeoPrefill.from_query_params(original.to_query_params())
        assert decoded == original

    def test_state_level_round_trips_minimal_payload(self) -> None:
        original = CampaignGeoPrefill(level="state", state="tx")
        params = original.to_query_params()
        assert params["states"] == "TX"
        assert "prefill_county_fips" not in params
        assert "prefill_zip" not in params
        assert "prefill_segments" not in params
        decoded = CampaignGeoPrefill.from_query_params(params)
        assert decoded == original

    def test_county_level_round_trips(self) -> None:
        original = CampaignGeoPrefill(
            level="county",
            state="IL",
            county_fips="17031",
            county_name="Cook",
            segment_codes=["retention"],
        )
        decoded = CampaignGeoPrefill.from_query_params(original.to_query_params())
        assert decoded == original

    def test_states_param_reuses_portfolio_builder_deep_link_key(self) -> None:
        # The one param today's surface applies as a real predicate.
        params = _zip_prefill().to_query_params()
        assert params["states"] == "IL"
        assert params[PARAM_SOURCE] == PREFILL_SOURCE


class TestDecodeBehaviour:
    def test_absent_marker_returns_none(self) -> None:
        assert CampaignGeoPrefill.from_query_params({}) is None
        assert (
            CampaignGeoPrefill.from_query_params({"states": "IL", "occupancy": "All"})
            is None
        )

    def test_marked_but_malformed_raises(self) -> None:
        params = _zip_prefill().to_query_params()
        params["prefill_county_fips"] = "1703"  # 4 digits
        with pytest.raises(ValueError):
            CampaignGeoPrefill.from_query_params(params)

    def test_unknown_segment_code_raises(self) -> None:
        params = _zip_prefill().to_query_params()
        params["prefill_segments"] = "itm,granite"
        with pytest.raises(ValueError, match="granite"):
            CampaignGeoPrefill.from_query_params(params)

    def test_multi_state_deep_link_uses_first_state(self) -> None:
        params = _zip_prefill().to_query_params()
        params["states"] = "IL,CA"
        decoded = CampaignGeoPrefill.from_query_params(params)
        assert decoded is not None and decoded.state == "IL"

    def test_negative_count_rejected(self) -> None:
        params = _zip_prefill().to_query_params()
        params["prefill_lead_count"] = "-5"
        with pytest.raises(ValueError):
            CampaignGeoPrefill.from_query_params(params)


class TestValidation:
    def test_zip_level_requires_zip_and_county(self) -> None:
        with pytest.raises(ValueError):
            CampaignGeoPrefill(level="zip", state="IL", county_fips="17031")
        with pytest.raises(ValueError):
            CampaignGeoPrefill(level="zip", state="IL", zip="60611")

    def test_county_level_requires_county_fips(self) -> None:
        with pytest.raises(ValueError):
            CampaignGeoPrefill(level="county", state="IL")

    def test_state_level_rejects_child_geo(self) -> None:
        with pytest.raises(ValueError):
            CampaignGeoPrefill(level="state", state="IL", county_fips="17031")

    def test_segment_codes_normalise_and_dedupe(self) -> None:
        prefill = CampaignGeoPrefill(
            level="state", state="IL", segment_codes=["ITM", "itm", " equity "]
        )
        assert prefill.segment_codes == ["itm", "equity"]
