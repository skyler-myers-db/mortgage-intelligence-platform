from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_decision_inputs import (
    DECISION_INPUT_KEYS,
    decision_inputs_from_offer_inputs,
)
from backend.services.audit_store import get_audit_store
from backend.services.repositories import get_offer_repository
from tests.fixtures import mock_population
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)


def test_offers_router_recommends_governed_offer() -> None:
    response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})
    assert response.status_code == 200
    body = response.json()
    assert body["borrower_id"] == "B-48291"
    assert body["offer_code"]
    assert body["sources"]
    assert body["thresholds_applied"]


def test_offers_router_returns_404_for_unknown_borrower() -> None:
    response = client.post("/api/offers/recommend", json={"borrower_id": "B-0000000000000"})
    assert response.status_code == 404


def test_offers_router_uses_refresh_applied_thresholds_from_offer_inputs() -> None:
    class ThresholdStubOfferRepository:
        def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
            if borrower_id != "B-48291":
                return None
            return {
                "rate_spread_bps": 88,
                "equity_pct": 39,
                "has_permit": False,
                "listed_for_sale": False,
                "is_investor": False,
                "is_current_customer": False,
                "is_competitor_lien": False,
                "offer_code": "refi",
                "min_spread_bps": 88,
                "min_equity_pct": 11,
                "heloc_equity_min_pct": 42,
                "cashout_equity_min_pct": 29,
                "retention_min_spread_bps": 61,
            }

    app.dependency_overrides[get_offer_repository] = ThresholdStubOfferRepository

    response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})

    assert response.status_code == 200
    body = response.json()
    assert body["thresholds_applied"] == {
        "min_spread_bps": 88,
        "min_equity_pct": 11,
        "heloc_equity_min_pct": 42,
        "cashout_equity_min_pct": 29,
        "retention_min_spread_bps": 61,
    }
    assert body["offer_code"] == "refi"
    assert body["alternatives"][0]["reason_not_chosen"] == (
        "Equity 39% is below the HELOC threshold (42%); cross-sell would not underwrite."
    )


def test_recommend_offer_audit_captures_decision_inputs() -> None:
    audit = InMemoryAuditStore()
    previous = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        response = client.post(
            "/api/offers/recommend",
            json={"borrower_id": "B-48291"},
            headers={"X-Correlation-ID": "forensic-offer-audit"},
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous

    assert response.status_code == 200, response.text
    events = audit.list(limit=10, event_type="RECOMMEND_OFFER")
    assert len(events) == 1
    metadata = events[0].payload_json
    assert events[0].correlation_id == response.headers["X-Correlation-ID"]
    assert set(metadata["decision_inputs"]) == set(DECISION_INPUT_KEYS)
    expected = mock_population.BORROWER_OFFER_INPUTS["B-48291"]
    assert metadata["decision_inputs"] == decision_inputs_from_offer_inputs(expected)
