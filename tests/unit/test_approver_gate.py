"""Approver-gate contract for human decision endpoints (audit P2-5).

``require_approver`` guards ``/outreach/approve`` and ``/outreach/reject``:

* trusted ``mip-approver`` group members are admitted as compatibility;
* configured automation identities and listed emails are admitted;
* admins are admitted and empty configuration never fails open;
  (operator can never lock themselves out), everyone else 403 with the
  shared ``{"detail": "forbidden"}`` body.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.config.settings import settings
from backend.services.rbac import can_access_approver, require_approver


def _request(
    email: str | None = None,
    groups: str | None = None,
    user: str | None = None,
) -> Request:
    headers = []
    if email is not None:
        headers.append((b"x-forwarded-email", email.encode()))
    if groups is not None:
        headers.append((b"x-forwarded-groups", groups.encode()))
    if user is not None:
        headers.append((b"x-forwarded-user", user.encode()))
    return Request({"type": "http", "headers": headers, "method": "POST", "path": "/"})


@pytest.fixture(autouse=True)
def _clean_allowlists(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "approver_emails", "", raising=False)
    monkeypatch.setattr(settings, "approver_identities", "", raising=False)
    monkeypatch.setattr(settings, "approver_group_name", "mip-approver", raising=False)
    monkeypatch.setattr(settings, "admin_emails", "", raising=False)
    monkeypatch.setattr(settings, "admin_identities", "", raising=False)
    monkeypatch.setattr(settings, "admin_group_name", "mip-admin", raising=False)
    monkeypatch.setattr(settings, "trust_forwarded_headers", True, raising=False)
    monkeypatch.setattr(settings, "app_env", "local", raising=False)
    yield


def test_empty_allowlists_never_admit_arbitrary_authenticated_actor() -> None:
    request = _request(email="lo01@summit.example", groups="")
    assert can_access_approver(request) is False
    with pytest.raises(HTTPException) as exc:
        require_approver(request)
    assert exc.value.status_code == 403


def test_trusted_approver_group_is_admitted_as_compatibility() -> None:
    request = _request(email="lo01@summit.example", groups="mip-approver")
    assert can_access_approver(request) is True
    assert require_approver(request) == "lo01@summit.example"


def test_group_compatibility_is_ignored_at_untrusted_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)
    with pytest.raises(HTTPException):
        require_approver(_request(email="lo01@summit.example", groups="mip-approver"))


def test_configured_automation_identity_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "approver_identities", "normal-client,operator2-client")
    assert require_approver(_request(email="normal-client", groups="")) == "normal-client"
    assert (
        require_approver(_request(user="operator2-client", groups="")) == "operator2-client"
    )


def test_verifier_and_unlisted_identity_are_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "approver_identities", "normal-client,operator2-client")
    for actor in ("verifier-client", "unlisted-client"):
        with pytest.raises(HTTPException) as exc:
            require_approver(_request(email=actor, groups=""))
        assert exc.value.status_code == 403


def test_deployed_env_rejects_group_only_and_accepts_exact_admin_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "dev")
    with pytest.raises(HTTPException) as exc:
        require_approver(_request(email="lo01@summit.example", groups=""))
    assert exc.value.status_code == 403
    assert exc.value.detail == "forbidden"

    with pytest.raises(HTTPException):
        require_approver(_request(email="operator@summit.example", groups="mip-admin"))
    monkeypatch.setattr(settings, "admin_identities", "admin-client")
    assert require_approver(_request(user="admin-client", groups="mip-admin")) == "admin-client"


def test_listed_email_is_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "approver_emails", "Approver@Summit.example")
    actor = require_approver(_request(email="approver@summit.example"))
    assert actor == "approver@summit.example"


def test_unlisted_email_is_denied_with_shared_403_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "approver_emails", "approver@summit.example")
    with pytest.raises(HTTPException) as exc:
        require_approver(_request(email="intruder@summit.example", groups=""))
    assert exc.value.status_code == 403
    assert exc.value.detail == "forbidden"


def test_admin_group_still_passes_when_allowlist_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deploying operator must never lock themselves out (2026-06-11
    empty-admin-allowlist incident lesson)."""
    monkeypatch.setattr(settings, "approver_emails", "approver@summit.example")
    actor = require_approver(
        _request(email="operator@summit.example", groups="mip-admin")
    )
    assert actor == "operator@summit.example"


def test_admin_email_still_passes_when_allowlist_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "approver_emails", "approver@summit.example")
    monkeypatch.setattr(settings, "admin_emails", "operator@summit.example")
    actor = require_approver(_request(email="operator@summit.example", groups=""))
    assert actor == "operator@summit.example"


def test_admin_automation_identity_also_has_approver_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_identities", "admin-client")
    assert require_approver(_request(user="admin-client", groups="")) == "admin-client"
