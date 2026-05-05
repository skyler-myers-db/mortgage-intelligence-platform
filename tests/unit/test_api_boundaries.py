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

from fastapi.testclient import TestClient

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
