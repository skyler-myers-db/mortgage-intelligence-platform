from fastapi.testclient import TestClient

from backend.main import app

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
