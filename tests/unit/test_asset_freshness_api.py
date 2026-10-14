"""Asset freshness for every authenticated user (audit 2026-09-21 critic-03).

Decision D-audit-reads-c1: ``GET /api/assets/{asset_key}/freshness`` answers
the evidence drawer's freshness chip for every role from ONE reviewed
``gold.source_readiness`` row (the descriptor's ``freshness_basis``). It
issues no object probe, Delta detail, COUNT, information_schema or lineage
read, writes no audit row, never caches a failure, and serializes only the
freshness facts (no note, source table, row count or fully qualified name).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import audit_store as audit_store_module
from backend.services.asset_metadata import AssetMetadataService, get_asset_metadata_service
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.resilience import TTLCache
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

LOAN_OFFICER = {
    "X-Forwarded-Email": "lo.alpha@summit.example",
    "X-Forwarded-User": "lo.alpha@summit.example",
    "X-Forwarded-Groups": "",
}
FRESH_AT = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")


class _ReadinessSql:
    """Records every statement; answers the readiness SELECT with ``rows``."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.rows = rows if rows is not None else [
            {"status": "live", "last_updated": FRESH_AT, "checked_at": FRESH_AT}
        ]
        self.fail = fail

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.calls.append((statement, parameters))
        if self.fail:
            raise DatabricksSqlError("warehouse said: PERMISSION_DENIED statement_id=abc")
        return list(self.rows)


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    monkeypatch.setattr(audit_store_module, "_AUDIT_STORE", store)
    yield store


def _install(sql: _ReadinessSql, cache: TTLCache | None = None) -> AssetMetadataService:
    service = AssetMetadataService(sql, cache=cache if cache is not None else TTLCache())
    app.dependency_overrides[get_asset_metadata_service] = lambda: service
    return service


def _get(asset_key: str, headers: dict[str, str] | None = None) -> Any:
    return TestClient(app).get(f"/api/v1/assets/{asset_key}/freshness", headers=headers or LOAN_OFFICER)


def test_a_non_admin_reads_the_band_from_one_readiness_row(audit: InMemoryAuditStore) -> None:
    sql = _ReadinessSql()
    _install(sql)

    response = _get("portfolio_headline_metric_view")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "asset_key": "portfolio_headline_metric_view",
        "title": "Portfolio Headline Metric View",
        "freshness": "fresh",
        "last_updated": FRESH_AT,
        "checked_at": FRESH_AT,
        "status": "live",
        "basis": "UC Gold Borrower 360",
        "source": "source_readiness",
    }
    assert response.headers["cache-control"] == "private, no-store"
    # Exactly one statement: the readiness row, the basis bound by name.
    assert len(sql.calls) == 1
    statement, params = sql.calls[0]
    assert "mip.gold.source_readiness" in statement
    assert "source_name = :basis" in statement
    assert params == {"basis": "UC Gold Borrower 360"}
    upper = statement.upper()
    for forbidden in ("DESCRIBE", "COUNT(", "INFORMATION_SCHEMA", "TABLE_LINEAGE", "TBLPROPERTIES"):
        assert forbidden not in upper
    for internal in ("NOTE", "SOURCE_TABLE", "ROW_COUNT"):
        assert internal not in upper
    # Audit-free: no row of any kind.
    assert audit.list(limit=100) == []


def test_the_serialized_key_set_is_pinned() -> None:
    _install(_ReadinessSql())

    body = _get("borrower_360").json()

    assert set(body) == {"asset_key", "title", "freshness", "last_updated", "checked_at", "status", "basis", "source"}
    for internal in ("note", "source_table", "row_count", "fqn", "asset_path", "catalog", "uc_object"):
        assert internal not in body


def test_an_unknown_key_is_a_404_before_any_sql() -> None:
    sql = _ReadinessSql()
    _install(sql)

    response = _get("system.access.table_lineage")

    assert response.status_code == 404
    assert response.json() == {"detail": "asset not found"}
    assert sql.calls == []


def test_no_forwarded_identity_is_a_401() -> None:
    sql = _ReadinessSql()
    _install(sql)

    # The conftest default group header alone never authenticates.
    response = TestClient(app).get("/api/v1/assets/borrower_360/freshness")

    assert response.status_code == 401
    assert sql.calls == []


def test_a_warehouse_failure_is_a_503_that_is_never_cached() -> None:
    cache = TTLCache()
    failing = _ReadinessSql(fail=True)
    service = _install(failing, cache)

    response = _get("borrower_360")

    assert response.status_code == 503
    rendered = response.text.lower()
    assert "permission_denied" not in rendered and "statement_id" not in rendered
    assert cache.get("asset_freshness:borrower_360") is None
    # The next open asks again (and succeeds once the warehouse is back).
    failing.fail = False
    second = _get("borrower_360")
    assert second.status_code == 200
    assert len(failing.calls) == 2
    assert service.get_freshness("borrower_360").source == "source_readiness"
    assert len(failing.calls) == 2  # now served from the cache


def test_no_readiness_row_is_unavailable_and_cached() -> None:
    sql = _ReadinessSql(rows=[])
    _install(sql)

    first = _get("lead_scores")
    second = _get("lead_scores")

    assert first.status_code == 200
    assert first.json()["source"] == "unavailable"
    assert first.json()["freshness"] == "unavailable"
    assert first.json()["status"] == "unknown"
    assert first.json()["last_updated"] is None
    assert second.json() == first.json()
    assert len(sql.calls) == 1


@pytest.mark.parametrize("asset_key", ["fn_in_the_money", "offer_rules_config", "source_readiness"])
def test_a_not_tracked_asset_runs_no_sql(asset_key: str) -> None:
    sql = _ReadinessSql()
    _install(sql)

    response = _get(asset_key)

    assert response.status_code == 200
    body = response.json()
    assert (body["source"], body["freshness"], body["status"], body["basis"]) == (
        "not_tracked",
        "unavailable",
        "unknown",
        None,
    )
    assert sql.calls == []


def test_an_unknown_readiness_status_reads_unknown() -> None:
    _install(_ReadinessSql(rows=[{"status": "weird", "last_updated": FRESH_AT, "checked_at": None}]))

    body = _get("lien_current").json()

    assert body["status"] == "unknown"
    assert body["basis"] == "Voluntary Lien"


def test_an_admin_reads_the_same_payload() -> None:
    _install(_ReadinessSql())

    response = TestClient(app).get(
        "/api/assets/borrower_360/freshness",
        headers={"X-Forwarded-Email": "admin@summit.example"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "source_readiness"
