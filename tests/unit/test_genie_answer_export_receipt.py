"""Audited Genie answer CSV export receipt (audit 2026-09-21 genie-06, slice 2).

The browser downloads a Genie answer's rows as CSV only after
``POST /api/v1/genie/export-receipt`` answers. These tests pin the ledger
contract at the HTTP layer through ``TestClient``:

* exactly one ``GENIE_ANSWER_EXPORT`` row per receipt, whose metadata key set
  is exactly the reviewed allowlist (ids, counts and two digests; no free
  text), attributed to the edge-forwarded actor;
* a declaration carrying any extra field (question text, cell values) is
  refused by the schema before anything is read or written;
* ownership is checked FIRST and fails closed: another actor's real message,
  a withheld source or an unknown message is a 404 with a constant detail and
  zero writes (cross-actor forgery is refused);
* counts that cannot describe the owned answer are a 422 with zero writes;
* Lakebase down is a 503 with the safe detail and zero writes;
* an edge-authenticated actor is required (401 otherwise);
* the client cannot self-report the event through ``POST /api/audit/event``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.genie_feedback_routes import (
    GENIE_EXPORT_COUNT_MISMATCH_DETAIL,
    GENIE_EXPORT_NOT_FOUND_DETAIL,
)
from backend.config.settings import settings
from backend.main import app
from backend.services.audit_event_types import is_server_owned_audit_event_type
from backend.services.audit_store import get_audit_store
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_answer_export_receipt import GENIE_EXPORT_OWNERSHIP_SQL
from backend.services.lakebase import LakebaseError, get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[2]
GENIE_EXPORT_CLIENT = ROOT / "frontend" / "src" / "lib" / "apiClients" / "genieExport.ts"

ACTOR = "analyst@summit-mortgage.example"
OTHER_ACTOR = "approver@summit-mortgage.example"
CONVERSATION = "01f13d4968af1b249dc388fd5b18b195"
MESSAGE = "01f13d4a0c3b1f2e9a7d5c6b4e3f2a10"
EXPECTED_METADATA_KEYS = {
    "conversation_id",
    "message_id",
    "exported_row_count",
    "row_count",
    "csv_sha256",
    "columns_sha256",
}
NON_PERSISTABLE = ("degraded", "policy_blocked", "refused", "data_gap", "out_of_footprint")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _headers(actor: str = ACTOR) -> dict[str, str]:
    return {"X-Forwarded-Email": actor, "X-Forwarded-Groups": ""}


def _declaration(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "conversation_id": CONVERSATION,
        "message_id": MESSAGE,
        "scope": "answer",
        "row_count": 120,
        "answer_row_count": 120,
        "csv_sha256": _sha256("# source=genie\nstate,borrowers\nIL,1204\n"),
        "columns_sha256": _sha256('["state","borrowers"]'),
    }
    body.update(overrides)
    return body


@dataclass(frozen=True)
class _StoredMessage:
    actor: str
    conversation_id: str
    message_id: str
    source: str
    row_count: int


class _FakeLakebase:
    """Just the ownership lookup: rows of mip_app.genie_messages joined to
    their session, filtered the way GENIE_EXPORT_OWNERSHIP_SQL filters."""

    def __init__(self, messages: list[_StoredMessage], *, down: bool = False) -> None:
        self.messages = messages
        self.down = down
        self.queries: list[dict[str, Any]] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        assert sql == GENIE_EXPORT_OWNERSHIP_SQL, "unexpected SQL"
        params = params or {}
        self.queries.append(params)
        if self.down:
            raise LakebaseError("lakebase unavailable")
        for message in self.messages:
            if (
                message.actor == params["actor_email"]
                and message.conversation_id == params["conversation_id"]
                and message.message_id == params["message_id"]
                and message.source not in NON_PERSISTABLE
            ):
                return {"row_count": message.row_count}
        return None


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


def _install_lakebase(lakebase: _FakeLakebase) -> None:
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase


@pytest.fixture
def lakebase() -> Iterator[_FakeLakebase]:
    prior = app.dependency_overrides.get(get_lakebase_client)
    fake = _FakeLakebase([_StoredMessage(ACTOR, CONVERSATION, MESSAGE, "genie", 120)])
    _install_lakebase(fake)
    try:
        yield fake
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior


def test_receipt_writes_exactly_one_row_with_only_the_reviewed_metadata(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase
) -> None:
    response = client.post("/api/v1/genie/export-receipt", json=_declaration(), headers=_headers())

    assert response.status_code == 200, response.text
    rows = audit_store.list(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "GENIE_ANSWER_EXPORT"
    assert row.action == "genie.answer_export"
    assert row.entity_type == "genie_message"
    assert row.entity_id == MESSAGE
    assert row.actor == ACTOR
    assert set(row.payload_json) == EXPECTED_METADATA_KEYS
    assert row.payload_json["exported_row_count"] == 120
    assert row.payload_json["row_count"] == 120
    body = response.json()
    assert body["audit_event_id"] == row.event_id
    assert body["event_type"] == "GENIE_ANSWER_EXPORT"
    assert body["actor"] == ACTOR
    assert body["scope"] == "answer"
    assert body["csv_sha256"] == row.payload_json["csv_sha256"]
    assert body["columns_sha256"] == row.payload_json["columns_sha256"]
    # The ownership lookup was keyed on the forwarded actor, not a body field.
    assert lakebase.queries == [
        {"actor_email": ACTOR, "conversation_id": CONVERSATION, "message_id": MESSAGE}
    ]


def test_a_section_export_is_recorded_as_a_section_and_needs_no_answer_total_match(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase
) -> None:
    response = client.post(
        "/api/v1/genie/export-receipt",
        json=_declaration(scope="section", row_count=7, answer_row_count=7),
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert [row.action for row in audit_store.list(limit=10)] == ["genie.section_export"]


def test_a_trimmed_history_replay_exports_the_rows_it_holds(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase
) -> None:
    response = client.post(
        "/api/v1/genie/export-receipt",
        json=_declaration(row_count=50, answer_row_count=120),
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    row = audit_store.list(limit=10)[0]
    assert row.payload_json["exported_row_count"] == 50
    assert row.payload_json["row_count"] == 120


@pytest.mark.parametrize(
    "extra",
    [
        {"question": "Which borrowers in Chicago should we call?"},
        {"rows": [{"state": "IL"}]},
        {"actor": "ceo@summit-mortgage.example"},
    ],
)
def test_extra_fields_are_refused_before_anything_is_read_or_written(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase, extra: dict[str, object]
) -> None:
    response = client.post(
        "/api/v1/genie/export-receipt", json=_declaration(**extra), headers=_headers()
    )

    assert response.status_code == 422
    assert audit_store.list(limit=10) == []
    assert lakebase.queries == []


def test_another_actors_real_message_is_not_found_and_writes_nothing(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase
) -> None:
    response = client.post(
        "/api/v1/genie/export-receipt", json=_declaration(), headers=_headers(OTHER_ACTOR)
    )

    assert response.status_code == 404
    assert response.json()["detail"] == GENIE_EXPORT_NOT_FOUND_DETAIL
    assert audit_store.list(limit=10) == []


@pytest.mark.parametrize("source", NON_PERSISTABLE)
def test_a_withheld_source_is_not_found_and_writes_nothing(
    audit_store: InMemoryAuditStore, source: str
) -> None:
    prior = app.dependency_overrides.get(get_lakebase_client)
    _install_lakebase(_FakeLakebase([_StoredMessage(ACTOR, CONVERSATION, MESSAGE, source, 120)]))
    try:
        response = client.post(
            "/api/v1/genie/export-receipt", json=_declaration(), headers=_headers()
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior

    assert response.status_code == 404
    assert response.json()["detail"] == GENIE_EXPORT_NOT_FOUND_DETAIL
    assert audit_store.list(limit=10) == []


def test_the_ownership_sql_joins_the_session_and_excludes_every_withheld_source() -> None:
    sql = " ".join(GENIE_EXPORT_OWNERSHIP_SQL.split())
    assert "FROM mip_app.genie_messages AS messages JOIN mip_app.genie_sessions AS sessions" in sql
    assert "sessions.actor_email = messages.actor_email" in sql
    assert "messages.actor_email = %(actor_email)s" in sql
    assert "messages.message_id = %(message_id)s" in sql
    for source in NON_PERSISTABLE:
        assert f"'{source}'" in sql


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"row_count": 121, "answer_row_count": 120}, "more rows than the answer reports"),
        ({"row_count": 50, "answer_row_count": 50}, "answer total differs from the recorded answer"),
        ({"row_count": 120, "answer_row_count": None}, "answer total missing for a recorded answer"),
    ],
)
def test_counts_that_cannot_describe_the_owned_answer_are_refused(
    audit_store: InMemoryAuditStore,
    lakebase: _FakeLakebase,
    overrides: dict[str, object],
    reason: str,
) -> None:
    response = client.post(
        "/api/v1/genie/export-receipt", json=_declaration(**overrides), headers=_headers()
    )

    assert response.status_code == 422, reason
    assert response.json()["detail"] == GENIE_EXPORT_COUNT_MISMATCH_DETAIL
    assert audit_store.list(limit=10) == []


def test_lakebase_down_is_a_503_with_the_safe_detail_and_no_write(
    audit_store: InMemoryAuditStore,
) -> None:
    prior = app.dependency_overrides.get(get_lakebase_client)
    _install_lakebase(_FakeLakebase([], down=True))
    try:
        response = client.post(
            "/api/v1/genie/export-receipt", json=_declaration(), headers=_headers()
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior

    assert response.status_code == 503
    assert response.json()["detail"] == safe_dependency_detail("lakebase")
    assert audit_store.list(limit=10) == []


def test_malformed_digests_and_ids_are_refused_before_the_lookup(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase
) -> None:
    for overrides in (
        {"csv_sha256": "not-a-digest"},
        {"columns_sha256": "A" * 64},
        {"message_id": "msg with spaces"},
        {"row_count": 5001},
        {"row_count": 0},
        {"scope": "everything"},
    ):
        response = client.post(
            "/api/v1/genie/export-receipt", json=_declaration(**overrides), headers=_headers()
        )
        assert response.status_code == 422, overrides
    assert audit_store.list(limit=10) == []
    assert lakebase.queries == []


def test_receipt_requires_an_authenticated_actor(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase
) -> None:
    response = client.post(
        "/api/v1/genie/export-receipt",
        json=_declaration(),
        headers={"X-Forwarded-Email": "", "X-Forwarded-User": "", "X-Forwarded-Groups": ""},
    )

    assert response.status_code == 401
    assert audit_store.list(limit=10) == []


def test_receipt_requires_a_json_body(audit_store: InMemoryAuditStore, lakebase: _FakeLakebase) -> None:
    response = client.post(
        "/api/v1/genie/export-receipt",
        content="conversation_id=x",
        headers={**_headers(), "Content-Type": "text/plain"},
    )

    assert response.status_code == 415
    assert audit_store.list(limit=10) == []


def test_client_cannot_self_report_a_genie_answer_export(audit_store: InMemoryAuditStore) -> None:
    assert is_server_owned_audit_event_type("GENIE_ANSWER_EXPORT")
    response = client.post(
        "/api/v1/audit/event",
        json={
            "actor": ACTOR,
            "action": "genie.answer_export",
            "entity_type": "genie_message",
            "entity_id": MESSAGE,
            "event_type": "GENIE_ANSWER_EXPORT",
        },
        headers={"X-Forwarded-Email": ACTOR},
    )

    assert response.status_code == 400
    assert audit_store.list(limit=10) == []


def test_browser_keys_its_not_found_copy_on_the_same_constant_detail() -> None:
    # The browser says "not in your Genie history" only for this detail.
    source = GENIE_EXPORT_CLIENT.read_text(encoding="utf-8")
    match = re.search(r"export const GENIE_EXPORT_NOT_FOUND_DETAIL = '([^']+)';", source)
    assert match is not None, "GENIE_EXPORT_NOT_FOUND_DETAIL not found"
    assert match.group(1) == GENIE_EXPORT_NOT_FOUND_DETAIL


def test_unversioned_alias_is_deprecated_and_writes_the_same_single_row(
    audit_store: InMemoryAuditStore, lakebase: _FakeLakebase
) -> None:
    response = client.post("/api/genie/export-receipt", json=_declaration(), headers=_headers())

    assert response.status_code == 200, response.text
    assert [row.event_type for row in audit_store.list(limit=10)] == ["GENIE_ANSWER_EXPORT"]
    deprecated = app.openapi()["paths"]["/api/genie/export-receipt"]["post"].get("deprecated")
    assert deprecated is True
