from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_decision_inputs import DECISION_INPUT_KEYS
from backend.services.audit_store import get_audit_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

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


def test_borrower_view_audit_carries_correlation_and_decision_inputs() -> None:
    audit = InMemoryAuditStore()
    previous = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        response = client.get(
            "/api/borrowers/B-48291",
            headers={"X-Correlation-ID": "forensic-view-audit"},
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous

    assert response.status_code == 200, response.text
    assert response.headers["X-Correlation-ID"] == "forensic-view-audit"
    events = audit.list(limit=10, event_type="VIEW_BORROWER")
    assert len(events) == 1
    assert events[0].correlation_id == response.headers["X-Correlation-ID"]
    assert set(events[0].payload_json["decision_inputs"]) == set(DECISION_INPUT_KEYS)
