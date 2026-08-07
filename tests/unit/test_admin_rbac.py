"""RBAC contract for the admin surface.

Slice-RBAC: ``backend/services/rbac.py`` gates every ``/api/admin/*``
endpoint via the ``AdminDep`` FastAPI dependency. Admission is a match
against the configured admin group name (default ``mip-admin``) plus
the hard-coded fallback ``"admins"``; everything else is a 403 with
``{"detail": "forbidden"}``. The conftest session-level wrap stamps
``X-Forwarded-Groups: mip-admin`` on the default ``TestClient``, so
these tests override per-call where they need to exercise deny paths.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.rbac import resolve_workflow_actor
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore


@pytest.fixture()
def client() -> TestClient:
    """A default ``TestClient`` -- conftest stamps the admin header
    onto every instance, so GETs against admin routes 200 by default.
    """
    return TestClient(app)


def test_admin_requires_admin_group(client: TestClient) -> None:
    """An absent ``X-Forwarded-Groups`` header -> 403 + exact body.

    We override the default header with an empty value on the call;
    httpx merges per-call headers over instance defaults, so ``""``
    tokenises to an empty set and the dependency denies.
    """
    response = client.get(
        "/api/admin/rules", headers={"X-Forwarded-Groups": ""}
    )
    assert response.status_code == 403
    # The 403 body string is load-bearing for the frontend banner copy.
    assert response.json() == {"detail": "forbidden"}


def test_admin_allows_matching_group(client: TestClient) -> None:
    """The configured group name admits the caller."""
    response = client.get(
        "/api/admin/rules",
        headers={"X-Forwarded-Groups": f"other-group,{settings.admin_group_name}"},
    )
    assert response.status_code == 200, response.text


def test_admin_rejects_non_admin_group(client: TestClient) -> None:
    """A group list that doesn't include mip-admin / admins -> 403."""
    response = client.get(
        "/api/admin/rules",
        headers={"X-Forwarded-Groups": "analysts,loan-officers"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}


@pytest.mark.parametrize(
    "path",
    ["/api/audit/events", "/api/audit/events/page", "/api/audit/rollups"],
)
def test_audit_reads_reject_non_admin_group(client: TestClient, path: str) -> None:
    """The relocated audit explorer is protected at its own API boundary."""

    response = client.get(
        path,
        headers={"X-Forwarded-Groups": "analysts,loan-officers"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}


def test_session_returns_only_admin_capability_from_same_group_rule(
    client: TestClient,
) -> None:
    admitted = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Groups": settings.admin_group_name},
    )
    denied = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Groups": "analysts,loan-officers"},
    )
    compat = client.get(
        "/api/session",
        headers={"X-Forwarded-Groups": settings.admin_group_name},
    )

    assert admitted.status_code == 200
    assert admitted.json() == {"can_access_admin": True, "can_approve": True}
    assert denied.status_code == 200
    assert denied.json() == {"can_access_admin": False, "can_approve": False}
    assert compat.status_code == 200
    assert compat.json() == admitted.json()


def test_session_and_admin_gate_share_email_allowlist_rule(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_emails", "operator@example.com")
    headers = {
        "X-Forwarded-Email": "operator@example.com",
        "X-Forwarded-Groups": "",
    }

    session = client.get("/api/v1/session", headers=headers)
    admin = client.get("/api/admin/rules", headers=headers)

    assert session.json() == {"can_access_admin": True, "can_approve": True}
    assert admin.status_code == 200, admin.text


def test_session_separates_approver_automation_from_admin_and_verifier(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "approver_identities", "normal-client,operator2-client")
    monkeypatch.setattr(settings, "admin_identities", "admin-client")
    monkeypatch.setattr(settings, "approver_emails", "")
    monkeypatch.setattr(settings, "admin_emails", "")

    normal = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Email": "normal-client", "X-Forwarded-Groups": ""},
    )
    operator2 = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-User": "operator2-client", "X-Forwarded-Groups": ""},
    )
    admin = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Email": "admin-client", "X-Forwarded-Groups": ""},
    )
    verifier = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Email": "verifier-client", "X-Forwarded-Groups": ""},
    )

    expected_operator = {"can_access_admin": False, "can_approve": True}
    assert normal.json() == expected_operator
    assert operator2.json() == expected_operator
    assert admin.json() == {"can_access_admin": True, "can_approve": True}
    assert verifier.json() == {"can_access_admin": False, "can_approve": False}


def test_sandbox_rejects_group_only_and_accepts_exact_admin_identity(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "sandbox")
    monkeypatch.setattr(settings, "admin_identities", "admin-client")
    monkeypatch.setattr(settings, "admin_emails", "")

    group_only = client.get(
        "/api/admin/rules",
        headers={
            "X-Forwarded-Email": "operator@example.com",
            "X-Forwarded-Groups": settings.admin_group_name,
        },
    )
    exact_identity = client.get(
        "/api/admin/rules",
        headers={
            "X-Forwarded-User": "admin-client",
            "X-Forwarded-Groups": "",
        },
    )

    assert group_only.status_code == 403
    assert exact_identity.status_code == 200, exact_identity.text


def test_force_degraded_rejects_non_admin_group(client: TestClient) -> None:
    """The drill switch is admin-only because it affects every live user."""

    response = client.post(
        "/api/admin/force-degraded",
        headers={"X-Forwarded-Groups": "analysts,loan-officers"},
        json={"state": "on", "dependency": "warehouse", "ttl_s": 30},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}


def test_admin_custom_group_name_from_settings(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``settings.admin_group_name`` drives admission -- a lender that
    ships ``MIP_ADMIN_GROUP_NAME=risk-admin`` gets that group admitted
    without code changes.
    """
    monkeypatch.setattr(settings, "admin_group_name", "risk-admin")
    response = client.get(
        "/api/admin/rules", headers={"X-Forwarded-Groups": "risk-admin"}
    )
    assert response.status_code == 200, response.text
    # The default mip-admin is NOT implicitly allowed when the env var
    # is overridden. (The hard-coded "admins" fallback still is -- that
    # is a separate, intentional choke point.)
    denied = client.get(
        "/api/admin/rules", headers={"X-Forwarded-Groups": "mip-admin"}
    )
    assert denied.status_code == 403


def test_admin_respects_trust_forwarded_headers_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-09: when ``settings.trust_forwarded_headers`` is False, the
    admin gate IGNORES ``X-Forwarded-Groups`` even if it names the
    admin group -- the forwarded header is untrustable in that posture,
    so fail-closed (403) is the correct answer.

    Flipping the flag back to True with the same header restores the
    existing admit path -- pinning the default-True posture that
    matches the Databricks Apps edge.
    """
    # Trust disabled -- even a well-formed mip-admin header denies.
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)
    denied = client.get(
        "/api/admin/rules", headers={"X-Forwarded-Groups": "mip-admin"}
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "forbidden"}
    session_denied = client.get(
        "/api/v1/session", headers={"X-Forwarded-Groups": "mip-admin"}
    )
    assert session_denied.json() == {"can_access_admin": False, "can_approve": False}

    # Trust re-enabled -- same header admits again.
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    admitted = client.get(
        "/api/admin/rules", headers={"X-Forwarded-Groups": "mip-admin"}
    )
    assert admitted.status_code == 200, admitted.text
    session_admitted = client.get(
        "/api/v1/session", headers={"X-Forwarded-Groups": "mip-admin"}
    )
    assert session_admitted.json() == {"can_access_admin": True, "can_approve": True}


def test_admin_fallback_group_always_admitted(client: TestClient) -> None:
    """The ``admins`` literal is a belt-and-suspenders admit path so an
    operator can't accidentally strip all admin access by zeroing the
    env var. It's documented; this test pins it.
    """
    response = client.get(
        "/api/admin/rules", headers={"X-Forwarded-Groups": "admins"}
    )
    assert response.status_code == 200, response.text


def test_put_rules_is_rejected_without_mutating_or_auditing() -> None:
    audit = InMemoryAuditStore()
    previous = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        c = TestClient(app)
        response = c.put(
            "/api/admin/rules",
            json={"attempted_change": {"note": "hello"}},
            headers={
                "X-Forwarded-Email": "governance-reviewer@example.com",
                "X-Forwarded-Groups": "mip-admin",
            },
        )
        assert response.status_code == 410, response.text
        assert audit.list(limit=10) == []
    finally:
        if previous is None:
            del app.dependency_overrides[get_audit_store]
        else:
            app.dependency_overrides[get_audit_store] = previous


def _bare_request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal Starlette request for direct gate-function tests."""
    raw = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": raw,
            "query_string": b"",
        }
    )


def test_resolve_workflow_actor_trusted_edge_matches_shared_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behind a trusted edge the workflow resolver IS the shared fail-closed
    gate: forwarded identity in, actor out; no identity, 401 with the shared
    detail string.
    """
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)

    assert (
        resolve_workflow_actor(_bare_request({"X-Forwarded-Email": "lo@example.com"}))
        == "lo@example.com"
    )
    assert resolve_workflow_actor(_bare_request({"X-Forwarded-User": "lo.user"})) == "lo.user"

    with pytest.raises(HTTPException) as excinfo:
        resolve_workflow_actor(_bare_request())
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "authenticated identity required"


def test_resolve_workflow_actor_untrusted_edge_serves_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GRANTS.md §11a: with trust off, workflow surfaces keep serving with
    the distinct untrusted-edge marker — forwarded headers are spoofable on
    a non-Apps edge, so they are ignored rather than admitted or 401'd.
    """
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)

    actor = resolve_workflow_actor(_bare_request({"X-Forwarded-Email": "spoofed@example.com"}))
    assert actor == "unknown-actor@untrusted-edge"
