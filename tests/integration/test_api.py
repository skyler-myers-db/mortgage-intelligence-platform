from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").status_code == 200


def test_segments():
    res = client.get("/api/segments")
    assert res.status_code == 200
    assert any(s["code"] == "itm" for s in res.json())


def test_approve_writes_audit():
    res = client.post("/api/outreach/approve", json={"borrower_id": "B-48291", "actor": "test"})
    assert res.status_code == 200
    assert res.json()["approved"] is True
