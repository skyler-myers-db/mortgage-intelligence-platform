"""Unit tests for the Unity-Catalog-backed admin rules + sources endpoints.

Slice13-accuracy follow-up: the ``/api/admin/rules`` + ``/api/admin/sources``
endpoints now read from ``mip.ref.offer_rules_config`` and per-table
``DESCRIBE DETAIL`` / ``SELECT COUNT(*)``. The admin UI drops its
THRESHOLD_DEFAULTS, RULES_EDITED_AT, and DATA_SOURCES literals and
consumes these endpoints.

The conftest installs ``_FakeAdminSqlClient`` so the default tests
exercise the real ``AdminRulesService`` against a programmed SQL shim.
503 paths install a fault-injecting stub per-test.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.admin_rules import (
    AdminRulesService,
    get_admin_rules_service,
)
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.resilience import DependencyDownError

client = TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/admin/rules
# ---------------------------------------------------------------------------


def test_get_rules_returns_uc_backed_thresholds() -> None:
    """Default conftest stub returns the six canonical rows."""
    response = client.get("/api/admin/rules")
    assert response.status_code == 200, response.text
    body = response.json()

    assert "offer_rules_version" in body
    assert body["offer_rules_version"].startswith("itm_"), body
    # Deterministic hash suffix = 12 hex chars
    assert len(body["offer_rules_version"]) == len("itm_") + 12

    assert "rules_edited_at" in body
    assert body["rules_edited_at"] is not None

    thresholds = body["thresholds"]
    assert isinstance(thresholds, list)
    assert len(thresholds) == 6

    keys = {t["key"] for t in thresholds}
    assert keys == {
        "mip_min_spread_bps",
        "mip_min_equity_pct",
        "mip_heloc_equity_min_pct",
        "mip_cashout_equity_min_pct",
        "mip_retention_min_spread_bps",
        "mip_market_rate",
    }

    # Value parity with fn_in_the_money / fn_next_best_offer / fn_rate_spread headers
    by_key = {t["key"]: t for t in thresholds}
    assert by_key["mip_min_spread_bps"]["value"] == 75.0
    assert by_key["mip_min_equity_pct"]["value"] == 15.0
    assert by_key["mip_heloc_equity_min_pct"]["value"] == 35.0
    assert by_key["mip_cashout_equity_min_pct"]["value"] == 25.0
    assert by_key["mip_retention_min_spread_bps"]["value"] == 50.0
    assert by_key["mip_market_rate"]["value"] == 0.04875


def test_get_rules_version_is_deterministic_hash_of_values() -> None:
    """Same thresholds -> same version across calls."""
    r1 = client.get("/api/admin/rules").json()
    r2 = client.get("/api/admin/rules").json()
    assert r1["offer_rules_version"] == r2["offer_rules_version"]


def test_get_rules_returns_503_on_dependency_down() -> None:
    class _BoomClient:
        def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
            raise DependencyDownError("warehouse", reason="breaker open")

    previous = app.dependency_overrides.get(get_admin_rules_service)
    app.dependency_overrides[get_admin_rules_service] = lambda: AdminRulesService(_BoomClient())
    try:
        response = client.get("/api/admin/rules")
        assert response.status_code == 503
    finally:
        if previous is None:
            del app.dependency_overrides[get_admin_rules_service]
        else:
            app.dependency_overrides[get_admin_rules_service] = previous


def test_get_rules_returns_503_on_sql_error() -> None:
    class _BoomClient:
        def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
            raise DatabricksSqlError("SQL execution failed")

    previous = app.dependency_overrides.get(get_admin_rules_service)
    app.dependency_overrides[get_admin_rules_service] = lambda: AdminRulesService(_BoomClient())
    try:
        response = client.get("/api/admin/rules")
        assert response.status_code == 503
    finally:
        if previous is None:
            del app.dependency_overrides[get_admin_rules_service]
        else:
            app.dependency_overrides[get_admin_rules_service] = previous


# ---------------------------------------------------------------------------
# GET /api/admin/sources
# ---------------------------------------------------------------------------


def test_get_sources_returns_live_plus_roadmap_split() -> None:
    response = client.get("/api/admin/sources")
    assert response.status_code == 200, response.text
    rows = response.json()

    assert isinstance(rows, list)
    assert len(rows) == 8

    # Exactly two roadmap entries (MLS + Building Permits) per the
    # slice13-accuracy split.
    by_name = {r["name"]: r for r in rows}
    assert by_name["MLS"]["status"] == "roadmap"
    assert by_name["MLS"]["rows"] is None
    assert by_name["MLS"]["last_updated"] is None
    assert by_name["Building Permits"]["status"] == "roadmap"

    # Live sources have row counts + last_updated timestamps.
    for live_name in (
        "Cotality Public Records",
        "Voluntary Lien",
        "MMA Mortgage Analytics",
        "CLIP",
        "Owner Link",
        "AVM",
    ):
        r = by_name[live_name]
        assert r["status"] == "live", r
        assert r["rows"] is not None
        assert r["last_updated"] is not None
        assert isinstance(r["note"], str)


def test_get_sources_degrades_per_source_on_sql_failure() -> None:
    """Per-source degrade contract (2026-04-23): if a DESCRIBE DETAIL
    fails for one table (permission denial, schema drift, etc.), the
    remaining sources still render. The failing source comes back with
    ``status='error'`` and a diagnostic note, NOT 503 for the whole
    panel. This prevents a single grant gap from blacking out the
    admin page.
    """

    class _BoomClient:
        def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
            raise DatabricksSqlError("DESCRIBE DETAIL unavailable")

    previous = app.dependency_overrides.get(get_admin_rules_service)
    app.dependency_overrides[get_admin_rules_service] = lambda: AdminRulesService(_BoomClient())
    try:
        response = client.get("/api/admin/sources")
        assert response.status_code == 200, response.text
        rows = response.json()
        # All live-source candidates degrade to `error`; roadmap rows pass through.
        statuses = {r["status"] for r in rows}
        assert "error" in statuses
        assert "roadmap" in statuses
        # No live sources when the SQL client blows up on every call.
        assert "live" not in statuses
    finally:
        if previous is None:
            del app.dependency_overrides[get_admin_rules_service]
        else:
            app.dependency_overrides[get_admin_rules_service] = previous


def test_get_sources_permission_denied_marks_source_cleanly() -> None:
    """PERMISSION_DENIED on a single table → `status='permission_denied'`
    (a distinct value so the admin UI can show "grant needed" specifically)."""

    class _PermDeniedClient:
        def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
            raise DatabricksSqlError(
                "PERMISSION_DENIED: User does not have USE SCHEMA on Schema 'mip.silver'."
            )

    previous = app.dependency_overrides.get(get_admin_rules_service)
    app.dependency_overrides[get_admin_rules_service] = lambda: AdminRulesService(_PermDeniedClient())
    try:
        response = client.get("/api/admin/sources")
        assert response.status_code == 200, response.text
        rows = response.json()
        statuses = {r["status"] for r in rows}
        assert "permission_denied" in statuses
    finally:
        if previous is None:
            del app.dependency_overrides[get_admin_rules_service]
        else:
            app.dependency_overrides[get_admin_rules_service] = previous


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


def test_rules_service_caches_reads_within_ttl() -> None:
    """TTL cache collapses repeat reads into a single warehouse call."""

    class _CountingClient:
        def __init__(self) -> None:
            self.call_count = 0

        def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
            self.call_count += 1
            return [
                {
                    "key": "mip_min_spread_bps",
                    "value": 75.0,
                    "unit": "bps",
                    "label": "Min spread (bps)",
                    "description": "d",
                    "sort_order": 1,
                    "last_updated": "2026-04-22 00:00:00",
                }
            ]

    sql = _CountingClient()
    service = AdminRulesService(sql, cache_ttl_s=30.0)
    service.get_rules()
    service.get_rules()
    service.get_rules()
    assert sql.call_count == 1


# ---------------------------------------------------------------------------
# Legacy PUT shim (backwards compat)
# ---------------------------------------------------------------------------


def test_put_rules_legacy_shim_still_works() -> None:
    """The legacy in-memory PUT is retained -- it no longer changes the
    UC-backed GET payload, but it DOES record an override surfaced via
    ``legacy_override``.

    Round-3 hole-finder #17: the request body is now Pydantic-validated
    (``{"overrides": {...}}``) — arbitrary top-level keys get a 422.
    """
    put = client.put("/api/admin/rules", json={"overrides": {"note": "hello"}})
    assert put.status_code == 200
    body = client.get("/api/admin/rules").json()
    assert body["legacy_override"].get("note") == "hello"


def test_put_rules_rejects_arbitrary_top_level_keys() -> None:
    """Round-3 hole-finder #17: the old contract silently accepted
    ``{"x": "y"}``. Now it's 422."""
    put = client.put("/api/admin/rules", json={"x": "y"})
    assert put.status_code == 422
