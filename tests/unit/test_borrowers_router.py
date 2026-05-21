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

    proof = client.get("/api/borrowers/B-48291/proof")
    assert proof.status_code == 200
    proof_body = proof.json()
    assert proof_body["borrower_id"] == "B-48291"
    assert proof_body["trusted"] is True
    assert proof_body["opportunity_score"] == body["opportunity_score"]
    assert proof_body["signal_strength"] == body["confidence"]
    assert len(proof_body["score_components"]) == 5
    assert {item["key"] for item in proof_body["score_components"]} == {
        "economic_incentive",
        "intent_trigger",
        "fit",
        "relationship",
        "evidence",
    }
    assert all(query["sql_hash"] for query in proof_body["reproduce"])
    assert all("SELECT *" not in query["sql"].upper() for query in proof_body["reproduce"])


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


def test_borrower_proof_view_audit_uses_safe_metadata() -> None:
    audit = InMemoryAuditStore()
    previous = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        response = client.get(
            "/api/borrowers/B-48291/proof",
            headers={"X-Correlation-ID": "proof-view-audit"},
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous

    assert response.status_code == 200, response.text
    events = audit.list(limit=10, event_type="VIEW_BORROWER_PROOF")
    assert len(events) == 1
    assert events[0].correlation_id == response.headers["X-Correlation-ID"]
    assert events[0].payload_json["borrower_id"] == "B-48291"
    assert events[0].payload_json["source_assets"]
    assert events[0].payload_json["sql_hash"]
    assert events[0].payload_json["row_count"] == 1
    assert "sql" not in events[0].payload_json
