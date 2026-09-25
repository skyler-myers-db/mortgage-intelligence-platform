"""``/api/session`` tells the caller who they are signed in as (audit flow-02 / shell-06).

The UI prints "Approving as <email>" beside the approval gate, so the value
must be exactly the identity the approve endpoint will record, must never be
a fabricated one, and must never reach the logs.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app

_ACTOR = "approver.one@summit.example"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_session_returns_the_forwarded_email(client: TestClient) -> None:
    response = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Email": _ACTOR, "X-Forwarded-Groups": ""},
    )

    assert response.status_code == 200
    assert response.json()["actor_email"] == _ACTOR


def test_session_identity_matches_the_actor_the_approver_gate_admits(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The banner names the same actor ``require_approver`` returns.

    ``X-Forwarded-Email`` wins over ``X-Forwarded-User`` in both places, so the
    "Approving as" line can never name someone other than the audited actor.
    """
    monkeypatch.setattr(settings, "approver_emails", _ACTOR)
    monkeypatch.setattr(settings, "approver_identities", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "admin_identities", "")
    headers = {
        "X-Forwarded-Email": _ACTOR,
        "X-Forwarded-User": "someone-else",
        "X-Forwarded-Groups": "",
    }

    body = client.get("/api/v1/session", headers=headers).json()

    assert body == {
        "can_access_admin": False,
        "can_approve": True,
        "actor_email": _ACTOR,
        "actor_display_name": "Approver One",
        "role_labels": ["Approver"],
        "lender_name": settings.mip_lender_name,
        "rum_enabled": settings.mip_rum_enabled,
    }


def test_session_falls_back_to_forwarded_user(client: TestClient) -> None:
    response = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-User": "service-client", "X-Forwarded-Groups": ""},
    )

    assert response.json()["actor_email"] == "service-client"


def test_session_never_presents_the_default_actor_as_the_signed_in_person(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No forwarded identity -> null, not ``settings.default_actor``."""
    monkeypatch.setattr(settings, "default_actor", "demo.default@summit.example")

    body = client.get("/api/v1/session", headers={"X-Forwarded-Groups": ""}).json()

    assert body["actor_email"] is None


def test_session_ignores_forwarded_identity_when_the_edge_is_untrusted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5-09 posture: a spoofable header is never echoed back as an identity."""
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)

    body = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Email": _ACTOR, "X-Forwarded-Groups": "mip-admin"},
    ).json()

    assert body == {
        "can_access_admin": False,
        "can_approve": False,
        "actor_email": None,
        "actor_display_name": None,
        "role_labels": [],
        "lender_name": settings.mip_lender_name,
        "rum_enabled": settings.mip_rum_enabled,
    }


def test_session_never_logs_the_actor_identity(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/api/v1/session",
            headers={"X-Forwarded-Email": _ACTOR, "X-Forwarded-Groups": ""},
        )

    assert response.json()["actor_email"] == _ACTOR
    assert response.json()["actor_display_name"] == "Approver One"
    # Non-vacuity: the request really was logged, just without the identity.
    assert any(record.name == "mip.http" for record in caplog.records)
    logged = "\n".join(
        f"{record.getMessage()} {record.__dict__}" for record in caplog.records
    )
    assert _ACTOR not in logged
    assert "Approver One" not in logged


def test_compat_and_versioned_session_paths_agree(client: TestClient) -> None:
    headers = {"X-Forwarded-Email": _ACTOR, "X-Forwarded-Groups": ""}

    assert (
        client.get("/api/session", headers=headers).json()
        == client.get("/api/v1/session", headers=headers).json()
    )


# --- shell-06: display name and role labels for the topbar identity menu ---


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ("jane.doe@summit.example", "Jane Doe"),
        ("jane_doe@summit.example", "Jane Doe"),
        ("mary-ann.lee@summit.example", "Mary Ann Lee"),
        # Not a letters-only word list: shown verbatim, never guessed at.
        ("jdoe@summit.example", "jdoe"),
        ("jdoe2@summit.example", "jdoe2"),
        ("j.doe.@summit.example", "j.doe."),
        # A non-email forwarded identity (service principal) is its own label.
        ("service-client", "service-client"),
    ],
)
def test_display_name_is_derived_from_the_forwarded_identity_only(
    client: TestClient,
    identity: str,
    expected: str,
) -> None:
    header = "X-Forwarded-Email" if "@" in identity else "X-Forwarded-User"
    body = client.get(
        "/api/v1/session", headers={header: identity, "X-Forwarded-Groups": ""}
    ).json()

    assert body["actor_email"] == identity
    assert body["actor_display_name"] == expected


def test_display_name_follows_the_audited_identity_not_the_user_header(
    client: TestClient,
) -> None:
    """Email wins over User for the label exactly as for the audit actor."""
    body = client.get(
        "/api/v1/session",
        headers={
            "X-Forwarded-Email": _ACTOR,
            "X-Forwarded-User": "someone.else",
            "X-Forwarded-Groups": "",
        },
    ).json()

    assert body["actor_display_name"] == "Approver One"


def test_role_labels_name_the_capability_tiers_the_server_decided(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_emails", "admin.person@summit.example")
    monkeypatch.setattr(settings, "admin_identities", "")
    monkeypatch.setattr(settings, "approver_emails", _ACTOR)
    monkeypatch.setattr(settings, "approver_identities", "")

    def labels(email: str) -> list[str]:
        body = client.get(
            "/api/v1/session",
            headers={"X-Forwarded-Email": email, "X-Forwarded-Groups": ""},
        ).json()
        # Labels never contradict the authorization booleans.
        assert ("Administrator" in body["role_labels"]) is body["can_access_admin"]
        assert ("Approver" in body["role_labels"]) is body["can_approve"]
        return list(body["role_labels"])

    assert labels("admin.person@summit.example") == ["Administrator", "Approver"]
    assert labels(_ACTOR) == ["Approver"]
    assert labels("analyst@summit.example") == ["Workspace user"]


def test_no_forwarded_identity_means_no_name_and_no_roles(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "default_actor", "demo.default@summit.example")

    body = client.get("/api/v1/session", headers={"X-Forwarded-Groups": ""}).json()

    assert body["actor_display_name"] is None
    assert body["role_labels"] == []


def test_session_handler_is_async_and_needs_no_worker_thread() -> None:
    """delivery-09: the handler only reads forwarded headers and settings, so
    it runs on the event loop and a cold-cache burst holding every worker
    thread cannot stall the shell's identity call behind it."""
    import inspect

    from backend.api import session

    assert inspect.iscoroutinefunction(session.get_session)


# --- delivery-07: the tenant label and RUM gate ride the zero-dependency call ---


def test_session_carries_the_configured_lender_and_rum_gate(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same settings values ``/api/config/options`` returns, so the shell
    can paint the tenant label without waiting on the warehouse-backed call."""
    monkeypatch.setattr(settings, "mip_lender_name", "Harbor Point Lending")
    monkeypatch.setattr(settings, "mip_rum_enabled", True)

    body = client.get("/api/v1/session", headers={"X-Forwarded-Groups": ""}).json()

    assert body["lender_name"] == "Harbor Point Lending"
    assert body["rum_enabled"] is True

    monkeypatch.setattr(settings, "mip_rum_enabled", False)
    assert client.get("/api/v1/session", headers={"X-Forwarded-Groups": ""}).json()["rum_enabled"] is False


def test_session_identity_fields_touch_no_warehouse_or_lakebase(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new fields are settings reads: a SQL or Lakebase client that raises
    is never even asked for, so a warehouse or Lakebase 503 can never block
    shell identity."""
    from backend.services import databricks_sql, lakebase

    calls: list[str] = []

    def _refuse(name: str):
        def _raise(*_args: object, **_kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"/api/session asked for the {name} client")

        return _raise

    monkeypatch.setattr(databricks_sql, "get_sql_client", _refuse("sql"))
    monkeypatch.setattr(lakebase, "get_lakebase_client", _refuse("lakebase"))

    response = client.get(
        "/api/v1/session",
        headers={"X-Forwarded-Email": _ACTOR, "X-Forwarded-Groups": ""},
    )

    assert response.status_code == 200
    assert response.json()["lender_name"] == settings.mip_lender_name
    assert calls == []
