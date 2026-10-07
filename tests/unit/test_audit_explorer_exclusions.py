"""'Exclude my ledger reads' on the audit explorer (W5d, tables-10).

``GET /audit/events``, ``/audit/events/page`` and ``/audit/count`` take
``exclude_own_ledger_reads=true``. It drops the VIEW_AUDIT_LEDGER rows the
SERVER-resolved reader wrote (the ``AuditReaderDep`` identity), never rows
named by a query value, through one NULL-safe clause the page and the count
share. The flag joins the filter fingerprint only when it is on, so a default
cursor is byte-identical to the base tree's (golden below) and a cursor never
crosses the flag. Every served read still writes its own VIEW_AUDIT_LEDGER
row. The real-PostgreSQL proof (count == page total, NULL-safety) is
tests/integration/test_audit_exclusion_postgres.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.config.settings import settings
from backend.main import app
from backend.schemas.audit import AuditEvent
from backend.services.audit_filter_sql import audit_count_statement, audit_filter_clauses
from backend.services.audit_lakebase_store import LakebaseAuditStore
from backend.services.audit_pagination import audit_filter_fingerprint
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

ADMIN = "ops@summit-mortgage.example"
AUDITOR = "auditor.one@summit-mortgage.example"
APPROVER = "approver@summit-mortgage.example"
ADMIN_HEADERS = {"X-Forwarded-Email": ADMIN, "X-Forwarded-Groups": "mip-admin"}
AUDITOR_HEADERS = {"X-Forwarded-Email": AUDITOR, "X-Forwarded-Groups": ""}
PAGE = "/api/v1/audit/events/page"
EXCLUSION_CLAUSE = (
    "(event_type IS DISTINCT FROM 'VIEW_AUDIT_LEDGER' "
    "OR actor_email IS DISTINCT FROM %(exclude_ledger_reads_by)s)"
)

# Captured on the base tree (2931d82c) with tools-free python -P: the same
# seeded store, request and secret as test_the_default_cursor_and_fingerprint_
# match_the_base_tree. The flag off must leave both byte-identical.
GOLDEN_FINGERPRINT = "f5d1baea6193b3a39ac5ab534f8fe4676dbd5e5a67f0c49d887af34411156091"
GOLDEN_CURSOR = (
    "eyJhIjo0LCJmIjoiZjVkMWJhZWE2MTkzYjNhMzlhYzVhYjUzNGY4ZmU0Njc2ZGJkNWU1YTY3ZjBjNDlkODg3"
    "YWYzNDQxMTE1NjA5MSIsInMiOjUsInQiOiJtZW1vcnk6NSIsInYiOjJ9.NivDzzm70-g9lD5rqICxbZKpyMQ4VLsXHfpM2FCJyGo"
)


@pytest.fixture(autouse=True)
def _roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "admin_emails", ADMIN)
    monkeypatch.setattr(settings, "admin_identities", "")
    monkeypatch.setattr(settings, "auditor_emails", AUDITOR)
    monkeypatch.setattr(settings, "auditor_identities", "")
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr("test-secret-0123456789abcdef"))


@pytest.fixture
def store() -> Iterator[InMemoryAuditStore]:
    prior = app.dependency_overrides.get(get_audit_store)
    memory = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: memory
    try:
        yield memory
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior


class _CountLakebase:
    """Records the count statement; answers a fixed count."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.statements.append((sql, dict(params or {})))
        return {"event_count": 2}


@pytest.fixture
def lakebase() -> Iterator[_CountLakebase]:
    prior = app.dependency_overrides.get(get_lakebase_client)
    fake = _CountLakebase()
    app.dependency_overrides[get_lakebase_client] = lambda: fake
    try:
        yield fake
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior


def _ledger_read(memory: InMemoryAuditStore, actor: str, surface: str = "events_page") -> AuditEvent:
    return memory.write(
        actor=actor,
        action="audit_ledger.read",
        entity_type="audit_ledger",
        entity_id=surface,
        payload_json={"ledger_surface": surface, "has_cursor": False, "returned_row_count": 1},
        event_type="VIEW_AUDIT_LEDGER",
    )


def _view_leads(memory: InMemoryAuditStore, actor: str, index: int = 0) -> AuditEvent:
    return memory.write(
        actor=actor,
        action="view_leads",
        entity_type="lead_list",
        entity_id=f"queue-{index}",
        event_type="VIEW_LEADS",
    )


def _ids(response: Any) -> set[str]:
    return {item["event_id"] for item in response.json()["items"]}


# --- what the exclusion drops ----------------------------------------------------


def test_only_the_readers_own_ledger_reads_are_excluded(store: InMemoryAuditStore) -> None:
    mine = _ledger_read(store, ADMIN)
    theirs = _ledger_read(store, AUDITOR)
    my_other = _view_leads(store, ADMIN)
    approver = _view_leads(store, APPROVER, 1)

    excluded = client.get(f"{PAGE}?limit=50&exclude_own_ledger_reads=true", headers=ADMIN_HEADERS)
    assert excluded.status_code == 200, excluded.text
    assert _ids(excluded) == {theirs.event_id, my_other.event_id, approver.event_id}

    # The same reader with the flag off sees every row, the one just written
    # by the excluded read included.
    everything = client.get(f"{PAGE}?limit=50", headers=ADMIN_HEADERS)
    assert {mine.event_id, theirs.event_id, my_other.event_id, approver.event_id} <= _ids(everything)


def test_every_served_read_still_writes_its_ledger_row(store: InMemoryAuditStore) -> None:
    _view_leads(store, APPROVER)
    client.get(f"{PAGE}?limit=10&exclude_own_ledger_reads=true", headers=AUDITOR_HEADERS)

    rows = [row for row in store.list(limit=100) if row.event_type == "VIEW_AUDIT_LEDGER"]
    assert len(rows) == 1
    assert rows[0].actor == AUDITOR


def test_a_client_actor_param_cannot_redirect_the_exclusion(store: InMemoryAuditStore) -> None:
    auditor_read = _ledger_read(store, AUDITOR)
    _ledger_read(store, ADMIN)

    # ?actor= is the explorer's row filter, and an unknown parameter is
    # ignored: neither names whose reads are excluded. The admin's own
    # identity does, so the auditor's ledger read stays.
    response = client.get(
        f"{PAGE}?limit=50&exclude_own_ledger_reads=true&actor={AUDITOR}&exclude_ledger_reads_by={AUDITOR}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert _ids(response) == {auditor_read.event_id}


def test_the_count_binds_the_server_identity_into_the_shared_clause(lakebase: _CountLakebase, store: InMemoryAuditStore) -> None:
    response = client.get(
        f"/api/v1/audit/count?exclude_own_ledger_reads=true&actor={AUDITOR}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200, response.text
    [(sql, params)] = lakebase.statements
    assert EXCLUSION_CLAUSE in sql
    assert params["exclude_ledger_reads_by"] == ADMIN
    assert params["actor"] == AUDITOR
    # Off: no clause, no parameter.
    client.get("/api/v1/audit/count", headers=ADMIN_HEADERS)
    sql_off, params_off = lakebase.statements[-1]
    assert "IS DISTINCT FROM" not in sql_off
    assert "exclude_ledger_reads_by" not in params_off


def test_the_page_and_the_count_share_one_clause() -> None:
    page_client = _RecordingClient()
    LakebaseAuditStore(client=page_client).list(limit=10, exclude_ledger_reads_by=ADMIN)  # type: ignore[arg-type]
    [(page_sql, page_params)] = page_client.statements
    count_sql, count_params = audit_count_statement(exclude_ledger_reads_by=ADMIN)

    assert EXCLUSION_CLAUSE in page_sql
    assert EXCLUSION_CLAUSE in count_sql
    assert page_params["exclude_ledger_reads_by"] == count_params["exclude_ledger_reads_by"] == ADMIN
    clauses, params = audit_filter_clauses()
    assert clauses == [] and params == {}


class _RecordingClient:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.statements.append((sql, dict(params or {})))
        return []


# --- cursors and fingerprints -------------------------------------------------------


def test_a_cursor_never_crosses_the_flag(store: InMemoryAuditStore) -> None:
    for index in range(5):
        _view_leads(store, APPROVER, index)

    on = client.get(f"{PAGE}?limit=2&exclude_own_ledger_reads=true", headers=ADMIN_HEADERS).json()
    off = client.get(f"{PAGE}?limit=2", headers=ADMIN_HEADERS).json()
    assert on["next_cursor"] and off["next_cursor"]

    replay_off = client.get(f"{PAGE}?limit=2&cursor={on['next_cursor']}", headers=ADMIN_HEADERS)
    replay_on = client.get(
        f"{PAGE}?limit=2&exclude_own_ledger_reads=true&cursor={off['next_cursor']}", headers=ADMIN_HEADERS
    )
    same = client.get(f"{PAGE}?limit=2&exclude_own_ledger_reads=true&cursor={on['next_cursor']}", headers=ADMIN_HEADERS)

    assert replay_off.status_code == 422
    assert replay_on.status_code == 422
    assert same.status_code == 200


def test_the_default_cursor_and_fingerprint_match_the_base_tree(store: InMemoryAuditStore) -> None:
    for index in range(5):
        _view_leads(store, APPROVER, index)

    response = client.get(f"{PAGE}?limit=2&event_type=VIEW_LEADS", headers=ADMIN_HEADERS)

    assert response.json()["next_cursor"] == GOLDEN_CURSOR
    default_filters: dict[str, Any] = {
        "limit": 2,
        "actor": None,
        "action": None,
        "entity_id": None,
        "borrower_id": None,
        "subject_clip": None,
        "event_type": "VIEW_LEADS",
        "correlation_id": None,
        "since": None,
        "until": None,
        "event_id": None,
    }
    assert audit_filter_fingerprint(default_filters) == GOLDEN_FINGERPRINT
    assert audit_filter_fingerprint({**default_filters, "exclude_own_ledger_reads": True}) != GOLDEN_FINGERPRINT


# --- the in-memory store matches the SQL ------------------------------------------------


def _sql_keeps(event_type: str | None, actor: str | None, reader: str) -> bool:
    """The clause's three-valued logic: IS DISTINCT FROM never yields NULL."""

    return event_type != "VIEW_AUDIT_LEDGER" or actor != reader


@pytest.mark.parametrize("reader", [ADMIN, AUDITOR])
def test_the_in_memory_store_matches_the_sql_predicate(reader: str) -> None:
    memory = InMemoryAuditStore()
    written = [
        _ledger_read(memory, ADMIN),
        _ledger_read(memory, AUDITOR),
        _view_leads(memory, ADMIN),
        _view_leads(memory, AUDITOR, 1),
    ]

    kept = {event.event_id for event in memory.list(limit=100, exclude_ledger_reads_by=reader)}

    expected = {event.event_id for event in written if _sql_keeps(event.event_type, event.actor, reader)}
    assert kept == expected
    assert len(kept) == 3
