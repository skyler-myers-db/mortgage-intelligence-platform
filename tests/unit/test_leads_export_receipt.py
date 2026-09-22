"""Audited Lead Queue CSV export receipt (audit tables-08, wave 1a).

The download waits for ``POST /api/v1/leads/export-receipt``. These tests pin
the ledger contract at the HTTP layer through ``TestClient``:

* exactly one ``LEAD_EXPORT`` row per receipt, carrying the filter
  fingerprint, the exported row count, both digests and the id list;
* a declaration whose id digest (or count) does not describe the ids it
  sends is refused with 422 and writes nothing;
* an edge-authenticated actor is required (401 otherwise) and the row is
  attributed to the forwarded identity, never a body field;
* the client cannot self-report the event through the admin-only
  ``POST /api/audit/event``;
* the write shares the mutation rate budget with every other write.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.audit_event_types import is_server_owned_audit_event_type
from backend.services.audit_store import get_audit_store
from backend.services.backpressure import BackpressureController
from backend.services.growth_agent_handoff import handoff_filters_fingerprint
from backend.services.lead_export_receipt import (
    borrower_ids_digest,
    lead_export_filter_fingerprint,
)
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

ACTOR = "approver@summit-mortgage.example"
ACTOR_HEADERS = {"X-Forwarded-Email": ACTOR, "X-Forwarded-Groups": ""}
IDS = ["B-AAAAAAAAAAAA1", "B-AAAAAAAAAAAA2"]
FILTERS = {"states": "IL,TX", "segment_codes": "itm,equity", "segment_mode": "any"}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _declaration(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "scope": "selected",
        "row_count": len(IDS),
        "csv_sha256": _sha256("borrower_id,city\nB-AAAAAAAAAAAA1,Chicago\n"),
        "borrower_ids": list(IDS),
        "borrower_ids_sha256": _sha256(json.dumps(IDS, separators=(",", ":"))),
        "filters": dict(FILTERS),
    }
    body.update(overrides)
    return body


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


def test_receipt_writes_exactly_one_lead_export_row(audit_store: InMemoryAuditStore) -> None:
    response = client.post(
        "/api/v1/leads/export-receipt", json=_declaration(), headers=ACTOR_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    rows = audit_store.list(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "LEAD_EXPORT"
    assert row.action == "lead_queue.export"
    assert row.entity_type == "lead_queue"
    assert row.actor == ACTOR
    assert body["audit_event_id"] == row.event_id
    assert body["event_type"] == "LEAD_EXPORT"
    assert body["actor"] == ACTOR
    assert body["row_count"] == 2
    assert body["scope"] == "selected"

    expected_fingerprint = handoff_filters_fingerprint(dict(sorted(FILTERS.items())))
    assert body["filter_fingerprint"] == expected_fingerprint
    assert row.payload_json["filter_fingerprint"] == expected_fingerprint
    assert row.payload_json["exported_row_count"] == 2
    assert row.payload_json["export_scope"] == "selected"
    assert row.payload_json["csv_sha256"] == body["csv_sha256"]
    assert row.payload_json["borrower_ids_sha256"] == borrower_ids_digest(IDS)
    assert row.payload_json["borrower_ids"] == IDS
    # Only the fingerprint reaches the ledger; the raw filter values do not.
    assert "filters" not in row.payload_json
    assert "states" not in row.payload_json


def test_receipt_rejects_borrower_id_digest_mismatch_with_422(
    audit_store: InMemoryAuditStore,
) -> None:
    tampered = _declaration(borrower_ids=[IDS[0], "B-AAAAAAAAAAAA9"])

    response = client.post("/api/v1/leads/export-receipt", json=tampered, headers=ACTOR_HEADERS)

    assert response.status_code == 422
    assert response.json()["detail"] == "export declaration does not match the borrower id list"
    assert audit_store.list(limit=10) == []


def test_receipt_rejects_row_count_that_disagrees_with_the_ids(
    audit_store: InMemoryAuditStore,
) -> None:
    response = client.post(
        "/api/v1/leads/export-receipt", json=_declaration(row_count=3), headers=ACTOR_HEADERS
    )

    assert response.status_code == 422
    assert audit_store.list(limit=10) == []


def test_receipt_rejects_a_malformed_digest_before_touching_the_store(
    audit_store: InMemoryAuditStore,
) -> None:
    response = client.post(
        "/api/v1/leads/export-receipt",
        json=_declaration(csv_sha256="not-a-digest"),
        headers=ACTOR_HEADERS,
    )

    assert response.status_code == 422
    assert audit_store.list(limit=10) == []


def test_receipt_requires_an_authenticated_actor(audit_store: InMemoryAuditStore) -> None:
    response = client.post(
        "/api/v1/leads/export-receipt",
        json=_declaration(),
        headers={"X-Forwarded-Email": "", "X-Forwarded-User": "", "X-Forwarded-Groups": ""},
    )

    assert response.status_code == 401
    assert audit_store.list(limit=10) == []


def test_receipt_actor_comes_from_the_edge_header_not_the_body(
    audit_store: InMemoryAuditStore,
) -> None:
    response = client.post(
        "/api/v1/leads/export-receipt",
        json=_declaration(actor="ceo@summit-mortgage.example"),
        headers=ACTOR_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert audit_store.list(limit=10)[0].actor == ACTOR


def test_client_cannot_self_report_a_lead_export_through_the_audit_route(
    audit_store: InMemoryAuditStore,
) -> None:
    assert is_server_owned_audit_event_type("LEAD_EXPORT")
    response = client.post(
        "/api/v1/audit/event",
        json={
            "actor": ACTOR,
            "action": "lead_queue.export",
            "entity_type": "lead_queue",
            "entity_id": "export-self-report",
            "event_type": "LEAD_EXPORT",
        },
        headers={"X-Forwarded-Email": ACTOR},
    )

    assert response.status_code == 400
    assert audit_store.list(limit=10) == []


def test_receipt_write_is_rate_limited_as_a_mutation() -> None:
    budget = BackpressureController().classify("POST", "/api/v1/leads/export-receipt")

    assert budget is not None
    assert budget.scope == "mutation"
    assert budget.dependency == "lakebase"
    assert budget.requests_per_minute == settings.mip_rate_limit_mutation_per_minute


def test_filter_fingerprint_is_key_order_independent_and_matches_the_handoff_form() -> None:
    forward = lead_export_filter_fingerprint({"states": "IL", "segment": "itm"})
    reversed_order = lead_export_filter_fingerprint({"segment": "itm", "states": "IL"})

    assert forward == reversed_order
    assert forward == handoff_filters_fingerprint({"segment": "itm", "states": "IL"})
    assert lead_export_filter_fingerprint({}) == handoff_filters_fingerprint({})
    assert forward != lead_export_filter_fingerprint({"states": "TX", "segment": "itm"})


def test_borrower_ids_digest_matches_the_browser_canonical_form() -> None:
    # The browser hashes `JSON.stringify(ids)`: compact, ordered, ASCII.
    assert borrower_ids_digest(IDS) == _sha256('["B-AAAAAAAAAAAA1","B-AAAAAAAAAAAA2"]')
    assert borrower_ids_digest(list(reversed(IDS))) != borrower_ids_digest(IDS)
