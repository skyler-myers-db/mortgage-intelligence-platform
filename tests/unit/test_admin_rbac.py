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
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.audit_store import get_audit_store
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
    assert admitted.json() == {"can_access_admin": True}
    assert denied.status_code == 200
    assert denied.json() == {"can_access_admin": False}
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

    assert session.json() == {"can_access_admin": True}
    assert admin.status_code == 200, admin.text


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
    assert session_denied.json() == {"can_access_admin": False}

    # Trust re-enabled -- same header admits again.
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    admitted = client.get(
        "/api/admin/rules", headers={"X-Forwarded-Groups": "mip-admin"}
    )
    assert admitted.status_code == 200, admitted.text
    session_admitted = client.get(
        "/api/v1/session", headers={"X-Forwarded-Groups": "mip-admin"}
    )
    assert session_admitted.json() == {"can_access_admin": True}


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
