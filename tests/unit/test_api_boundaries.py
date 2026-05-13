"""API boundary guards.

Two narrow concerns covered here:

* R5-15: the SPA catch-all must not silently serve ``index.html`` for
  unmatched ``/api/*`` paths. Prior behavior masked typos / trailing-
  slash routing bugs by returning HTML with a 200 status, which
  suppressed monitoring signal and confused any JSON client.
* R5-14: GET /api/audit/events must reject ``?limit`` values outside
  the clamped range. The underlying ``mip_app.action_audit`` ledger
  grows unbounded; an unclamped limit would let a caller pull the
  whole table in a single request.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.api.data_estate as data_estate_api
import backend.services.databricks_sql as databricks_sql
from backend.api.config import _target_lender_options
from backend.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# R5-15: SPA fallback must not swallow unmatched /api/* requests
# ---------------------------------------------------------------------------


def test_unmatched_api_route_returns_json_404_not_html() -> None:
    """GET on an unregistered /api/* path must return a structured 404.

    The SPA catch-all (only active when ``frontend/dist/`` is present)
    previously fell through to ``index.html`` for any non-asset path,
    including mistyped API routes. The new contract returns
    ``{"detail": "not found"}`` with status 404 and JSON content-type.
    """
    response = client.get("/api/this-route-does-not-exist")
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith("application/json"), (
        response.headers.get("content-type")
    )
    body = response.json()
    assert body == {"detail": "not found"}


def test_unmatched_api_route_with_trailing_segments_also_404() -> None:
    """Nested paths under /api/ that don't match a registered route
    must 404, not serve the SPA shell."""
    response = client.get("/api/audit/events/does-not-exist/nested")
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith("application/json")


def test_generated_api_docs_are_not_exposed_by_default() -> None:
    """FastAPI docs/schema routes stay closed in shared app deploys.

    With the SPA catch-all mounted, a disabled docs route must not fall
    through to ``index.html`` with HTTP 200. A compact JSON 404 keeps the
    external contract uninteresting to authenticated workspace users.
    """
    for path in ("/openapi.json", "/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"].startswith("application/json"), path
        assert response.json() == {"detail": "not found"}


def test_browser_security_headers_are_present_on_api_responses() -> None:
    """Defense-in-depth browser headers are emitted by app middleware."""
    response = client.get("/api/this-route-does-not-exist")

    assert response.headers["strict-transport-security"].startswith(
        "max-age=31536000"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == (
        "geolocation=(), camera=(), microphone=()"
    )
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_data_estate_does_not_shadow_admin_auth_dependency_name() -> None:
    """Only RBAC-gated routers should expose an ``AdminDep`` alias."""
    assert "AdminDep" not in data_estate_api.__dict__
    assert "AdminRulesServiceDep" in data_estate_api.__dict__


# ---------------------------------------------------------------------------
# R5-14: /api/audit/events clamps caller-supplied limit
# ---------------------------------------------------------------------------


def test_audit_events_rejects_oversized_limit() -> None:
    """A caller passing ``?limit=1000`` (> MAX_AUDIT_LIMIT=500) gets 422.

    FastAPI's ``Query(le=500)`` returns 422 on violation, which the
    frontend already handles as a client-error banner. The stricter
    contract prevents a full-ledger scan triggered by a pathological
    or compromised client.
    """
    response = client.get("/api/audit/events?limit=1000")
    assert response.status_code == 422, response.text


def test_audit_events_rejects_zero_and_negative_limit() -> None:
    """``ge=1`` lower bound: 0 and negative values are 422, not 500."""
    assert client.get("/api/audit/events?limit=0").status_code == 422
    assert client.get("/api/audit/events?limit=-5").status_code == 422


def test_audit_events_accepts_limit_at_cap() -> None:
    """The boundary value ``limit=500`` must succeed -- it's the
    documented ceiling, not an off-by-one."""
    response = client.get("/api/audit/events?limit=500")
    assert response.status_code == 200, response.text


def test_public_audit_event_cannot_forge_genie_actions() -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "attacker@example.com",
            "action": "genie.save_borrowers",
            "entity_type": "genie_action",
            "entity_id": "msg-1",
            "event_type": "GENIE_ACTION_SAVE_BORROWERS",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "event type is owned by a governed server route"


def test_public_audit_event_masks_subject_clip() -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "anonymous",
            "action": "view.custom",
            "entity_type": "lead_queue",
            "entity_id": "manual",
            "subject_clip": "1234567890",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["subject_clip"].startswith("clip_ref_")


def test_public_audit_event_rejects_top_level_pii_values() -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "anonymous",
            "action": "view.custom",
            "entity_type": "lead_queue",
            "entity_id": "jane@example.com",
            "subject_segment": "SSN 123-45-6789",
            "request_id": "555-212-3333",
        },
    )

    assert response.status_code == 422


def test_public_audit_event_rejects_name_shaped_top_level_values() -> None:
    for field in ("action", "entity_type", "entity_id", "event_type", "subject_segment"):
        payload = {
            "actor": "anonymous",
            "action": "view.custom",
            "entity_type": "lead_queue",
            "entity_id": "manual",
        }
        payload[field] = "Jane Smith"
        response = client.post("/api/audit/event", json=payload)
        assert response.status_code == 422, field


def test_config_target_lender_options_drop_raw_lender_names(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        def execute(self, sql: str) -> list[dict[str, object]]:
            _ = sql
            return [
                {"current_lender_ref": "Summit Mortgage"},
                {"current_lender_ref": "Wells Fargo Bank"},
                {"current_lender_ref": "Competitor B"},
            ]

    monkeypatch.setattr(databricks_sql, "get_sql_client", lambda: _Client())

    values, status = _target_lender_options()

    assert status == "live"
    assert values == ["All", "Summit Mortgage", "Competitor B"]
