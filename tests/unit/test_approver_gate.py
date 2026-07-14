"""Approver-gate contract for human decision endpoints (audit P2-5).

``require_approver`` guards ``/outreach/approve`` and ``/outreach/reject``:

* empty ``MIP_APPROVER_EMAILS`` in local/test -> authenticated users may
  exercise the development workflow;
* empty roster in a deployed environment -> admin-only, never fail-open;
* non-empty allowlist -> listed emails admitted, admins admitted
  (operator can never lock themselves out), everyone else 403 with the
  shared ``{"detail": "forbidden"}`` body.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.config.settings import settings
from backend.services.rbac import require_approver


def _request(email: str | None = None, groups: str | None = None) -> Request:
    headers = []
    if email is not None:
        headers.append((b"x-forwarded-email", email.encode()))
    if groups is not None:
        headers.append((b"x-forwarded-groups", groups.encode()))
    return Request({"type": "http", "headers": headers, "method": "POST", "path": "/"})


@pytest.fixture(autouse=True)
def _clean_allowlists(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "approver_emails", "", raising=False)
    monkeypatch.setattr(settings, "admin_emails", "", raising=False)
    monkeypatch.setattr(settings, "app_env", "local", raising=False)
    yield


def test_empty_allowlist_admits_authenticated_actor_only_in_local() -> None:
    actor = require_approver(_request(email="lo01@summit.example"))
    assert actor == "lo01@summit.example"


def test_empty_allowlist_fails_closed_to_admin_in_deployed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "dev")
    with pytest.raises(HTTPException) as exc:
        require_approver(_request(email="lo01@summit.example", groups=""))
    assert exc.value.status_code == 403
    assert exc.value.detail == "forbidden"

    actor = require_approver(
        _request(email="operator@summit.example", groups="mip-admin")
    )
    assert actor == "operator@summit.example"


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
