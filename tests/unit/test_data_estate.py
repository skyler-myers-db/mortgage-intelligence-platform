from __future__ import annotations

from backend.services.admin_rules import SourceRow
from backend.services.data_estate import build_data_estate_response


def test_data_estate_separates_first_party_from_live_cotality() -> None:
    estate = build_data_estate_response(
        (
            SourceRow("Cotality Public Records", "live", 10, "2026-05-06", "ok"),
            SourceRow("Voluntary Lien", "live", 20, "2026-05-06", "ok"),
            SourceRow("First-party LOS / Applications", "not_configured", 0, None, "not connected"),
            SourceRow("MLS", "roadmap", None, None, "pending"),
            SourceRow("Building Permits", "roadmap", None, None, "pending"),
        )
    )

    first_party = next(lane for lane in estate.lanes if lane.id == "first_party")
    cotality = next(lane for lane in estate.lanes if lane.id == "cotality")

    assert first_party.status == "not_configured"
    assert any(asset.uc_object == "mip.first_party.loan_applications" for asset in first_party.assets)
    assert cotality.status == "roadmap"
    assert "Cotality MLS/Listings Delta Share is pending." in estate.known_data_gaps
    assert estate.public_demo_masking is True


def test_data_estate_does_not_mark_empty_first_party_as_live() -> None:
    estate = build_data_estate_response(
        (
            SourceRow("First-party Servicing Portfolio", "configured_empty", 0, None, "empty"),
        )
    )

    first_party = next(lane for lane in estate.lanes if lane.id == "first_party")
    assert first_party.status == "not_configured"
    assert any(asset.status == "configured_empty" for asset in first_party.assets)


def test_data_estate_discloses_synthetic_first_party_demo_feeds() -> None:
    estate = build_data_estate_response(
        (
            SourceRow(
                "First-party LOS / Applications",
                "demo_synthetic",
                1200,
                "2026-05-06",
                "Summit Mortgage synthetic LOS/application feed · connected",
                synthetic_demo=True,
            ),
            SourceRow(
                "First-party Servicing Portfolio",
                "demo_synthetic",
                800,
                "2026-05-06",
                "Summit Mortgage synthetic servicing feed · connected",
                synthetic_demo=True,
            ),
            SourceRow(
                "First-party CRM / Campaigns",
                "demo_synthetic",
                1800,
                "2026-05-06",
                "Summit Mortgage synthetic CRM/campaign feed · connected",
                synthetic_demo=True,
            ),
            SourceRow(
                "First-party Customer Interactions",
                "demo_synthetic",
                2100,
                "2026-05-06",
                "Summit Mortgage synthetic interaction feed · connected",
                synthetic_demo=True,
            ),
            SourceRow(
                "First-party Product Balances",
                "demo_synthetic",
                1400,
                "2026-05-06",
                "Summit Mortgage synthetic banking-product feed · connected",
                synthetic_demo=True,
            ),
        )
    )

    first_party = next(lane for lane in estate.lanes if lane.id == "first_party")
    assert first_party.status == "demo_synthetic"
    assert all(asset.synthetic_demo for asset in first_party.assets)
    assert {asset.status for asset in first_party.assets} == {"demo_synthetic"}
    assert (
        "First-party lender feeds are synthetic Summit Mortgage demo feeds, "
        "not real customer data."
    ) in estate.known_data_gaps
    assert "Customer first-party data feeds are not connected in this demo workspace." not in estate.known_data_gaps
