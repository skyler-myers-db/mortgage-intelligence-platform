from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_store import AuditMetadataValueViolation, get_audit_store
from backend.services.genie_sales_ops import sales_ops_genie_response
from backend.services.lakebase import get_lakebase_client
from backend.services.repositories import get_borrower_repository
from backend.services.sales_state import get_sales_state_store
from tests.fixtures import mock_population as mock_data
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})


def _borrower_id() -> str:
    return mock_data.BORROWERS[0].borrower_id


def _approve_for_sales(borrower_id: str) -> None:
    draft = client.post(
        "/api/outreach/draft",
        json={"borrower_id": borrower_id, "channel": "email"},
    )
    assert draft.status_code == 200
    approved = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": borrower_id,
            "offer_code": "refi_plus_heloc",
            "channel": "email",
            "draft_body": draft.json()["body"],
            "request_id": str(uuid4()),
        },
    )
    assert approved.status_code == 200


def _sales_ops_response(question: str):
    lakebase = app.dependency_overrides[get_lakebase_client]()
    borrowers = app.dependency_overrides[get_borrower_repository]()
    return sales_ops_genie_response(
        lakebase,
        borrowers,
        actor="skyler@entrada.ai",
        question=question,
        conversation_id=None,
    )


def test_leads_honor_approval_status_filter() -> None:
    pending = client.get("/api/leads?approval_status=pending&limit=5")
    assert pending.status_code == 200
    assert len(pending.json()) > 0

    approved = client.get("/api/leads?approval_status=approved&limit=5")
    assert approved.status_code == 200
    assert approved.json() == []
    assert approved.headers["X-Total-Matching"] == "0"

    invalid = client.get("/api/leads?approval_status=worked")
    assert invalid.status_code == 422


def test_sales_team_assignment_and_assignee_filter_round_trip() -> None:
    team = client.get("/api/sales/team")
    assert team.status_code == 200
    assert any(member["email"] == "lo01@summit.example" for member in team.json())

    borrower_id = _borrower_id()
    _approve_for_sales(borrower_id)
    assign = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": "lo01@summit.example", "strategy": "manual"},
    )
    assert assign.status_code == 200
    body = assign.json()
    assert body["assignment"]["borrower_id"] == borrower_id
    assert body["assignment"]["assigned_to_email"] == "lo01@summit.example"
    assert body["audit_event_id"]

    filtered = client.get("/api/leads?assigned_to=lo01@summit.example&limit=5")
    assert filtered.status_code == 200
    rows = filtered.json()
    assert any(row["borrower_id"] == borrower_id for row in rows)
    assert rows[0]["assigned_to_email"] == "lo01@summit.example"

    assignment_read = client.get(f"/api/leads/{borrower_id}/assignment")
    assert assignment_read.status_code == 200
    assert assignment_read.json()["borrower_id"] == borrower_id
    assert assignment_read.json()["assigned_to_email"] == "lo01@summit.example"


def test_disposition_requires_callback_time_and_updates_lifecycle() -> None:
    borrower_id = _borrower_id()
    _approve_for_sales(borrower_id)
    client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": "lo01@summit.example", "strategy": "manual"},
    )

    missing_callback = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": "lo01@summit.example", "outcome": "callback_scheduled"},
    )
    assert missing_callback.status_code == 422

    callback_at = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    logged = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={
            "lo_email": "lo01@summit.example",
            "outcome": "callback_scheduled",
            "callback_at": callback_at,
            "notes": "Callback requested after rate review.",
        },
    )
    assert logged.status_code == 200
    assert logged.json()["disposition"]["outcome"] == "callback_scheduled"
    assert logged.json()["audit_event_id"]

    lifecycle = client.get(f"/api/borrowers/{borrower_id}/lifecycle")
    assert lifecycle.status_code == 200
    body = lifecycle.json()
    assert body["assignment"]["assigned_to_email"] == "lo01@summit.example"
    assert body["latest_disposition"]["outcome"] == "callback_scheduled"


def test_sales_standup_aging_and_conversion_surfaces() -> None:
    borrower_id = mock_data.BORROWERS[1].borrower_id
    _approve_for_sales(borrower_id)
    client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": "lo02@summit.example", "strategy": "manual"},
    )
    logged = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": "lo02@summit.example", "outcome": "application_started"},
    )
    assert logged.status_code == 200

    today = datetime.now(UTC).date().isoformat()
    standup = client.get(f"/api/sales/standup?date={today}")
    assert standup.status_code == 200
    assert standup.json()["applications_started"] >= 1

    conversion = client.get(f"/api/sales/conversion?from={today}&to={today}&groupBy=lo")
    assert conversion.status_code == 200
    rows = conversion.json()["rows"]
    assert any(row["group_key"] == "lo02@summit.example" for row in rows)

    invalid = client.get(f"/api/sales/conversion?from={today}&to=2020-01-01&groupBy=lo")
    assert invalid.status_code == 422

    aging = client.get("/api/sales/aging?older_than_days=7")
    assert aging.status_code == 200
    assert isinstance(aging.json(), list)


def test_sales_distribute_requires_approved_borrowers() -> None:
    borrower_id = mock_data.BORROWERS[2].borrower_id

    response = client.post(
        "/api/sales/distribute",
        json={
            "borrower_ids": [borrower_id],
            "lo_emails": ["lo01@summit.example"],
            "limit": 1,
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "lead must be approved before assignment"


def test_sales_aging_omits_approval_rows_without_live_borrower() -> None:
    live_borrower_id = mock_data.BORROWERS[2].borrower_id
    old = datetime.now(UTC) - timedelta(days=14)

    class _Store:
        def require_manager_actor(self, actor: str) -> object:
            return object()

        def visible_lo_emails(self, *, actor: str) -> None:
            return None

        def aging(self, *, older_than_days: int, limit: int = 50) -> list[dict[str, object]]:
            return [
                {
                    "borrower_id": "B-TEST-ORPHAN",
                    "approval_status": "approved",
                    "approved_at": old,
                    "age_days": 14,
                    "outreach_status": "queued",
                    "outreach_at": None,
                    "assigned_to_email": None,
                    "latest_disposition_outcome": None,
                    "latest_disposition_at": None,
                },
                {
                    "borrower_id": live_borrower_id,
                    "approval_status": "approved",
                    "approved_at": old,
                    "age_days": 14,
                    "outreach_status": "queued",
                    "outreach_at": None,
                    "assigned_to_email": None,
                    "latest_disposition_outcome": None,
                    "latest_disposition_at": None,
                },
            ]

    class _Borrowers:
        def get(self, borrower_id: str) -> object | None:
            return object() if borrower_id == live_borrower_id else None

    previous_store = app.dependency_overrides.get(get_sales_state_store)
    previous_borrowers = app.dependency_overrides.get(get_borrower_repository)
    app.dependency_overrides[get_sales_state_store] = lambda: _Store()
    app.dependency_overrides[get_borrower_repository] = lambda: _Borrowers()
    try:
        response = client.get("/api/sales/aging?older_than_days=7")
    finally:
        if previous_store is None:
            app.dependency_overrides.pop(get_sales_state_store, None)
        else:
            app.dependency_overrides[get_sales_state_store] = previous_store
        if previous_borrowers is None:
            app.dependency_overrides.pop(get_borrower_repository, None)
        else:
            app.dependency_overrides[get_borrower_repository] = previous_borrowers

    assert response.status_code == 200
    borrower_ids = {row["borrower_id"] for row in response.json()}
    assert live_borrower_id in borrower_ids
    assert "B-TEST-ORPHAN" not in borrower_ids


def test_genie_routes_sales_manager_lo_conversion_to_sales_ops_adapter() -> None:
    previous_lakebase = app.dependency_overrides.get(get_lakebase_client)
    previous_audit = app.dependency_overrides.get(get_audit_store)
    assert previous_lakebase is not None
    isolated_lakebase = type(previous_lakebase())()
    isolated_audit = InMemoryAuditStore()
    app.dependency_overrides[get_lakebase_client] = lambda: isolated_lakebase
    app.dependency_overrides[get_audit_store] = lambda: isolated_audit
    isolated_client = TestClient(app)
    isolated_client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})
    borrower_id = mock_data.BORROWERS[2].borrower_id
    try:
        draft = isolated_client.post(
            "/api/outreach/draft",
            json={"borrower_id": borrower_id, "channel": "email"},
        )
        assert draft.status_code == 200
        approved = isolated_client.post(
            "/api/outreach/approve",
            json={
                "borrower_id": borrower_id,
                "offer_code": "refi_plus_heloc",
                "channel": "email",
                "draft_body": draft.json()["body"],
                "request_id": str(uuid4()),
            },
        )
        assert approved.status_code == 200
        assign = isolated_client.post(
            f"/api/leads/{borrower_id}/assign",
            json={"assigned_to_email": "lo02@summit.example", "strategy": "manual"},
        )
        assert assign.status_code == 200
        disposition = isolated_client.post(
            f"/api/leads/{borrower_id}/disposition",
            json={"lo_email": "lo02@summit.example", "outcome": "application_started"},
        )
        assert disposition.status_code == 200

        response = isolated_client.post(
            "/api/genie/message",
            json={"question": "Which LO had the highest application-start rate this week?"},
        )
    finally:
        if previous_lakebase is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = previous_lakebase
        if previous_audit is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous_audit
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "sales_ops"
    assert body["proof"]["trusted"] is False
    assert "mip_app.call_dispositions" in body["trusted_assets"]
    assert any(row["lo_email"] == "lo02@summit.example" for row in body["table_rows"])


def test_genie_sales_aging_omits_approval_rows_without_live_borrower() -> None:
    lakebase = app.dependency_overrides[get_lakebase_client]()
    previous_audit = app.dependency_overrides.get(get_audit_store)
    audit = InMemoryAuditStore()
    dispositioned = {row["borrower_id"] for row in lakebase.dispositions}
    live_borrower_id = next(
        borrower.borrower_id
        for borrower in mock_data.BORROWERS
        if borrower.borrower_id not in dispositioned
    )
    old = datetime.now(UTC) - timedelta(days=21)
    live_old = datetime.now(UTC) - timedelta(days=14)
    orphan_ids = [f"B-TEST-GENIE-ORPHAN-{i:02d}" for i in range(15)]
    added = [
        {
            "approval_id": uuid4(),
            "borrower_id": borrower_id,
            "action": "approve",
            "offer_code": "refi_plus_heloc",
            "actor_email": "skyler@entrada.ai",
            "rationale": None,
            "decided_at": old - timedelta(minutes=i),
            "request_id": str(uuid4()),
        }
        for i, borrower_id in enumerate(orphan_ids)
    ]
    added.append(
        {
            "approval_id": uuid4(),
            "borrower_id": live_borrower_id,
            "action": "approve",
            "offer_code": "refi_plus_heloc",
            "actor_email": "skyler@entrada.ai",
            "rationale": None,
            "decided_at": live_old,
            "request_id": str(uuid4()),
        }
    )
    lakebase.approvals.extend(added)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        response = _sales_ops_response(
            "Show approved leads that have not been touched in 7 days."
        )
    finally:
        if previous_audit is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous_audit
        added_ids = {row["approval_id"] for row in added}
        lakebase.approvals[:] = [
            row for row in lakebase.approvals if row.get("approval_id") not in added_ids
        ]

    assert response is not None
    rows = response.table_rows
    borrower_ids = {row["borrower_id"] for row in rows}
    assert live_borrower_id in borrower_ids
    assert borrower_ids.isdisjoint(orphan_ids)
    aging_queries = [
        sql for sql, _params, _limit in lakebase.fetchalls
        if "FROM mip_app.approvals" in sql and "age_days" in sql
    ]
    assert aging_queries
    assert "LIMIT 100" in aging_queries[-1]


def test_sales_routes_enforce_actor_scope_and_assignment_eligibility() -> None:
    pending_id = mock_data.BORROWERS[3].borrower_id
    pending = client.post(
        f"/api/leads/{pending_id}/assign",
        json={"assigned_to_email": "lo01@summit.example", "strategy": "manual"},
    )
    assert pending.status_code == 409
    assert "approved" in pending.text

    borrower_id = mock_data.BORROWERS[4].borrower_id
    _approve_for_sales(borrower_id)
    lo_client = TestClient(app)
    lo_client.headers.update({
        "X-Forwarded-Email": "lo01@summit.example",
        "X-Forwarded-Groups": "mip-admin",
    })

    assignment = lo_client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": "lo02@summit.example", "strategy": "manual"},
    )
    assert assignment.status_code == 403

    scoped_filter = lo_client.get("/api/leads?assigned_to=lo02@summit.example")
    assert scoped_filter.status_code == 403

    spoofed_disposition = lo_client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": "lo02@summit.example", "outcome": "connected"},
    )
    assert spoofed_disposition.status_code == 403


def test_sales_assignment_request_id_replay_is_strict() -> None:
    borrower_id = mock_data.BORROWERS[6].borrower_id
    other_id = mock_data.BORROWERS[7].borrower_id
    _approve_for_sales(borrower_id)
    _approve_for_sales(other_id)
    request_id = str(uuid4())

    first = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "request_id": request_id,
        },
    )
    assert first.status_code == 200

    retry = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "request_id": request_id,
        },
    )
    assert retry.status_code == 200
    assert retry.json()["assignment"]["borrower_id"] == borrower_id
    assert retry.json()["audit_event_id"] == ""

    collision = client.post(
        f"/api/leads/{other_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "request_id": request_id,
        },
    )
    assert collision.status_code == 403


def test_non_sales_actor_does_not_receive_sales_hydration() -> None:
    borrower_id = mock_data.BORROWERS[5].borrower_id
    _approve_for_sales(borrower_id)
    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": "lo01@summit.example", "strategy": "manual"},
    )
    assert assigned.status_code == 200

    nonsales = TestClient(app)
    nonsales.headers.update({
        "X-Forwarded-Email": "pat@entrada.ai",
        "X-Forwarded-Groups": "mip-admin",
    })
    borrower = nonsales.get(f"/api/borrowers/{borrower_id}")
    assert borrower.status_code == 200
    body = borrower.json()
    assert body.get("assigned_to_email") is None
    assert body.get("latest_disposition_outcome") is None


def test_genie_sales_manager_samples_route_to_sales_ops_adapter() -> None:
    questions = [
        "Top borrowers in an LO queue ranked by aging and score.",
        "How many leads went from approved to application started this week?",
        "Show approved leads that have not been touched in 7 days.",
    ]
    for question in questions:
        response = _sales_ops_response(question)
        assert response is not None
        assert response.source == "sales_ops"


def test_genie_lo_queue_answer_avoids_contact_field_language() -> None:
    response = _sales_ops_response("Top borrowers in an LO queue ranked by aging and score.")

    assert response is not None
    answer = response.answer.lower()
    assert "email" not in answer
    assert "phone" not in answer
    assert "street address" not in answer
    assert "selected loan officer" in answer


def test_sales_audit_metadata_is_strictly_validated() -> None:
    store = InMemoryAuditStore()
    base = {
        "actor": "skyler@entrada.ai",
        "action": "lead.disposition",
        "entity_type": "borrower",
        "entity_id": _borrower_id(),
        "event_type": "CALL_DISPOSITION",
    }
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            **base,
            payload_json={
                "borrower_id": _borrower_id(),
                "disposition_id": "Jane Smith",
                "lo_email": "lo01@summit.example",
                "outcome": "connected",
            },
        )
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            **base,
            payload_json={
                "borrower_id": _borrower_id(),
                "disposition_id": str(uuid4()),
                "lo_email": "lo01@summit.example",
                "outcome": "connected",
                "notes": "Jane Smith called back",
            },
        )
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="lead.distribute",
            entity_type="lead_queue",
            entity_id="_sales_distribution",
            event_type="LEAD_DISTRIBUTE",
            payload_json={
                "borrower_ids": [_borrower_id()],
                "lo_emails": ["lo01@summit.example"],
                "per_lo_counts": {"jane@example.com": 2},
                "strategy": "round_robin",
            },
        )


def test_audit_rollups_group_by_is_validated() -> None:
    ok = client.get("/api/audit/rollups?period=week&groupBy=actor")
    assert ok.status_code == 200

    action = client.get("/api/audit/rollups?period=week&groupBy=action")
    assert action.status_code == 200

    bad = client.get("/api/audit/rollups?period=week&groupBy=borrower_email")
    assert bad.status_code == 422
