from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.admin_rules import SourceRow
from backend.services.data_estate import build_data_estate_response

client = TestClient(app)

# The payload emits Catalog Explorer deep links, so the route requires the
# forwarded workspace identity (conftest only defaults the admin group
# header, never an identity header).
ACTOR_HEADERS = {"X-Forwarded-Email": "analyst@summit.example"}


def test_data_estate_route_returns_lane_inventory() -> None:
    response = client.get("/api/data-estate", headers=ACTOR_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["public_demo_masking"] is True
    assert isinstance(body["lanes"], list)


@pytest.mark.parametrize("path", ["/api/data-estate", "/api/v1/data-estate"])
def test_data_estate_rejects_anonymous_callers(path: str) -> None:
    """No forwarded identity -> 401. The payload embeds workspace deep-link
    URLs, so a non-Apps deploy must not serve it to anonymous callers
    (GRANTS §11a). The conftest default ``X-Forwarded-Groups: mip-admin``
    header is still present on this request, which also pins that a group
    claim alone never authenticates."""
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "authenticated identity required"}


def test_data_estate_rejects_spoofed_identity_when_edge_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trust_forwarded_headers=False (non-Apps deploy) fails closed even
    when the caller supplies identity headers."""
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)
    response = client.get("/api/data-estate", headers=ACTOR_HEADERS)
    assert response.status_code == 401


def test_data_estate_accepts_forwarded_user_without_email() -> None:
    """Apps may forward only ``X-Forwarded-User`` for service identities."""
    response = client.get(
        "/api/data-estate",
        headers={"X-Forwarded-User": "svc-mip-automation"},
    )
    assert response.status_code == 200


def test_data_estate_separates_first_party_from_live_cotality() -> None:
    estate = build_data_estate_response(
        (
            SourceRow("Cotality Public Records", "live", 10, "2026-05-06", "ok"),
            SourceRow("Voluntary Lien", "live", 20, "2026-05-06", "ok"),
            SourceRow("First-party LOS / Applications", "not_configured", 0, None, "not connected"),
            SourceRow("MLS Listings", "live", 1200, "2026-06-12", "live"),
            SourceRow("Cotality HELOC Propensity", "live", 900, "2026-06-12", "live"),
            SourceRow("Cotality Refi Propensity", "live", 850, "2026-06-12", "live"),
            SourceRow("Building Permits", "roadmap", None, None, "pending"),
        )
    )

    first_party = next(lane for lane in estate.lanes if lane.id == "first_party")
    cotality = next(lane for lane in estate.lanes if lane.id == "cotality")

    assert first_party.status == "not_configured"
    assert any(asset.uc_object == "mip.first_party.loan_applications" for asset in first_party.assets)
    assert cotality.status == "roadmap"
    assert "Cotality MLS/Listings Delta Share is pending." not in estate.known_data_gaps
    assert "Cotality Building Permits Delta Share is pending." in estate.known_data_gaps
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
    assert "First-party lender feeds use demo/synthetic rows in this workspace." in estate.known_data_gaps
    assert "Customer first-party data feeds are not connected in this demo workspace." not in estate.known_data_gaps


def test_data_estate_assets_include_catalog_explorer_links(monkeypatch):
    monkeypatch.setattr(settings, "databricks_host", "https://dbc-test.cloud.databricks.com")

    estate = build_data_estate_response(
        (
            SourceRow("UC Gold Lead Population", "live", 100, "2026-06-15", "ok"),
        )
    )

    databricks_lane = next(lane for lane in estate.lanes if lane.id == "databricks")
    lead_population = next(
        asset for asset in databricks_lane.assets if asset.uc_object == "mip.gold.lead_population"
    )
    lakebase = next(asset for asset in databricks_lane.assets if asset.uc_object == "mip_app.*")

    assert lead_population.catalog_explorer_url == (
        "https://dbc-test.cloud.databricks.com/explore/data/mip/gold/lead_population"
    )
    assert lakebase.catalog_explorer_url is None
