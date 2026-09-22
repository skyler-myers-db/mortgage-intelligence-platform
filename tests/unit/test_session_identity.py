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
    # Non-vacuity: the request really was logged, just without the identity.
    assert any(record.name == "mip.http" for record in caplog.records)
    logged = "\n".join(
        f"{record.getMessage()} {record.__dict__}" for record in caplog.records
    )
    assert _ACTOR not in logged


def test_compat_and_versioned_session_paths_agree(client: TestClient) -> None:
    headers = {"X-Forwarded-Email": _ACTOR, "X-Forwarded-Groups": ""}

    assert (
        client.get("/api/session", headers=headers).json()
        == client.get("/api/v1/session", headers=headers).json()
    )
