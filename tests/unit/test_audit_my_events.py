"""Actor-scoped audit recovery feed contract."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.schemas.audit import AuditEvent
from backend.services.audit_store import get_audit_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)
ALICE_HEADERS = {
    "X-Forwarded-Email": "alice.operator@example.com",
    "X-Forwarded-Groups": "",
}
BOB_HEADERS = {
    "X-Forwarded-Email": "bob.operator@example.com",
    "X-Forwarded-Groups": "",
}


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


def _write(
    store: InMemoryAuditStore,
    *,
    actor: str,
    event_type: str,
    borrower_id: str = "B-AAAAAAAAAAAA1",
) -> None:
    store.write(
        actor=actor,
        action="activity.record",
        entity_type="borrower",
        entity_id=borrower_id,
        event_type=event_type,
    )


def test_non_admin_reads_only_their_actor_scoped_activity(
    audit_store: InMemoryAuditStore,
) -> None:
    _write(audit_store, actor=ALICE_HEADERS["X-Forwarded-Email"], event_type="SAVE_LEAD")
    _write(audit_store, actor=BOB_HEADERS["X-Forwarded-Email"], event_type="LEAD_ASSIGN")

    response = client.get("/api/audit/my-events", headers=ALICE_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert [item["event_type"] for item in response.json()["items"]] == ["SAVE_LEAD"]
    assert client.get("/api/v1/audit/events", headers=ALICE_HEADERS).status_code == 403


def test_my_events_requires_an_edge_authenticated_identity(
    audit_store: InMemoryAuditStore,
) -> None:
    del audit_store
    response = client.get(
        "/api/v1/audit/my-events",
        headers={"X-Forwarded-Groups": "", "X-Forwarded-Email": ""},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "audit identity required"


def test_my_events_fails_closed_when_forwarded_identity_is_untrusted(
    audit_store: InMemoryAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audit_store
    monkeypatch.setattr(settings, "trust_forwarded_headers", False)

    response = client.get("/api/v1/audit/my-events", headers=ALICE_HEADERS)

    assert response.status_code == 401
    assert response.json()["detail"] == "audit identity required"


def test_my_events_rejects_arbitrary_actor_filters(
    audit_store: InMemoryAuditStore,
) -> None:
    _write(audit_store, actor=ALICE_HEADERS["X-Forwarded-Email"], event_type="SAVE_LEAD")

    response = client.get(
        "/api/v1/audit/my-events?actor=bob.operator%40example.com",
        headers=ALICE_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "actor filter is not supported"


def test_my_events_omits_actor_payload_and_unsafe_historical_identifiers(
    audit_store: InMemoryAuditStore,
) -> None:
    audit_store._events.append(  # noqa: SLF001 - security regression fixture
        AuditEvent(
            event_id="evt-historical-unsafe",
            actor=ALICE_HEADERS["X-Forwarded-Email"],
            action="outreach.approve",
            entity_type="borrower",
            entity_id="borrower.person@example.com",
            payload_json={"borrower_email": "borrower.person@example.com"},
            evidence_ids=["private-proof-reference"],
            created_at="2026-07-14T12:00:00+00:00",
            event_type="OUTREACH_APPROVE",
            correlation_id="private-correlation-reference",
            audit_sequence=1,
        )
    )

    response = client.get("/api/v1/audit/my-events", headers=ALICE_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {
            "event_type": "OUTREACH_APPROVE",
            "entity_type": "borrower",
            "subject_id": None,
            "created_at": "2026-07-14T12:00:00+00:00",
        }
    ]
    serialized = response.text
    assert "alice.operator@example.com" not in serialized
    assert "borrower.person@example.com" not in serialized
    assert "payload_json" not in serialized
    assert "evidence_ids" not in serialized
    assert "correlation_id" not in serialized


def test_my_events_exposes_only_a_validated_masked_borrower_id_from_metadata(
    audit_store: InMemoryAuditStore,
) -> None:
    audit_store._events.append(  # noqa: SLF001 - security regression fixture
        AuditEvent(
            event_id="evt-approval",
            actor=ALICE_HEADERS["X-Forwarded-Email"],
            action="outreach.approve",
            entity_type="approval",
            entity_id="11111111-1111-4111-8111-111111111111",
            payload_json={
                "borrower_id": "B-AAAAAAAAAAAA1",
                "borrower_email": "private.borrower@example.com",
            },
            evidence_ids=["private-proof-reference"],
            created_at="2026-07-14T12:00:00+00:00",
            event_type="OUTREACH_APPROVE",
            audit_sequence=1,
        )
    )

    response = client.get("/api/v1/audit/my-events", headers=ALICE_HEADERS)

    assert response.status_code == 200
    assert response.json()["items"][0]["subject_id"] == "B-AAAAAAAAAAAA1"
    assert "private.borrower@example.com" not in response.text


def test_my_events_cursor_is_snapshot_stable_signed_and_actor_bound(
    audit_store: InMemoryAuditStore,
) -> None:
    actor = ALICE_HEADERS["X-Forwarded-Email"]
    _write(audit_store, actor=actor, event_type="ACTIVITY_ONE")
    _write(audit_store, actor=actor, event_type="ACTIVITY_TWO")
    _write(audit_store, actor=actor, event_type="ACTIVITY_THREE")

    first = client.get("/api/v1/audit/my-events?limit=2", headers=ALICE_HEADERS)
    assert first.status_code == 200
    first_body = first.json()
    assert [item["event_type"] for item in first_body["items"]] == [
        "ACTIVITY_THREE",
        "ACTIVITY_TWO",
    ]
    cursor = first_body["next_cursor"]
    assert cursor

    _write(audit_store, actor=actor, event_type="ACTIVITY_CONCURRENT")
    second = client.get(
        "/api/v1/audit/my-events",
        params={"limit": 2, "cursor": cursor},
        headers=ALICE_HEADERS,
    )
    assert second.status_code == 200
    assert [item["event_type"] for item in second.json()["items"]] == ["ACTIVITY_ONE"]

    encoded_payload, signature = cursor.split(".")
    replacement = ("A" if encoded_payload[0] != "A" else "B") + encoded_payload[1:]
    tampered = f"{replacement}.{signature}"
    assert (
        client.get(
            "/api/v1/audit/my-events",
            params={"limit": 2, "cursor": tampered},
            headers=ALICE_HEADERS,
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/audit/my-events",
            params={"limit": 2, "cursor": cursor},
            headers=BOB_HEADERS,
        ).status_code
        == 422
    )
