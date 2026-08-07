"""Unit tests for ``workspace_host`` on the authenticated health body.

The frontend Genie surface deep-links Unity Catalog asset names into the
workspace Catalog Explorer. ``useWorkspaceHost()`` reads ``workspace_host``
from ``GET /api/health`` and re-validates it with the same rules as the
backend's ``workspace_origin()`` guard; when the key is absent it degrades
to plain text. Contract pinned here:

1. The anonymous liveness body stays exactly ``{status, mode}`` even when a
   valid workspace host is configured (R6-09 reconnaissance contract).
2. The authenticated body carries the validated https workspace origin.
3. An unset or untrustworthy ``DATABRICKS_HOST`` omits the key entirely --
   never an empty string or null -- so the frontend treats the host as
   unknown.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import health as health_mod
from backend.main import app
from backend.services import health_probes, resilience
from backend.services.forced_degraded import _reset_forced_degraded_for_tests

client = TestClient(app)

VALID_HOST = "https://dbc-12345678-abcd.cloud.databricks.com"
AUTH_HEADERS = {"X-Forwarded-Email": "ops@example.com"}
ADMIN_HEADERS = {
    "X-Forwarded-Email": "ops@example.com",
    "X-Forwarded-Groups": "mip-admin",
}


@pytest.fixture(autouse=True)
def _stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    resilience._reset_breakers_for_tests()
    _reset_forced_degraded_for_tests()
    client.cookies.clear()
    health_probes._probe_cache.clear()
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)


def test_anonymous_body_keeps_r6_09_shape_with_host_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured workspace host must never leak onto the anonymous body."""
    monkeypatch.setattr(health_mod.settings, "databricks_host", VALID_HOST)

    res = client.get("/api/health")

    assert res.status_code == 200
    assert set(res.json().keys()) == {"status", "mode"}


def test_authenticated_body_carries_validated_workspace_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod.settings, "databricks_host", VALID_HOST)

    res = client.get("/api/health", headers=AUTH_HEADERS)

    assert res.status_code == 200
    assert res.json()["workspace_host"] == VALID_HOST


def test_authenticated_body_normalizes_bare_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health must reuse workspace_origin(), not echo the raw setting."""
    monkeypatch.setattr(
        health_mod.settings,
        "databricks_host",
        "dbc-12345678-abcd.cloud.databricks.com",
    )

    res = client.get("/api/health", headers=AUTH_HEADERS)

    assert res.json()["workspace_host"] == VALID_HOST


@pytest.mark.parametrize(
    "raw_host",
    [
        None,
        "",
        "   ",
        "https://workspace.example",  # .env.local placeholder template value
        "http://dbc-12345678-abcd.cloud.databricks.com",  # https only
        "https://user:pw@dbc-12345678-abcd.cloud.databricks.com",
        "https://dbc-12345678-abcd.cloud.databricks.com/some/path",
        "https://dbc-12345678-abcd.cloud.databricks.com?q=1",
        "https://dbc-12345678-abcd.cloud.databricks.com#frag",
        "https://evil.example.com",
    ],
)
def test_invalid_or_unset_host_omits_key(
    monkeypatch: pytest.MonkeyPatch, raw_host: str | None
) -> None:
    """The key is omitted -- never emitted as an empty string or null."""
    monkeypatch.setattr(health_mod.settings, "databricks_host", raw_host)

    res = client.get("/api/health", headers=AUTH_HEADERS)

    assert res.status_code == 200
    assert "workspace_host" not in res.json()


def test_admin_body_carries_workspace_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin diagnostics stay a superset of the authenticated shape."""
    monkeypatch.setattr(health_mod.settings, "databricks_host", VALID_HOST)

    res = client.get("/api/admin/health", headers=ADMIN_HEADERS)

    assert res.status_code == 200
    assert res.json()["workspace_host"] == VALID_HOST
