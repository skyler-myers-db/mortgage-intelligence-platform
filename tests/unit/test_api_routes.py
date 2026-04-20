from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_required_routes_exist_and_respond():
    checks = [
        ("get", "/api/health", None, 200),
        ("get", "/api/config/options", None, 200),
        ("post", "/api/portfolio/preview", {"criteria": {"state": "CA"}}, 200),
        ("post", "/api/portfolio/create", {"name": "Demo"}, 200),
        ("get", "/api/portfolio/demo", None, 200),
        ("get", "/api/segments?portfolio_id=demo", None, 200),
        ("get", "/api/leads?portfolio_id=demo&segment=equity_rich", None, 200),
        ("get", "/api/borrowers/b1", None, 200),
        ("get", "/api/borrowers/b1/evidence", None, 200),
        ("post", "/api/offers/recommend", {"borrower_id": "b1"}, 200),
        ("post", "/api/outreach/draft", {"channel": "email"}, 200),
        ("post", "/api/outreach/approve", {"message_id": "m1"}, 200),
        ("post", "/api/genie/start", {"context": {}}, 200),
        ("post", "/api/genie/message", {"conversation_id": "c1", "message": "hi"}, 200),
        ("post", "/api/audit/event", {"event_type": "view"}, 200),
        ("get", "/api/admin/rules", None, 200),
        ("put", "/api/admin/rules", {"x": "y"}, 200),
    ]

    for method, path, payload, expected in checks:
        call = getattr(client, method)
        response = call(path, json=payload) if payload is not None else call(path)
        assert response.status_code == expected, f"{method.upper()} {path} returned {response.status_code}"
