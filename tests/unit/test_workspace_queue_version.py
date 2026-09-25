"""GET /api/v1/workspace/queue-version: the audit-free Lead Queue change signal.

Audit 2026-09-21 ``states-09``. The Lead Queue polls this about once a minute
and shows "Queue updated · Refresh" when it moves, instead of re-reading
``/api/leads`` (which writes a VIEW_LEADS audit row). Pins: fail-closed auth,
no audit write, the SQL's reach, the 30 s cache, version sensitivity to each
of the eight inputs, the response contract, Lakebase failure mapping, the
backpressure bucket, and that /api/health never carries the version.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.config.settings import settings
from backend.main import app
from backend.schemas.queue_version import QueueVersionResponse
from backend.services import audit_store as audit_mod
from backend.services.backpressure import BackpressureController
from backend.services.lakebase import LakebaseError
from backend.services.resilience_cache import TTLCache
from backend.services.workspace_queue_version import (
    QUEUE_VERSION_SQL,
    VERSION_INPUTS,
    QueueVersionService,
    get_queue_version_service,
    queue_version_from_row,
)

client = TestClient(app)
ACTOR_HEADERS = {"X-Forwarded-Email": "approver@summit.example"}
PATHS = ["/api/workspace/queue-version", "/api/v1/workspace/queue-version"]

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
BASE_ROW: dict[str, Any] = {
    "approvals_count": 12,
    "approvals_latest": T0,
    "assignments_count": 5,
    "assignments_latest": T0 - timedelta(minutes=5),
    "dispositions_count": 3,
    "dispositions_latest": T0 - timedelta(hours=1),
    "outcomes_count": 1,
    "outcomes_latest": T0 - timedelta(days=1),
}


class FakeLakebase:
    """Records every statement; answers the aggregate row."""

    def __init__(self, row: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.row = dict(BASE_ROW if row is None else row)
        self.error = error
        self.statements: list[str] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self.statements.append(sql)
        if self.error is not None:
            raise self.error
        return dict(self.row)

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:  # pragma: no cover - must not run
        self.statements.append(sql)
        raise AssertionError("the queue version must never write")


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake() -> FakeLakebase:
    return FakeLakebase()


@pytest.fixture
def served(fake: FakeLakebase, monkeypatch: pytest.MonkeyPatch):
    """The route with a fresh cache over the fake, and the audit store trapped."""

    service = QueueVersionService(lambda: fake, cache=TTLCache())
    app.dependency_overrides[get_queue_version_service] = lambda: service

    def _no_audit_store() -> None:
        raise AssertionError("the queue version must not resolve the audit store")

    monkeypatch.setattr(audit_mod, "get_audit_store", _no_audit_store)
    yield service
    app.dependency_overrides.pop(get_queue_version_service, None)


@pytest.mark.parametrize("path", PATHS)
def test_rejects_a_caller_with_no_identity(path: str, served: QueueVersionService, fake: FakeLakebase) -> None:
    response = client.get(path)
    assert response.status_code == 401
    assert fake.statements == []


def test_rejects_spoofed_identity_when_the_edge_is_untrusted(
    served: QueueVersionService, fake: FakeLakebase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)
    assert client.get(PATHS[1], headers=ACTOR_HEADERS).status_code == 401
    assert fake.statements == []


@pytest.mark.parametrize("path", PATHS)
def test_answers_an_opaque_version_with_one_select_and_no_audit_write(
    path: str, served: QueueVersionService, fake: FakeLakebase,
) -> None:
    response = client.get(path, headers=ACTOR_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"version"}
    assert re.fullmatch(r"[0-9a-f]{32}", body["version"])
    assert body["version"] == queue_version_from_row(BASE_ROW)
    assert len(fake.statements) == 1
    assert fake.statements[0].lstrip().upper().startswith("WITH")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT)\b", fake.statements[0], re.IGNORECASE)


def test_the_sql_reads_only_the_four_decision_ledgers_as_aggregates() -> None:
    tables = set(re.findall(r"\bFROM\s+([a-z_]+\.[a-z_]+)", QUEUE_VERSION_SQL, re.IGNORECASE))
    assert tables == {
        "mip_app.approvals",
        "mip_app.lead_assignments",
        "mip_app.call_dispositions",
        "mip_app.feedback",
    }
    assert "assignment_id IS NOT NULL" in QUEUE_VERSION_SQL
    assert "status_updated_at" in QUEUE_VERSION_SQL
    lowered = QUEUE_VERSION_SQL.lower()
    # No row identity or actor is ever projected, and nothing outside Lakebase.
    for forbidden in ("borrower_id", "actor", "email", "mip.gold", "mip.silver", "mip.ref", "mip.audit"):
        assert forbidden not in lowered, forbidden
    select_list = QUEUE_VERSION_SQL.rsplit("SELECT", 1)[1].split("FROM", 1)[0]
    projected = set(re.findall(r"AS\s+([a-z_]+)", select_list))
    assert projected == set(VERSION_INPUTS)


def test_a_30_second_cache_serves_two_calls_from_one_read_then_reads_again() -> None:
    fake = FakeLakebase()
    clock = Clock()
    service = QueueVersionService(lambda: fake, cache=TTLCache(now=clock))
    first = service.current()
    clock.now += 29.0
    assert service.current() == first
    assert len(fake.statements) == 1
    fake.row["approvals_count"] = 13
    clock.now += 2.0
    assert service.current() != first
    assert len(fake.statements) == 2


def test_a_failed_read_is_not_served_stale() -> None:
    fake = FakeLakebase()
    clock = Clock()
    service = QueueVersionService(lambda: fake, cache=TTLCache(now=clock))
    service.current()
    clock.now += 31.0
    fake.error = LakebaseError("connection reset")
    with pytest.raises(LakebaseError):
        service.current()


def test_identical_rows_give_the_same_version() -> None:
    assert queue_version_from_row(dict(BASE_ROW)) == queue_version_from_row(dict(BASE_ROW))


@pytest.mark.parametrize("name", VERSION_INPUTS)
def test_each_of_the_eight_inputs_moves_the_version(name: str) -> None:
    changed = dict(BASE_ROW)
    value = changed[name]
    changed[name] = value + 1 if isinstance(value, int) else value + timedelta(seconds=1)
    assert queue_version_from_row(changed) != queue_version_from_row(BASE_ROW)


def test_an_empty_ledger_set_still_has_a_version() -> None:
    empty = {name: (0 if name.endswith("_count") else None) for name in VERSION_INPUTS}
    assert re.fullmatch(r"[0-9a-f]{32}", queue_version_from_row(empty))
    assert queue_version_from_row(empty) != queue_version_from_row(BASE_ROW)


def test_the_response_contract_is_32_hex_and_closed() -> None:
    assert QueueVersionResponse(version="a" * 32).version == "a" * 32
    for bad in ("A" * 32, "a" * 31, "g" * 32, ""):
        with pytest.raises(ValidationError):
            QueueVersionResponse(version=bad)
    with pytest.raises(ValidationError):
        QueueVersionResponse.model_validate({"version": "a" * 32, "count": 12})


def test_a_lakebase_failure_is_a_503(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLakebase(error=LakebaseError("Lakebase fetchone failed: secret-host detail"))
    service = QueueVersionService(lambda: fake, cache=TTLCache())
    app.dependency_overrides[get_queue_version_service] = lambda: service
    try:
        response = client.get(PATHS[1], headers=ACTOR_HEADERS)
    finally:
        app.dependency_overrides.pop(get_queue_version_service, None)
    assert response.status_code == 503
    assert "secret-host" not in response.text


def test_a_missing_lakebase_configuration_is_a_503_not_a_500() -> None:
    def _unconfigured() -> FakeLakebase:
        raise LakebaseError("LAKEBASE_HOST is not set")

    service = QueueVersionService(_unconfigured, cache=TTLCache())
    app.dependency_overrides[get_queue_version_service] = lambda: service
    try:
        response = client.get(PATHS[1], headers=ACTOR_HEADERS)
    finally:
        app.dependency_overrides.pop(get_queue_version_service, None)
    assert response.status_code == 503


def test_backpressure_classifies_it_as_a_lakebase_read() -> None:
    budget = BackpressureController().classify("GET", "/api/v1/workspace/queue-version")
    assert budget is not None
    assert budget.scope == "lakebase-read"
    assert budget.dependency == "lakebase"


def test_health_never_carries_the_version(served: QueueVersionService, fake: FakeLakebase) -> None:
    response = client.get("/api/health", headers=ACTOR_HEADERS)
    assert "version" not in {key for key in response.json() if "queue" in key}
    assert "queue" not in response.text
    assert fake.statements == []
