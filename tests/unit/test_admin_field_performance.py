"""GET /api/admin/field-performance (D-platform-process-d2 step 11).

Admin-only (a non-admin, an auditor and an approver get 403), days 7 or 28
only, a 20-sample p75 floor, facet parsing for the interaction and
client-error tables, 503 when Lakebase is down, no audit write, and the
AUDIT EXEMPT docstring.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import admin_field_performance
from backend.config.settings import settings
from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError
from backend.services.resilience import DependencyDownError
from backend.services.rum_buckets import bucket_index, zero_buckets
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

PATH = "/api/v1/admin/field-performance"
ADMIN = "ops@summit-mortgage.example"
AUDITOR = "auditor.one@summit-mortgage.example"
APPROVER = "approver@summit-mortgage.example"
ADMIN_HEADERS = {"X-Forwarded-Email": ADMIN, "X-Forwarded-Groups": "mip-admin"}


class _Lakebase:
    def __init__(self, rows: list[dict[str, Any]] | None = None, error: BaseException | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, dict[str, Any] | None, int]] = []

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.calls.append((sql, params, limit))
        if self.error is not None:
            raise self.error
        return self.rows

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        raise AssertionError("the field performance read never writes")


def _histogram(metric: str, values: list[float]) -> list[int]:
    buckets = zero_buckets()
    for value in values:
        buckets[bucket_index(metric, value)] += 1
    return buckets


def _row(metric: str, route: str, values: list[float], *, facet: str = "", rating: str = "good",
         builds: tuple[str, ...] = ("abc123def456",)) -> dict[str, Any]:
    return {
        "metric": metric, "route": route, "facet": facet, "rating": rating, "builds": list(builds),
        "sample_count": len(values), "buckets": _histogram(metric, values),
    }


@pytest.fixture(autouse=True)
def _roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(settings, "app_env", "sandbox")
    monkeypatch.setattr(settings, "admin_emails", ADMIN)
    monkeypatch.setattr(settings, "admin_identities", "")
    monkeypatch.setattr(settings, "auditor_emails", AUDITOR)
    monkeypatch.setattr(settings, "auditor_identities", "")
    monkeypatch.setattr(settings, "approver_emails", APPROVER)
    monkeypatch.setattr(settings, "approver_identities", "")


@pytest.fixture
def lakebase(monkeypatch: pytest.MonkeyPatch) -> _Lakebase:
    fake = _Lakebase()
    monkeypatch.setattr(admin_field_performance, "get_lakebase_client", lambda: fake)
    return fake


@pytest.fixture
def audit_store() -> Iterator[InMemoryAuditStore]:
    prior = app.dependency_overrides.get(get_audit_store)
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        yield store
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forwarded-Email": "lo@summit-mortgage.example", "X-Forwarded-Groups": "analysts"},
        {"X-Forwarded-Email": AUDITOR, "X-Forwarded-Groups": ""},
        {"X-Forwarded-Email": APPROVER, "X-Forwarded-Groups": ""},
    ],
    ids=["non-admin", "auditor", "approver"],
)
def test_only_an_administrator_reads_it(client: TestClient, lakebase: _Lakebase, headers: dict[str, str]) -> None:
    response = client.get(PATH, params={"days": 7}, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}
    assert lakebase.calls == []


@pytest.mark.parametrize("days", [7, 28])
def test_days_7_and_28_read_the_window_from_lakebase(client: TestClient, lakebase: _Lakebase, days: int) -> None:
    response = client.get(PATH, params={"days": days}, headers=ADMIN_HEADERS)

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    body = response.json()
    today = datetime.now(UTC).date()
    assert body["days"] == days
    assert body["since"] == (today - timedelta(days=days - 1)).isoformat()
    [(sql, params, _limit)] = lakebase.calls
    assert "FROM mip_app.rum_daily WHERE day >= %(since)s::date AND sample_count > 0" in sql
    assert params == {"since": today - timedelta(days=days - 1)}


@pytest.mark.parametrize("days", ["14", "0", "7.0", "abc", "-7"])
def test_any_other_window_is_422(client: TestClient, lakebase: _Lakebase, days: str) -> None:
    assert client.get(PATH, params={"days": days}, headers=ADMIN_HEADERS).status_code == 422
    assert lakebase.calls == []


def test_the_floor_hides_a_p75_under_20_samples_and_shows_it_at_20(client: TestClient, lakebase: _Lakebase) -> None:
    lakebase.rows = [
        # 12 good + 8 needs-improvement LCP samples on one route: 20 in all.
        _row("lcp", "/lead-queue", [1200.0] * 12, facet="h1"),
        _row("lcp", "/lead-queue", [3000.0] * 8, facet="img", rating="needs_improvement"),
        _row("inp", "/lead-queue", [90.0] * 19, facet="filter"),
        _row("cls", "/", [0.02] * 25),
    ]

    body = client.get(PATH, params={"days": 7}, headers=ADMIN_HEADERS).json()

    by_route = {row["route"]: row for row in body["vitals"]}
    lcp = by_route["/lead-queue"]["lcp"]
    assert lcp["samples"] == 20 and lcp["floor_applied"] is False
    assert lcp["rating"] == "needs_improvement" and 2500 < lcp["p75"] <= 3000
    inp = by_route["/lead-queue"]["inp"]
    assert inp == {"p75": None, "rating": None, "samples": 19, "floor_applied": True}
    assert by_route["/lead-queue"]["cls"] == {"p75": None, "rating": None, "samples": 0, "floor_applied": True}
    assert by_route["/"]["cls"]["rating"] == "good" and by_route["/"]["cls"]["samples"] == 25


def test_facets_feed_the_interaction_and_client_error_tables(client: TestClient, lakebase: _Lakebase) -> None:
    lakebase.rows = [
        # 28 good + 12 needs-improvement: the 30th of 40 sits in the (250, 300] bucket.
        _row("inp", "/lead-queue", [120.0] * 28, facet="lead-row"),
        _row("inp", "/lead-queue", [300.0] * 12, facet="lead-row", rating="needs_improvement"),
        _row("inp_input_delay", "/lead-queue", [10.0] * 40, facet="lead-row"),
        _row("inp_processing", "/lead-queue", [200.0] * 40, facet="lead-row"),
        _row("inp_presentation", "/lead-queue", [60.0] * 40, facet="lead-row"),
        _row("inp", "/lead-queue", [80.0] * 3, facet="filter"),
        _row("client_error", "/borrower-360/:id", [1.0] * 4, facet="TypeError|render|drawer", rating="info"),
        _row("client_error", "/borrower-360/:id", [1.0] * 2, facet="TypeError|render|drawer", rating="info"),
        _row("client_error", "/", [1.0], facet="ChunkLoadError|chunk|-", rating="info"),
        _row("client_error", "/", [1.0], facet="malformed", rating="info"),
        _row("api_call", "/lead-queue", [120.0] * 40, facet="/api/leads|hit", rating="info",
             builds=("fedcba987654",)),
    ]

    body = client.get(PATH, params={"days": 28}, headers=ADMIN_HEADERS).json()

    interactions = {(row["route"], row["interaction_target"]): row for row in body["interactions"]}
    lead_row = interactions[("/lead-queue", "lead-row")]
    assert lead_row["samples"] == 40
    assert lead_row["inp"]["rating"] == "needs_improvement"
    assert lead_row["input_delay"]["rating"] == "good"
    assert lead_row["processing"]["floor_applied"] is False
    assert interactions[("/lead-queue", "filter")]["inp"]["floor_applied"] is True
    assert body["client_errors"] == [
        {"route": "/borrower-360/:id", "error_name": "TypeError", "error_kind": "render", "boundary": "drawer",
         "count": 6},
        {"route": "/", "error_name": "ChunkLoadError", "error_kind": "chunk", "boundary": None, "count": 1},
    ]
    assert body["builds"] == ["abc123def456", "fedcba987654"]
    assert all(row["route"] != "/lead-queue" or row["lcp"]["samples"] == 0 for row in body["vitals"])


def test_browser_telemetry_reports_the_effective_setting(
    client: TestClient, lakebase: _Lakebase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mip_rum_enabled", False)
    assert client.get(PATH, params={"days": 7}, headers=ADMIN_HEADERS).json()["browser_telemetry"] == "off"
    monkeypatch.setattr(settings, "mip_rum_enabled", True)
    assert client.get(PATH, params={"days": 7}, headers=ADMIN_HEADERS).json()["browser_telemetry"] == "on"


@pytest.mark.parametrize(
    "error",
    [LakebaseError("connection to 10.0.0.1 failed"), DependencyDownError("lakebase", reason="breaker open")],
    ids=["lakebase-error", "breaker-open"],
)
def test_lakebase_down_is_a_503_with_the_safe_detail(
    client: TestClient, lakebase: _Lakebase, error: BaseException
) -> None:
    lakebase.error = error

    response = client.get(PATH, params={"days": 7}, headers=ADMIN_HEADERS)

    assert response.status_code == 503
    assert response.json()["detail"] == "lakebase is temporarily unavailable"
    assert "10.0.0.1" not in response.text


def test_an_unresolvable_client_is_a_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _unresolved() -> Any:
        raise LakebaseError("LAKEBASE_HOST is not set")

    monkeypatch.setattr(admin_field_performance, "get_lakebase_client", _unresolved)
    response = client.get(PATH, params={"days": 7}, headers=ADMIN_HEADERS)
    assert response.status_code == 503
    assert "LAKEBASE_HOST" not in response.text


def test_the_read_writes_no_audit_row(
    client: TestClient, lakebase: _Lakebase, audit_store: InMemoryAuditStore
) -> None:
    lakebase.rows = [_row("lcp", "/", [900.0] * 30)]
    assert client.get(PATH, params={"days": 7}, headers=ADMIN_HEADERS).status_code == 200
    assert audit_store.list(limit=50) == []


def test_the_handler_is_documented_audit_exempt() -> None:
    doc = inspect.getdoc(admin_field_performance.field_performance) or ""
    assert "AUDIT EXEMPT: read-only aggregate operational telemetry; no borrower data" in doc
