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
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

import backend.api.config as config_api
import backend.api.data_estate as data_estate_api
import backend.services.databricks_sql as databricks_sql
from backend.api.config import _target_lender_options
from backend.main import SecurityHeadersMiddleware, app

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


def test_versioned_api_route_is_primary_and_emits_api_version_header() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200, response.text
    assert response.headers["x-api-version"] == "v1"


def test_unversioned_api_route_stays_as_compatibility_alias() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    assert response.headers["x-api-version"] == "v1"


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


def test_hashed_static_assets_are_compressed_and_immutable() -> None:
    """Vite asset responses should be gzip-compressed and cache-immutable.

    Keep this clean-checkout safe: GitHub's backend job runs before the
    frontend build, so ``frontend/dist`` is intentionally absent there.
    This mini app exercises the same middleware stack contract without
    depending on generated files.
    """

    static_app = FastAPI()

    @static_app.get("/assets/index-test.js")
    def _asset() -> Response:
        return Response("const mip = 'x';\n" * 256, media_type="application/javascript")

    static_app.add_middleware(SecurityHeadersMiddleware)
    static_app.add_middleware(GZipMiddleware, minimum_size=1024)
    response = TestClient(static_app).get(
        "/assets/index-test.js",
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


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


def test_audit_events_filters_by_correlation_id() -> None:
    correlation_id = "audit-corr-filter-001"
    created = client.post(
        "/api/audit/event",
        headers={"X-Correlation-ID": correlation_id},
        json={
            "actor": "anonymous",
            "action": "view.custom",
            "entity_type": "lead_queue",
            "entity_id": "manual",
        },
    )

    assert created.status_code == 200, created.text
    assert created.json()["correlation_id"] == correlation_id

    response = client.get(f"/api/audit/events?correlation_id={correlation_id}")
    assert response.status_code == 200, response.text
    events = response.json()
    assert events
    assert {event["correlation_id"] for event in events} == {correlation_id}


@pytest.mark.parametrize(
    "unsafe_correlation_id",
    [
        "555-212-3333",
        "trace123456789",
        "trace1778869254",
        "req_1778869254",
        "abc555-212-3333xyz",
        "abcB-102FL7THC6Q3Lxyz",
        "xCL-123456789y",
    ],
)
def test_audit_events_rejects_pii_shaped_correlation_filter(
    unsafe_correlation_id: str,
) -> None:
    response = client.get(f"/api/audit/events?correlation_id={unsafe_correlation_id}")

    assert response.status_code == 422


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


@pytest.mark.parametrize(
    "server_owned_event_type",
    [
        "APPROVE",
        "CAMPAIGN_STATUS_UPDATE",
        "CALL_DISPOSITION",
        "DELETE_DRAFT",
        "DRAFT_OUTREACH",
        "LEAD_ASSIGN",
        "LEAD_DISTRIBUTE",
        "OUTREACH_REJECT",
        "PORTFOLIO_CREATE",
        "RECOMMEND_OFFER",
        "RUN_GENIE",
        "SAVE_DRAFT",
        "SAVE_LEAD",
        "UNSAVE_LEAD",
        "VIEW_BORROWER",
        "VIEW_LEADS",
    ],
)
def test_public_audit_event_cannot_forge_server_owned_events(
    server_owned_event_type: str,
) -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "attacker@example.com",
            "action": "view.custom",
            "entity_type": "lead_queue",
            "entity_id": "manual",
            "event_type": server_owned_event_type,
        },
    )

    assert response.status_code == 400, server_owned_event_type
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


def test_public_audit_event_requires_json_content_type() -> None:
    response = client.post(
        "/api/audit/event",
        content='{"action":"view.custom","entity_type":"lead_queue","entity_id":"manual"}',
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Unsupported content type"


def test_public_audit_event_rejects_nested_metadata_pii() -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "anonymous",
            "action": "view.custom",
            "entity_type": "genie",
            "entity_id": "manual",
            "payload_json": {
                "source": {
                    "owner_address": "123 Main St",
                    "free_text": "mailing address 123 Main St",
                }
            },
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "owner_address" in detail
    assert "metadata.source.free_text" in detail


@pytest.mark.parametrize("container", ["tool_steps", "policy_checks", "governance_chips"])
def test_public_audit_event_rejects_pii_in_growth_agent_proof_lists(container: str) -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "anonymous",
            "action": "view.custom",
            "entity_type": "genie",
            "entity_id": "manual",
            "payload_json": {
                container: [
                    {
                        "label": "Reviewed proof",
                        "status": "completed",
                        "detail": "mailing address 123 Main St",
                    }
                ]
            },
        },
    )

    assert response.status_code == 422
    assert "PII-shaped" in response.json()["detail"]


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
    monkeypatch.setattr(config_api.settings, "mip_lender_name", "Acme Mortgage")

    class _Client:
        def execute(self, sql: str) -> list[dict[str, object]]:
            _ = sql
            return [
                {"current_lender_ref": "Acme Mortgage"},
                {"current_lender_ref": "Wells Fargo Bank"},
                {"current_lender_ref": "Competitor B"},
            ]

    monkeypatch.setattr(databricks_sql, "get_sql_client", lambda: _Client())

    values, status = _target_lender_options()

    assert status == "live"
    assert values == ["All", "Acme Mortgage", "Competitor B"]


def test_config_target_lender_options_include_configured_tenant_when_gold_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_api.settings, "mip_lender_name", "Acme Mortgage")

    class _Client:
        def execute(self, sql: str) -> list[dict[str, object]]:
            _ = sql
            return []

    monkeypatch.setattr(databricks_sql, "get_sql_client", lambda: _Client())

    values, status = _target_lender_options()

    assert status == "configured_empty"
    assert values == ["All", "Acme Mortgage"]
