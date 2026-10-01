"""Unit tests for the Unity-Catalog-backed admin rules + sources endpoints.

Slice13-accuracy follow-up: the ``/api/admin/rules`` + ``/api/admin/sources``
endpoints read ``mip.ref.offer_rules_config`` and the gold
``mip.gold.source_readiness`` summary. App SQL is gold-only (2026-09-30): the
sources read never probes silver, first-party or provider tables; a source the
summary has no row for reads ``unavailable``. The admin UI drops its
THRESHOLD_DEFAULTS, RULES_EDITED_AT, and DATA_SOURCES literals and consumes
these endpoints.

The conftest installs ``_FakeAdminSqlClient`` so the default tests
exercise the real ``AdminRulesService`` against a programmed SQL shim.
503 paths install a fault-injecting stub per-test.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.admin_rules import (
    _SOURCES,
    AdminRulesService,
    get_admin_rules_service,
)
from backend.services.databricks_sql import (
    DatabricksSqlError,
    DatabricksSqlObjectMissingError,
    DatabricksSqlPermissionError,
)
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
    assert by_key["mip_market_rate"]["value"] == 0.0637
    assert by_key["mip_market_rate"]["description"].startswith("Operating market rate")


def test_get_rules_version_is_deterministic_hash_of_values() -> None:
    """Same thresholds -> same version across calls."""
    r1 = client.get("/api/admin/rules").json()
    r2 = client.get("/api/admin/rules").json()
    assert r1["offer_rules_version"] == r2["offer_rules_version"]


def test_get_settings_exposes_runtime_configuration_for_admins() -> None:
    response = client.get("/api/admin/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["app_env"] == settings.app_env
    assert body["lender_name"] == settings.mip_lender_name
    assert body["catalog"] == settings.mip_default_catalog


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
    assert len(rows) == 16

    by_name = {r["name"]: r for r in rows}
    assert by_name["Building Permits"]["status"] == "roadmap"
    assert by_name["Building Permits"]["rows"] is None
    assert by_name["Building Permits"]["last_updated"] is None

    # Live sources have row counts + last_updated timestamps.
    for live_name in (
        "Cotality Public Records",
        "Voluntary Lien",
        "MMA Mortgage Analytics",
        "CLIP",
        "Owner Link",
        "AVM",
        "FRED Market Rates",
        "MLS Listings",
        "Cotality HELOC Propensity",
        "Cotality Refi Propensity",
    ):
        r = by_name[live_name]
        assert r["status"] == "live", r
        assert r["rows"] is not None
        assert r["last_updated"] is not None
        assert isinstance(r["note"], str)
    for optional_name in (
        "First-party LOS / Applications",
        "First-party Servicing Portfolio",
        "First-party CRM / Campaigns",
        "First-party Customer Interactions",
        "First-party Product Balances",
    ):
        assert by_name[optional_name]["status"] in {"live", "demo_synthetic", "configured_empty", "not_configured"}


def test_get_sources_prefers_gold_source_readiness_summary() -> None:
    """The production path reads mip.gold.source_readiness, not silver.

    This preserves the governance boundary: ETL can inspect silver and
    publish a non-PII gold summary, while the running app principal only
    needs SELECT on gold/ref.
    """

    class _GoldSummaryClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def execute(
            self, statement: str, parameters: Any = None
        ) -> list[dict[str, Any]]:
            self.calls.append(statement)
            if "GOLD.SOURCE_READINESS" in statement.upper():
                names = [
                    "Cotality Public Records",
                    "Voluntary Lien",
                    "MMA Mortgage Analytics",
                    "CLIP",
                    "Owner Link",
                    "AVM",
                    "FRED Market Rates",
                    "First-party LOS / Applications",
                    "First-party Servicing Portfolio",
                    "First-party CRM / Campaigns",
                    "First-party Customer Interactions",
                    "First-party Product Balances",
                    "MLS Listings",
                    "Cotality HELOC Propensity",
                    "Cotality Refi Propensity",
                    "Building Permits",
                    "UC Gold Borrower 360",
                    "UC Gold Lead Scores",
                    "UC Gold Lead Population",
                    "UC Gold Segment Population",
                    "UC Gold Borrower Dossier",
                ]
                return [
                    {
                        "source_name": name,
                        "status": "roadmap"
                        if name == "Building Permits"
                        else "demo_synthetic"
                        if name == "First-party LOS / Applications"
                        else "live",
                        "row_count": None
                        if name == "Building Permits"
                        else 1000 + idx,
                        "last_updated": None
                        if name == "Building Permits"
                        else "2026-05-04 19:45:13",
                        "checked_at": "2026-05-04 20:00:00",
                        "note": "Contracted · pending Cotality share"
                        if name == "Building Permits"
                        else (
                            "Summit Mortgage synthetic LOS/application feed · connected"
                            if name == "First-party LOS / Applications"
                            else "Delta Share · nightly"
                        ),
                        "synthetic_demo": name == "First-party LOS / Applications",
                        "sort_order": idx,
                    }
                    for idx, name in enumerate(names, start=1)
                ]
            raise AssertionError(f"unexpected fallback source probe: {statement}")

    fake = _GoldSummaryClient()
    service = AdminRulesService(fake)
    rows = service.get_sources()

    assert len(rows) == 21
    assert rows[0].name == "Cotality Public Records"
    assert rows[0].status == "live"
    assert rows[0].checked_at == "2026-05-04 20:00:00"
    assert rows[12].name == "MLS Listings"
    assert rows[12].status == "live"
    assert rows[15].name == "Building Permits"
    assert rows[15].status == "roadmap"
    assert rows[-1].name == "UC Gold Borrower Dossier"
    assert rows[-1].status == "live"
    assert rows[7].name == "First-party LOS / Applications"
    assert rows[7].synthetic_demo is True
    assert not any("SILVER." in call.upper() for call in fake.calls)


_ADMIN_SOURCE_NAMES = [desc.name for desc in _SOURCES]
_ROADMAP_NAMES = {desc.name for desc in _SOURCES if desc.roadmap}
_APP_SCHEMAS = (".GOLD.", ".REF.")
_ETL_ONLY_MARKERS = (".SILVER.", ".FIRST_PARTY.", ".RAW.", "COTALITY_MORTGAGE_DATA.")


class _RecordingSummaryClient:
    """Answers the gold readiness summary for ``names``; records every statement."""

    def __init__(self, names: list[str] | None, *, error: BaseException | None = None) -> None:
        self.names = names
        self.error = error
        self.calls: list[str] = []

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.calls.append(statement)
        if self.error is not None:
            raise self.error
        if ".GOLD.SOURCE_READINESS" not in statement.upper() or self.names is None:
            return []
        return [
            {
                "source_name": name,
                "status": "roadmap" if name in _ROADMAP_NAMES else "live",
                "row_count": None if name in _ROADMAP_NAMES else 10 + idx,
                "last_updated": None if name in _ROADMAP_NAMES else "2026-09-29 06:00:00",
                "note": "summary note",
                "checked_at": "2026-09-29 06:05:00",
                "synthetic_demo": False,
                "sort_order": idx,
            }
            for idx, name in enumerate(self.names, start=1)
        ]

    def assert_gold_only(self) -> None:
        assert self.calls, "the readiness summary was never read"
        for call in self.calls:
            upper = call.upper()
            assert any(schema in upper for schema in _APP_SCHEMAS), call
            assert not any(marker in upper for marker in _ETL_ONLY_MARKERS), call
            assert "DESCRIBE" not in upper and "COUNT(*)" not in upper, call


def test_get_sources_absent_readiness_marks_every_non_roadmap_source_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = _RecordingSummaryClient(names=[])

    with caplog.at_level(logging.WARNING):
        rows = AdminRulesService(fake).get_sources()

    assert [row.name for row in rows] == _ADMIN_SOURCE_NAMES
    for row in rows:
        expected = "roadmap" if row.name in _ROADMAP_NAMES else "unavailable"
        assert row.status == expected, row
        assert (row.rows, row.last_updated, row.checked_at) == (None, None, None)
    unavailable = [row for row in rows if row.status == "unavailable"]
    assert all(row.note.endswith("· readiness summary has no row for this source") for row in unavailable)
    events = [r for r in caplog.records if getattr(r, "mip_event", None) == "admin_source_readiness_unavailable"]
    assert events and events[0].mip_outcome == "absent"  # type: ignore[attr-defined]
    fake.assert_gold_only()


def test_get_sources_missing_readiness_table_reads_as_absent() -> None:
    missing = DatabricksSqlObjectMissingError(
        "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`source_readiness` cannot be found."
    )
    wrapped = DependencyDownError("warehouse", reason="object missing")
    wrapped.last_error = missing
    fake = _RecordingSummaryClient(names=None, error=wrapped)

    rows = AdminRulesService(fake).get_sources()

    assert {row.status for row in rows} == {"unavailable", "roadmap"}
    fake.assert_gold_only()


def test_get_sources_partial_readiness_appends_unavailable_rows() -> None:
    present = [name for name in _ADMIN_SOURCE_NAMES if name not in {"AVM", "MLS Listings"}]
    fake = _RecordingSummaryClient(names=[*present, "UC Gold Borrower 360"])

    rows = AdminRulesService(fake).get_sources()

    names = [row.name for row in rows]
    assert names[: len(present) + 1] == [*present, "UC Gold Borrower 360"]
    assert names[len(present) + 1 :] == ["AVM", "MLS Listings"]
    by_name = {row.name: row for row in rows}
    assert by_name["Voluntary Lien"].status == "live"
    assert by_name["Voluntary Lien"].rows == 12
    for name in ("AVM", "MLS Listings"):
        row = by_name[name]
        assert row.status == "unavailable"
        assert (row.rows, row.last_updated, row.checked_at) == (None, None, None)
        assert row.note.endswith("· readiness summary has no row for this source")
    assert by_name["Building Permits"].status == "roadmap"
    fake.assert_gold_only()


def test_get_sources_complete_readiness_passes_through_unchanged() -> None:
    fake = _RecordingSummaryClient(names=_ADMIN_SOURCE_NAMES)

    rows = AdminRulesService(fake).get_sources()

    assert [row.name for row in rows] == _ADMIN_SOURCE_NAMES
    assert "unavailable" not in {row.status for row in rows}
    assert {row.status for row in rows if row.name in _ROADMAP_NAMES} == {"roadmap"}
    fake.assert_gold_only()


@pytest.mark.parametrize(
    "error",
    [
        DatabricksSqlError("SQL execution failed"),
        DatabricksSqlPermissionError("[INSUFFICIENT_PERMISSIONS] no SELECT on mip.gold"),
        DatabricksSqlObjectMissingError("[TABLE_OR_VIEW_NOT_FOUND] `mip`.`gold`.`other_table`"),
    ],
    ids=["sql-error", "permission", "other-missing-object"],
)
def test_get_sources_other_readiness_failures_answer_503(error: BaseException) -> None:
    fake = _RecordingSummaryClient(names=None, error=error)
    previous = app.dependency_overrides.get(get_admin_rules_service)
    app.dependency_overrides[get_admin_rules_service] = lambda: AdminRulesService(fake)
    try:
        response = client.get("/api/admin/sources")
    finally:
        if previous is None:
            del app.dependency_overrides[get_admin_rules_service]
        else:
            app.dependency_overrides[get_admin_rules_service] = previous

    assert response.status_code == 503, response.text
    # One statement, and never a probe of silver or first-party tables.
    assert len(fake.calls) == 1
    fake.assert_gold_only()


def test_sources_route_answers_200_with_unavailable_rows() -> None:
    fake = _RecordingSummaryClient(names=[])
    previous = app.dependency_overrides.get(get_admin_rules_service)
    app.dependency_overrides[get_admin_rules_service] = lambda: AdminRulesService(fake)
    try:
        sources = client.get("/api/admin/sources")
    finally:
        if previous is None:
            del app.dependency_overrides[get_admin_rules_service]
        else:
            app.dependency_overrides[get_admin_rules_service] = previous

    assert sources.status_code == 200, sources.text
    by_name = {row["name"]: row for row in sources.json()}
    assert by_name["MLS Listings"]["status"] == "unavailable"
    assert by_name["Building Permits"]["status"] == "roadmap"


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


def test_rules_service_caches_reads_within_ttl() -> None:
    """TTL cache collapses repeat reads into one rules load cycle."""

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
    assert sql.call_count == 2


# ---------------------------------------------------------------------------
# Rules write posture
# ---------------------------------------------------------------------------


def test_put_rules_is_gone_because_scoring_rules_are_uc_governed() -> None:
    put = client.put("/api/admin/rules", json={"attempted_change": {"note": "hello"}})
    assert put.status_code == 410
    assert "mip.ref.offer_rules_config" in put.json()["detail"]
    body = client.get("/api/admin/rules").json()
    assert "legacy_override" not in body


def test_put_rules_rejects_every_app_local_edit_attempt() -> None:
    put = client.put("/api/admin/rules", json={"x": "y"})
    assert put.status_code == 410
    assert "Unity Catalog" in put.json()["detail"]
