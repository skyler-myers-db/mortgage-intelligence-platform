"""``GET /api/audit/events/page?event_id=`` -- the audit explorer deep link.

Audit flow-04 / tables-10 (2026-09-21): every ``audit_event_id`` becomes a
link the explorer opens to. The store already filtered on actor, event type,
correlation id and a time window; ``event_id`` was the one thing missing, so a
link could only scan pages hoping to find its row. This pins the filter at
the HTTP layer, the cursor binding, and the PII refusal on the parameter.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.audit_store import get_audit_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)
ADMIN_HEADERS = {"X-Forwarded-Email": "admin@summit-mortgage.example"}


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


def _write(store: InMemoryAuditStore, borrower_id: str) -> str:
    return store.write(
        actor="approver@summit-mortgage.example",
        action="outreach.approve",
        entity_type="borrower",
        entity_id=borrower_id,
        event_type="OUTREACH_APPROVE",
    ).event_id


def test_event_id_filter_returns_only_the_linked_event(audit_store: InMemoryAuditStore) -> None:
    _write(audit_store, "B-AAAAAAAAAAAA1")
    target = _write(audit_store, "B-AAAAAAAAAAAA2")
    _write(audit_store, "B-AAAAAAAAAAAA3")

    response = client.get(
        "/api/v1/audit/events/page", params={"event_id": target}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["event_id"] for row in body["items"]] == [target]
    assert body["next_cursor"] is None


def test_unknown_event_id_is_an_empty_page_not_an_error(audit_store: InMemoryAuditStore) -> None:
    _write(audit_store, "B-AAAAAAAAAAAA1")

    response = client.get(
        "/api/v1/audit/events/page",
        params={"event_id": "evt-000000000000"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_pii_shaped_event_id_is_refused(audit_store: InMemoryAuditStore) -> None:
    response = client.get(
        "/api/v1/audit/events/page",
        params={"event_id": "someone@example.com"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid event_id"


def test_event_id_is_part_of_the_cursor_filter_binding(audit_store: InMemoryAuditStore) -> None:
    ids = [_write(audit_store, f"B-AAAAAAAAAAAA{index}") for index in range(3)]

    page = client.get(
        "/api/v1/audit/events/page", params={"limit": 1}, headers=ADMIN_HEADERS
    ).json()
    assert page["next_cursor"] is not None

    # A cursor minted for the unfiltered page cannot be replayed under a
    # different filter set; the fingerprint now covers event_id too.
    rebound = client.get(
        "/api/v1/audit/events/page",
        params={"limit": 1, "cursor": page["next_cursor"], "event_id": ids[0]},
        headers=ADMIN_HEADERS,
    )
    assert rebound.status_code == 422
    assert rebound.json()["detail"] == "invalid audit cursor"
