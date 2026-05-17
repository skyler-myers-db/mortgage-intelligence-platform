from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_borrowers_router_returns_dossier_and_evidence() -> None:
    dossier = client.get("/api/borrowers/B-48291")
    assert dossier.status_code == 200
    body = dossier.json()
    assert body["borrower_id"] == "B-48291"
    assert "opportunity_score" in body

    evidence = client.get("/api/borrowers/B-48291/evidence")
    assert evidence.status_code == 200
    assert isinstance(evidence.json(), list)


def test_borrowers_router_search_and_lifecycle_surfaces() -> None:
    search = client.get("/api/borrowers/search?q=Chicago")
    assert search.status_code == 200
    assert isinstance(search.json(), list)

    lifecycle = client.get(
        "/api/borrowers/B-48291/lifecycle",
        headers={"X-Forwarded-Email": "skyler@entrada.ai"},
    )
    assert lifecycle.status_code == 200
    assert "approval_status" in lifecycle.json()


def test_borrowers_router_rejects_invalid_public_id() -> None:
    response = client.get("/api/borrowers/raw-clip-123")
    assert response.status_code == 422
