"""Audit explorer backend (audit tables-10): facets, count and the export receipt.

``GET /api/v1/audit/facets`` and ``GET /api/v1/audit/count`` are admin-gated,
AUDIT-FREE reads of ``mip_app.action_audit``: they write no ledger row and run
no INSERT. ``/count`` builds exactly the WHERE ``/events/page`` pages through
(the shared ``audit_filter_clauses``). ``POST /api/v1/audit/export-receipt``
verifies the explorer's declaration against the event ids it sends and writes
exactly one server-owned ``AUDIT_EXPORT`` row with four metadata keys.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.audit import EXPORT_DIGEST_MISMATCH_DETAIL
from backend.config.settings import settings
from backend.main import app
from backend.services.audit_event_types import is_server_owned_audit_event_type
from backend.services.audit_filter_sql import AUDIT_COUNT_CAP, AUDIT_FACET_LIMIT
from backend.services.audit_lakebase_store import LakebaseAuditStore
from backend.services.audit_pagination import audit_filter_fingerprint
from backend.services.audit_store import get_audit_store
from backend.services.backpressure import BackpressureController
from backend.services.lakebase import LakebaseError, get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

ADMIN = "ops@summit-mortgage.example"
ADMIN_HEADERS = {"X-Forwarded-Email": ADMIN, "X-Forwarded-Groups": "mip-admin"}
NON_ADMIN_HEADERS = {"X-Forwarded-Email": "lo@summit-mortgage.example", "X-Forwarded-Groups": ""}
EVENT_IDS = [
    "0f8fad5b-d9cb-469f-a165-70867728950e",
    "7c9e6679-7425-40de-944b-e07fc1f90ae7",
]
FILTERS = {"event_type": "APPROVE", "since": "2026-09-01T00:00:00Z"}
FILTER_PARAMS = {
    "actor": "approver@summit-mortgage.example",
    "action": "outreach.approve",
    "entity_id": "approval-1",
    "borrower_id": "B-ABCDEFGHIJKLM",
    "subject_clip": "1234567890",
    "event_type": "APPROVE",
    "correlation_id": "corr-0123abcd-4567ef",
    "since": "2026-09-01T00:00:00Z",
    "until": "2026-09-30T00:00:00Z",
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _declaration(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "row_count": len(EVENT_IDS),
        "csv_sha256": _sha256("event_id,event_type\n"),
        "event_ids": list(EVENT_IDS),
        "event_ids_sha256": _sha256(json.dumps(EVENT_IDS, separators=(",", ":"))),
        "filters": dict(FILTERS),
    }
    body.update(overrides)
    return body


class _RecordingLakebase:
    """Answers the explorer's reads; records every statement it is handed."""

    def __init__(self, *, count: int = 3, facet_rows: int = 2, error: bool = False) -> None:
        self.count = count
        self.facet_rows = facet_rows
        self.error = error
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def _record(self, sql: str, params: dict[str, Any] | None) -> None:
        self.statements.append((sql, dict(params or {})))
        if self.error:
            raise LakebaseError("lakebase unavailable")

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._record(sql, params)
        return {"event_count": self.count}

    def fetchall(
        self, sql: str, params: dict[str, Any] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._record(sql, params)
        if "GROUP BY" not in sql:
            return []
        rows = [{"value": f"VALUE_{index}", "event_count": 100 - index} for index in range(self.facet_rows)]
        return [*rows, {"value": None, "event_count": 1}][:limit]

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self._record(sql, params)
        raise AssertionError("the explorer reads must not write")


@pytest.fixture(autouse=True)
def _trusted_test_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)


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
def lakebase() -> Iterator[_RecordingLakebase]:
    prior = app.dependency_overrides.get(get_lakebase_client)
    fake = _RecordingLakebase()
    app.dependency_overrides[get_lakebase_client] = lambda: fake
    try:
        yield fake
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior


# ---------------------------------------------------------------------------
# Admin gate, and no audit write on the reads.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/audit/facets"),
        ("GET", "/api/v1/audit/count"),
        ("POST", "/api/v1/audit/export-receipt"),
    ],
)
def test_non_admins_are_refused(
    method: str, path: str, audit_store: InMemoryAuditStore, lakebase: _RecordingLakebase
) -> None:
    response = client.request(method, path, json=_declaration(), headers=NON_ADMIN_HEADERS)

    assert response.status_code == 403
    assert audit_store.list(limit=10) == []
    assert lakebase.statements == []


@pytest.mark.parametrize("path", ["/api/v1/audit/facets", "/api/v1/audit/count"])
def test_explorer_reads_are_audit_free(
    path: str, audit_store: InMemoryAuditStore, lakebase: _RecordingLakebase
) -> None:
    response = client.get(path, headers=ADMIN_HEADERS)

    assert response.status_code == 200, response.text
    assert audit_store.list(limit=10) == [], "a read must never write an audit row"
    assert lakebase.statements, "the read reached the ledger"
    for sql, _params in lakebase.statements:
        assert "INSERT" not in sql.upper()
        assert re.search(r"\bFROM mip_app\.action_audit\b", sql)


def test_facets_default_to_a_90_day_window_and_drop_nulls(lakebase: _RecordingLakebase) -> None:
    response = client.get("/api/v1/audit/facets", headers=ADMIN_HEADERS)

    body = response.json()
    assert response.status_code == 200
    assert [facet["value"] for facet in body["event_types"]] == ["VALUE_0", "VALUE_1"]
    assert body["actions"][0] == {"value": "VALUE_0", "count": 100}
    assert body["truncated"] == {"event_types": False, "actions": False, "actors": False}
    assert body["until"] is None
    assert len(lakebase.statements) == 3
    grouped = {sql.split(" AS value")[0].removeprefix("SELECT ") for sql, _ in lakebase.statements}
    assert grouped == {"event_type", "metadata->>'action'", "actor_email"}
    for sql, params in lakebase.statements:
        assert "event_at >= %(since)s" in sql and "IS NOT NULL" in sql
        assert sql.endswith(f"LIMIT {AUDIT_FACET_LIMIT + 1}")
        assert "until" not in params
    window = datetime.fromisoformat(body["since"])
    expected = datetime.now(UTC) - timedelta(days=90)
    assert abs((window - expected).total_seconds()) < 60
    assert lakebase.statements[0][1]["since"] == window


def test_facets_flag_truncation_per_facet(lakebase: _RecordingLakebase) -> None:
    lakebase.facet_rows = AUDIT_FACET_LIMIT + 1

    body = client.get(
        "/api/v1/audit/facets",
        params={"since": "2026-09-01T00:00:00Z", "until": "2026-09-30T00:00:00Z"},
        headers=ADMIN_HEADERS,
    ).json()

    assert body["truncated"] == {"event_types": True, "actions": True, "actors": True}
    assert len(body["actors"]) == AUDIT_FACET_LIMIT
    assert all("until" in params for _sql, params in lakebase.statements)


@pytest.mark.parametrize("path", ["/api/v1/audit/facets", "/api/v1/audit/count"])
def test_a_lakebase_failure_answers_503(path: str, lakebase: _RecordingLakebase) -> None:
    lakebase.error = True

    response = client.get(path, headers=ADMIN_HEADERS)

    assert response.status_code == 503
    assert "lakebase unavailable" not in response.text


# ---------------------------------------------------------------------------
# Count: the page's WHERE, a cap, the page's validation.
# ---------------------------------------------------------------------------


class _RecordingPageClient:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def fetchall(
        self, sql: str, params: dict[str, Any] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self.statements.append((sql, dict(params or {})))
        return []


def _where(sql: str) -> str:
    match = re.search(r"WHERE (.*?)\s*(?:ORDER BY|LIMIT)", sql, flags=re.DOTALL)
    assert match, sql
    return " ".join(match.group(1).split())


def test_count_and_page_build_the_same_where_for_the_same_filters(
    lakebase: _RecordingLakebase,
) -> None:
    page_client = _RecordingPageClient()
    prior = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: LakebaseAuditStore(page_client)  # type: ignore[arg-type]
    try:
        page = client.get("/api/v1/audit/events/page", params=FILTER_PARAMS, headers=ADMIN_HEADERS)
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior
    count = client.get("/api/v1/audit/count", params=FILTER_PARAMS, headers=ADMIN_HEADERS)

    assert page.status_code == 200, page.text
    assert count.status_code == 200, count.text
    (page_sql, page_params), (count_sql, count_params) = page_client.statements[0], lakebase.statements[0]
    snapshot_clause = (
        "pg_visible_in_snapshot(mip_app.action_audit.xmin::text::xid8, snapshot_anchor.snapshot) AND "
    )
    assert _where(page_sql) == snapshot_clause + _where(count_sql)
    page_filters = {key: value for key, value in page_params.items() if key not in {"limit", "offset"}}
    assert page_filters == count_params
    # Every one of the nine filters reached the WHERE.
    assert len(_where(count_sql).split(" AND ")) == 9


def test_count_caps_at_the_bound(lakebase: _RecordingLakebase) -> None:
    lakebase.count = AUDIT_COUNT_CAP + 1

    body = client.get("/api/v1/audit/count", headers=ADMIN_HEADERS).json()

    assert body == {"count": AUDIT_COUNT_CAP, "capped": True, "cap": AUDIT_COUNT_CAP}
    sql, params = lakebase.statements[0]
    assert "WHERE" not in sql, "no filters, no WHERE"
    assert sql.endswith(f"LIMIT {AUDIT_COUNT_CAP + 1}) t")
    assert params == {}


def test_count_below_the_cap_is_exact(lakebase: _RecordingLakebase) -> None:
    lakebase.count = 42

    body = client.get("/api/v1/audit/count", params={"event_type": "APPROVE"}, headers=ADMIN_HEADERS).json()

    assert body == {"count": 42, "capped": False, "cap": AUDIT_COUNT_CAP}


@pytest.mark.parametrize(
    ("param", "value", "detail"),
    [
        ("borrower_id", "not-a-borrower", "invalid borrower_id"),
        ("correlation_id", "ops@summit-mortgage.example", "invalid correlation_id"),
    ],
)
def test_count_validates_like_the_page(
    param: str, value: str, detail: str, lakebase: _RecordingLakebase
) -> None:
    count = client.get("/api/v1/audit/count", params={param: value}, headers=ADMIN_HEADERS)
    page = client.get("/api/v1/audit/events/page", params={param: value}, headers=ADMIN_HEADERS)

    assert (count.status_code, count.json()["detail"]) == (422, detail)
    assert (page.status_code, page.json()["detail"]) == (422, detail)
    assert lakebase.statements == []


def test_count_takes_no_cursor_limit_or_event_id(lakebase: _RecordingLakebase) -> None:
    response = client.get(
        "/api/v1/audit/count",
        params={"cursor": "x", "limit": "5", "event_id": "not an id"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert lakebase.statements[0][1] == {}


# ---------------------------------------------------------------------------
# The export receipt.
# ---------------------------------------------------------------------------


def test_receipt_writes_one_audit_export_row_with_exactly_four_keys(
    audit_store: InMemoryAuditStore,
) -> None:
    response = client.post("/api/v1/audit/export-receipt", json=_declaration(), headers=ADMIN_HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    rows = audit_store.list(limit=10)
    assert len(rows) == 1
    row = rows[0]
    fingerprint = audit_filter_fingerprint(dict(FILTERS))
    assert (row.event_type, row.action, row.entity_type) == (
        "AUDIT_EXPORT",
        "audit_explorer.export",
        "audit_ledger",
    )
    assert row.entity_id == f"export-{fingerprint[:16]}"
    assert row.actor == ADMIN
    assert row.payload_json == {
        "exported_row_count": 2,
        "csv_sha256": body["csv_sha256"],
        "event_ids_sha256": _sha256(json.dumps(EVENT_IDS, separators=(",", ":"))),
        "filter_fingerprint": fingerprint,
    }
    assert body["audit_event_id"] == row.event_id
    assert body["event_type"] == "AUDIT_EXPORT"
    assert (body["actor"], body["row_count"], body["filter_fingerprint"]) == (ADMIN, 2, fingerprint)
    assert body["recorded_at"]


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"event_ids_sha256": _sha256("[]")}, EXPORT_DIGEST_MISMATCH_DETAIL),
        ({"row_count": 1}, EXPORT_DIGEST_MISMATCH_DETAIL),
        ({"event_ids": ["bad id!", EVENT_IDS[1]]}, "invalid event_ids"),
    ],
    ids=["digest", "row-count", "invalid-id"],
)
def test_a_bad_declaration_answers_422_and_writes_nothing(
    overrides: dict[str, object], detail: str, audit_store: InMemoryAuditStore
) -> None:
    response = client.post(
        "/api/v1/audit/export-receipt", json=_declaration(**overrides), headers=ADMIN_HEADERS
    )

    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert audit_store.list(limit=10) == []


def test_duplicate_event_ids_answer_422_and_write_nothing(audit_store: InMemoryAuditStore) -> None:
    duplicated = [EVENT_IDS[0], EVENT_IDS[0]]
    body = _declaration(
        event_ids=duplicated,
        event_ids_sha256=_sha256(json.dumps(duplicated, separators=(",", ":"))),
    )

    response = client.post("/api/v1/audit/export-receipt", json=body, headers=ADMIN_HEADERS)

    assert response.status_code == 422
    assert audit_store.list(limit=10) == []


def test_a_non_json_body_answers_415(audit_store: InMemoryAuditStore) -> None:
    response = client.post(
        "/api/v1/audit/export-receipt",
        content=json.dumps(_declaration()),
        headers={**ADMIN_HEADERS, "Content-Type": "text/plain"},
    )

    assert response.status_code == 415
    assert audit_store.list(limit=10) == []


def test_client_cannot_self_report_an_audit_export(audit_store: InMemoryAuditStore) -> None:
    assert is_server_owned_audit_event_type("AUDIT_EXPORT")

    response = client.post(
        "/api/v1/audit/event",
        json={
            "actor": ADMIN,
            "action": "audit_explorer.export",
            "entity_type": "audit_ledger",
            "entity_id": "export-self-report",
            "event_type": "AUDIT_EXPORT",
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert audit_store.list(limit=10) == []


def test_receipt_write_is_rate_limited_as_a_mutation() -> None:
    budget = BackpressureController().classify("POST", "/api/v1/audit/export-receipt")

    assert budget is not None
    assert budget.scope == "mutation"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/audit/facets"),
        ("GET", "/api/audit/count"),
        ("POST", "/api/audit/export-receipt"),
    ],
)
def test_the_deprecated_unversioned_alias_serves_the_same_route(
    method: str, path: str, audit_store: InMemoryAuditStore, lakebase: _RecordingLakebase
) -> None:
    response = client.request(method, path, json=_declaration(), headers=ADMIN_HEADERS)

    assert response.status_code == 200, response.text
    expected_rows = 1 if method == "POST" else 0
    assert len(audit_store.list(limit=10)) == expected_rows
