"""R6-10 trust boundary guard.

``trust_forwarded_headers=True`` is the default because the typical
deploy shape is Databricks Apps, which is the identity edge that strips
inbound ``X-Forwarded-*`` headers. On non-Apps runtimes (Azure App
Service, GKE, plain uvicorn behind nginx) that default is a trivial
audit-attribution / RBAC bypass. The startup check in
``backend/config/settings.py::check_trust_boundary_at_startup`` emits a
structured WARNING log line when the combination is dangerous; operators
then read the line and decide whether to flip the flag.

These tests pin the two polarities: warning fires on a non-Apps boot
with trust on, and does NOT fire on an Apps boot. They also guard the
"trust is off" path where the setting already matches the unusual
deploy shape.
"""
from __future__ import annotations

import logging

import pytest

from backend.config import settings as settings_mod


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop both Apps-marker env vars so tests start from a known state."""
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_URL", raising=False)


def test_warns_when_trust_true_and_not_apps(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-Apps boot with default trust=True must log the structured warning.

    This is the high-value case: customer deploys to GKE without
    thinking about the trust boundary, their NGINX forwards client
    headers verbatim, and any caller can claim any identity. The
    warning is the only chance operators get to notice before a real
    audit-attribution incident.
    """
    monkeypatch.setattr(
        settings_mod.get_settings(), "trust_forwarded_headers", True
    )
    caplog.set_level(logging.WARNING, logger="mip-runtime")
    settings_mod.check_trust_boundary_at_startup()

    matching = [
        rec for rec in caplog.records
        if "rbac_trust_boundary_unclear" in rec.getMessage()
    ]
    assert matching, "expected rbac_trust_boundary_unclear warning"
    # The structured `extra` must include the flag state so log-search
    # queries can filter cleanly -- the free-text body is a hint, the
    # fields are the API.
    rec = matching[0]
    assert getattr(rec, "event", None) == "rbac_trust_boundary_unclear"
    assert getattr(rec, "trust_forwarded_headers", None) is True
    assert getattr(rec, "databricks_app_marker", None) is False


def test_no_warning_when_apps_marker_present(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """On a Databricks Apps deploy (DATABRICKS_APP_PORT set) the default
    is safe -- the platform strips inbound headers. No warning should
    fire even with trust=True.
    """
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8080")
    monkeypatch.setattr(
        settings_mod.get_settings(), "trust_forwarded_headers", True
    )
    caplog.set_level(logging.WARNING, logger="mip-runtime")
    settings_mod.check_trust_boundary_at_startup()

    matching = [
        rec for rec in caplog.records
        if "rbac_trust_boundary_unclear" in rec.getMessage()
    ]
    assert not matching, (
        "no warning expected on Databricks Apps boot; got: "
        + ", ".join(rec.getMessage() for rec in matching)
    )


def test_no_warning_when_trust_disabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator has already made the call -- trust is off, so even on a
    non-Apps boot the posture is already fail-closed. The startup
    check stays silent so we don't page on a correctly-configured deploy.
    """
    monkeypatch.setattr(
        settings_mod.get_settings(), "trust_forwarded_headers", False
    )
    caplog.set_level(logging.WARNING, logger="mip-runtime")
    settings_mod.check_trust_boundary_at_startup()

    matching = [
        rec for rec in caplog.records
        if "rbac_trust_boundary_unclear" in rec.getMessage()
    ]
    assert not matching


def test_looks_like_databricks_app_deploy_reads_both_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Either ``DATABRICKS_APP_PORT`` or ``DATABRICKS_APP_URL`` counts.

    We accept both so a future platform rename doesn't silently disable
    the guard. The check is intentionally forgiving.
    """
    assert settings_mod.looks_like_databricks_app_deploy() is False

    monkeypatch.setenv("DATABRICKS_APP_PORT", "8080")
    assert settings_mod.looks_like_databricks_app_deploy() is True
    monkeypatch.delenv("DATABRICKS_APP_PORT")

    monkeypatch.setenv("DATABRICKS_APP_URL", "https://apps.databricks.com/mip")
    assert settings_mod.looks_like_databricks_app_deploy() is True
