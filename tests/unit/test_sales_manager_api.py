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
from backend.services.sales_state import (
    SalesStateStore,
    clear_sales_state_cache,
    get_sales_state_store,
)
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


def test_disposition_request_id_replays_without_duplicate_or_breaker(fake_lakebase_client) -> None:
    borrower_id = mock_data.BORROWERS[1].borrower_id
    _approve_for_sales(borrower_id)
    client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": "lo01@summit.example", "strategy": "manual"},
    )
    request_id = str(uuid4())
    payload = {
        "lo_email": "lo01@summit.example",
        "outcome": "connected",
        "notes": "Reviewed scenario and next steps.",
        "request_id": request_id,
    }

    first = client.post(f"/api/leads/{borrower_id}/disposition", json=payload)
    assert first.status_code == 200

    replay = client.post(f"/api/leads/{borrower_id}/disposition", json=payload)
    assert replay.status_code == 200
    assert replay.json()["disposition"]["disposition_id"] == first.json()["disposition"]["disposition_id"]
    assert len([
        row for row in fake_lakebase_client.dispositions
        if row.get("request_id") == request_id
    ]) == 1

    mismatch = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={**payload, "outcome": "not_now"},
    )
    assert mismatch.status_code == 409


def test_disposition_deactivated_cached_lo_is_rejected(fake_lakebase_client) -> None:
    manager_email = "manager-dispo-cache@summit.example"
    stale_lo_email = "lo-dispo-cache@summit.example"
    fake_lakebase_client.sales_team = [
        row
        for row in fake_lakebase_client.sales_team
        if row.get("email") not in {manager_email, stale_lo_email}
    ]
    fake_lakebase_client.sales_team.extend(
        [
            {
                "email": manager_email,
                "display_label": "Disposition Cache Manager",
                "role": "sales_manager",
                "manager_email": None,
                "region": "IL",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": stale_lo_email,
                "display_label": "Disposition Cache LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
        ]
    )
    borrower_id = mock_data.BORROWERS[14].borrower_id
    _approve_for_sales(borrower_id)
    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": stale_lo_email,
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert assigned.status_code == 200, assigned.text

    store = SalesStateStore(fake_lakebase_client)
    assert store.require_disposition_scope(
        actor=manager_email,
        lo_email=stale_lo_email,
        use_cache=True,
    ).email == stale_lo_email
    next(row for row in fake_lakebase_client.sales_team if row["email"] == stale_lo_email)[
        "active"
    ] = False

    manager_client = TestClient(app)
    manager_client.headers.update({"X-Forwarded-Email": manager_email})
    response = manager_client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={
            "lo_email": stale_lo_email,
            "outcome": "connected",
            "notes": "This should not persist.",
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "lo_email is not an active loan officer"
    assert not [
        row
        for row in fake_lakebase_client.dispositions
        if row.get("borrower_id") == borrower_id and row.get("lo_email") == stale_lo_email
    ]


def test_assignment_deactivated_cached_lo_is_rejected(fake_lakebase_client) -> None:
    manager_email = "manager-assign-cache@summit.example"
    stale_lo_email = "lo-assign-cache@summit.example"
    fake_lakebase_client.sales_team = [
        row
        for row in fake_lakebase_client.sales_team
        if row.get("email") not in {manager_email, stale_lo_email}
    ]
    fake_lakebase_client.sales_team.extend(
        [
            {
                "email": manager_email,
                "display_label": "Assignment Cache Manager",
                "role": "sales_manager",
                "manager_email": None,
                "region": "IL",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": stale_lo_email,
                "display_label": "Assignment Cache LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
        ]
    )
    borrower_id = mock_data.BORROWERS[15].borrower_id
    _approve_for_sales(borrower_id)

    store = SalesStateStore(fake_lakebase_client)
    assert store.require_assignee_in_scope(
        actor=manager_email,
        assigned_to_email=stale_lo_email,
        use_cache=True,
    ).email == stale_lo_email
    next(row for row in fake_lakebase_client.sales_team if row["email"] == stale_lo_email)[
        "active"
    ] = False

    manager_client = TestClient(app)
    manager_client.headers.update({"X-Forwarded-Email": manager_email})
    response = manager_client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": stale_lo_email,
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "assigned_to_email is not an active loan officer"
    assert not [
        row
        for row in fake_lakebase_client.assignments
        if row.get("borrower_id") == borrower_id and row.get("assigned_to_email") == stale_lo_email
    ]


def test_distribute_deactivated_cached_lo_is_rejected(fake_lakebase_client) -> None:
    manager_email = "manager-distribute-cache@summit.example"
    stale_lo_email = "lo-distribute-cache@summit.example"
    fake_lakebase_client.sales_team = [
        row
        for row in fake_lakebase_client.sales_team
        if row.get("email") not in {manager_email, stale_lo_email}
    ]
    fake_lakebase_client.sales_team.extend(
        [
            {
                "email": manager_email,
                "display_label": "Distribution Cache Manager",
                "role": "sales_manager",
                "manager_email": None,
                "region": "IL",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": stale_lo_email,
                "display_label": "Distribution Cache LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
        ]
    )
    borrower_id = mock_data.BORROWERS[16].borrower_id
    _approve_for_sales(borrower_id)

    store = SalesStateStore(fake_lakebase_client)
    assert store.require_assignee_in_scope(
        actor=manager_email,
        assigned_to_email=stale_lo_email,
        use_cache=True,
    ).email == stale_lo_email
    next(row for row in fake_lakebase_client.sales_team if row["email"] == stale_lo_email)[
        "active"
    ] = False

    manager_client = TestClient(app)
    manager_client.headers.update({"X-Forwarded-Email": manager_email})
    response = manager_client.post(
        "/api/sales/distribute",
        json={
            "borrower_ids": [borrower_id],
            "lo_emails": [stale_lo_email],
            "strategy": "round_robin",
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "lo_emails must all be active loan officers"
    assert not [
        row
        for row in fake_lakebase_client.assignments
        if row.get("borrower_id") == borrower_id and row.get("assigned_to_email") == stale_lo_email
    ]


def test_assignment_read_released_cached_assignment_is_not_visible(
    fake_lakebase_client,
) -> None:
    borrower_id = mock_data.BORROWERS[17].borrower_id
    _approve_for_sales(borrower_id)
    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert assigned.status_code == 200, assigned.text

    store = SalesStateStore(fake_lakebase_client)
    assert store.active_assignment_for(borrower_id, use_cache=True) is not None
    for row in fake_lakebase_client.assignments:
        if row.get("borrower_id") == borrower_id and row.get("released_at") is None:
            row["released_at"] = datetime.now(UTC)

    response = client.get(f"/api/leads/{borrower_id}/assignment")

    assert response.status_code == 404, response.text


def test_sales_reports_deactivated_cached_manager_is_forbidden(
    fake_lakebase_client,
) -> None:
    manager_email = "manager-report-cache@summit.example"
    managed_lo_email = "lo-report-cache@summit.example"
    fake_lakebase_client.sales_team = [
        row
        for row in fake_lakebase_client.sales_team
        if row.get("email") not in {manager_email, managed_lo_email}
    ]
    fake_lakebase_client.sales_team.extend(
        [
            {
                "email": manager_email,
                "display_label": "Report Cache Manager",
                "role": "sales_manager",
                "manager_email": None,
                "region": "IL",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": managed_lo_email,
                "display_label": "Report Cache LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
        ]
    )

    store = SalesStateStore(fake_lakebase_client)
    assert store.require_manager_actor(manager_email, use_cache=True).email == manager_email
    assert store.visible_lo_emails(actor=manager_email, use_cache=True) == {managed_lo_email}
    next(row for row in fake_lakebase_client.sales_team if row["email"] == manager_email)[
        "active"
    ] = False

    manager_client = TestClient(app)
    manager_client.headers.update({"X-Forwarded-Email": manager_email})
    today = datetime.now(UTC).date().isoformat()
    standup = manager_client.get(f"/api/sales/standup?date={today}")

    assert standup.status_code == 403, standup.text


def test_disposition_rejects_future_and_backwards_callback() -> None:
    borrower_id = mock_data.BORROWERS[1].borrower_id
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    future_disposition = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={
            "lo_email": "lo01@summit.example",
            "outcome": "connected",
            "occurred_at": future,
        },
    )
    assert future_disposition.status_code == 422

    backwards_callback = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={
            "lo_email": "lo01@summit.example",
            "outcome": "callback_scheduled",
            "occurred_at": datetime.now(UTC).isoformat(),
            "callback_at": past,
        },
    )
    assert backwards_callback.status_code == 422

    implicit_now_backwards_callback = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={
            "lo_email": "lo01@summit.example",
            "outcome": "callback_scheduled",
            "callback_at": past,
        },
    )
    assert implicit_now_backwards_callback.status_code == 422


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
    repeat_contact = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": "lo02@summit.example", "outcome": "connected"},
    )
    assert repeat_contact.status_code == 200

    today = datetime.now(UTC).date().isoformat()
    standup = client.get(f"/api/sales/standup?date={today}")
    assert standup.status_code == 200
    assert standup.json()["applications_started"] >= 1

    conversion = client.get(f"/api/sales/conversion?from={today}&to={today}&groupBy=lo")
    assert conversion.status_code == 200
    rows = conversion.json()["rows"]
    lo_row = next(row for row in rows if row["group_key"] == "lo02@summit.example")
    assert lo_row["calls_attempted"] == 2
    assert lo_row["unique_leads_contacted"] == 1
    assert lo_row["unique_application_starts"] == 1
    assert lo_row["application_start_rate"] == 1.0

    invalid = client.get(f"/api/sales/conversion?from={today}&to=2020-01-01&groupBy=lo")
    assert invalid.status_code == 422

    aging = client.get("/api/sales/aging?older_than_days=7")
    assert aging.status_code == 200
    assert isinstance(aging.json(), list)


def test_campaign_performance_uses_nested_same_borrower_sets(fake_lakebase_client) -> None:
    today = datetime.now(UTC).date().isoformat()
    fake_lakebase_client.dispositions.extend(
        [
            {
                "borrower_id": "B-REACHED-ONLY",
                "lo_email": "lo01@summit.example",
                "outcome": "connected",
                "occurred_at": datetime.now(UTC),
            },
            {
                "borrower_id": "B-NESTED",
                "lo_email": "lo01@summit.example",
                "outcome": "application_started",
                "occurred_at": datetime.now(UTC),
            },
        ]
    )
    fake_lakebase_client.outcomes.extend(
        [
            {
                "borrower_id": "B-DISJOINT",
                "assigned_to_email": "lo01@summit.example",
                "outcome_type": "closed_funded",
                "occurred_at": datetime.now(UTC),
            },
            {
                "borrower_id": "B-NESTED",
                "assigned_to_email": "lo01@summit.example",
                "outcome_type": "application_submitted",
                "occurred_at": datetime.now(UTC),
            },
            {
                "borrower_id": "B-NESTED",
                "assigned_to_email": "lo01@summit.example",
                "outcome_type": "closed_funded",
                "occurred_at": datetime.now(UTC),
            },
        ]
    )

    response = client.get(f"/api/sales/campaign-performance?from={today}&to={today}")
    assert response.status_code == 200
    assert response.json() == {
        "from_date": today,
        "to_date": today,
        "unique_contacts_reached": 2,
        "unique_application_starts": 1,
        "unique_applications_submitted": 1,
        "unique_closed_funded": 1,
        "methodology": "same_borrower_nested_funnel",
    }

    invalid = client.get(
        f"/api/sales/campaign-performance?from={today}&to=2020-01-01"
    )
    assert invalid.status_code == 422


def test_sales_closed_loop_outcomes_are_recorded_and_summarized(fake_lakebase_client) -> None:
    borrower_id = mock_data.BORROWERS[2].borrower_id
    request_id = str(uuid4())
    fake_lakebase_client.activation_destinations[0]["status"] = "connected"
    _approve_for_sales(borrower_id)
    client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": "lo01@summit.example", "strategy": "manual"},
    )

    recorded = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "lost_to_competitor",
            "source_system": "salesforce",
            "source_record_ref": "sf_case_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 425000,
            "competitor_lender_label": "Competitor D",
            "request_id": request_id,
        },
    )
    assert recorded.status_code == 200
    body = recorded.json()
    assert body["outcome"]["borrower_id"] == borrower_id
    assert body["outcome"]["outcome_type"] == "lost_to_competitor"
    assert body["outcome"]["audit_event_id"]

    replay = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "lost_to_competitor",
            "source_system": "salesforce",
            "source_record_ref": "sf_case_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 425000,
            "competitor_lender_label": "Competitor D",
            "request_id": request_id,
        },
    )
    assert replay.status_code == 200
    assert replay.json()["outcome"]["outcome_id"] == body["outcome"]["outcome_id"]

    today = datetime.now(UTC).date().isoformat()
    summary = client.get(f"/api/sales/outcomes/summary?from={today}&to={today}")
    assert summary.status_code == 200
    data = summary.json()
    assert data["lost_to_competitor"] >= 1
    assert data["top_competitors"][0]["competitor_lender_label"] == "Competitor D"
    salesforce_status = next(
        row for row in data["source_statuses"] if row["source_system"] == "salesforce"
    )
    assert salesforce_status["configured"] is True
    assert salesforce_status["status"] == "connected"

    unsafe = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "lost_to_competitor",
            "source_system": "salesforce",
            "source_record_ref": "sf_case_999",
            "competitor_lender_label": "555-123-4567",
        },
    )
    assert unsafe.status_code == 422

    person_name = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "lost_to_competitor",
            "source_system": "salesforce",
            "source_record_ref": "sf_case_1000",
            "competitor_lender_label": "John Smith",
        },
    )
    assert person_name.status_code == 422

    raw_brand = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "lost_to_competitor",
            "source_system": "salesforce",
            "source_record_ref": "sf_case_1001",
            "competitor_lender_label": "Rocket Mortgage",
        },
    )
    assert raw_brand.status_code == 422


def test_sales_outcome_rejects_unconfigured_customer_sources(fake_lakebase_client) -> None:
    borrower_id = mock_data.BORROWERS[2].borrower_id
    _approve_for_sales(borrower_id)

    missing_key = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "manual_import",
        },
    )
    assert missing_key.status_code == 422
    assert "request_id or source_record_ref is required" in str(missing_key.json())

    blocked = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "salesforce",
            "source_record_ref": "sf_case_pending",
        },
    )
    assert blocked.status_code == 409
    assert "not configured" in blocked.json()["detail"]

    manual = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "manual_import",
            "source_record_ref": "manual_upload_1",
        },
    )
    assert manual.status_code == 200
    assert manual.json()["outcome"]["source_system"] == "manual_import"

    summary = client.get(
        f"/api/sales/outcomes/summary?from={datetime.now(UTC).date().isoformat()}"
        f"&to={datetime.now(UTC).date().isoformat()}"
    )
    statuses = summary.json()["source_statuses"]
    assert next(row for row in statuses if row["source_system"] == "salesforce")["configured"] is False
    assert next(row for row in statuses if row["source_system"] == "manual_import")["configured"] is True


def test_sales_outcome_idempotency_replay_is_strict(fake_lakebase_client) -> None:
    borrower_id = mock_data.BORROWERS[2].borrower_id
    other_id = mock_data.BORROWERS[3].borrower_id
    request_id = str(uuid4())
    fake_lakebase_client.activation_destinations[2]["status"] = "connected"
    _approve_for_sales(borrower_id)
    _approve_for_sales(other_id)

    first = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
            "request_id": request_id,
        },
    )
    assert first.status_code == 200

    replay_by_request = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
            "request_id": request_id,
        },
    )
    assert replay_by_request.status_code == 200
    assert replay_by_request.json()["outcome"]["outcome_id"] == first.json()["outcome"]["outcome_id"]

    replay_by_source_record = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
        },
    )
    assert replay_by_source_record.status_code == 200
    assert replay_by_source_record.json()["outcome"]["outcome_id"] == first.json()["outcome"]["outcome_id"]

    fake_lakebase_client.activation_destinations[2]["status"] = "disabled"
    replay_after_disable = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
            "request_id": request_id,
        },
    )
    assert replay_after_disable.status_code == 200
    assert replay_after_disable.json()["outcome"]["outcome_id"] == first.json()["outcome"]["outcome_id"]

    unauthorized_replay = client.post(
        f"/api/leads/{borrower_id}/outcome",
        headers={"X-Forwarded-Email": "lo01@summit.example"},
        json={
            "outcome_type": "closed_funded",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
            "request_id": request_id,
        },
    )
    assert unauthorized_replay.status_code == 403

    wrong_borrower = client.post(
        f"/api/leads/{other_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
            "request_id": request_id,
        },
    )
    assert wrong_borrower.status_code == 409

    wrong_payload = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
        },
    )
    assert wrong_payload.status_code == 409

    changed_timestamp = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "los_pos",
            "source_record_ref": "los_file_123",
            "assigned_to_email": "lo01@summit.example",
            "loan_amount": 525000,
            "request_id": request_id,
            "occurred_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )
    assert changed_timestamp.status_code == 409


def test_sales_manager_outcome_requires_in_scope_assignment(fake_lakebase_client) -> None:
    manager_email = "manager-scope@summit.example"
    managed_lo_email = "lo-managed-scope@summit.example"
    alternate_lo_email = "lo-managed-scope-alt@summit.example"
    managed_emails = {manager_email, managed_lo_email, alternate_lo_email}
    fake_lakebase_client.sales_team = [
        row for row in fake_lakebase_client.sales_team if row.get("email") not in managed_emails
    ]
    fake_lakebase_client.sales_team.extend(
        [
            {
                "email": manager_email,
                "display_label": "Summit Manager",
                "role": "sales_manager",
                "manager_email": None,
                "region": "IL",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": managed_lo_email,
                "display_label": "Managed LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
            {
                "email": alternate_lo_email,
                "display_label": "Managed Alt LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
        ]
    )
    borrower_id = mock_data.BORROWERS[10].borrower_id
    other_id = mock_data.BORROWERS[11].borrower_id
    fake_lakebase_client.assignments = [
        row
        for row in fake_lakebase_client.assignments
        if row.get("borrower_id") not in {borrower_id, other_id}
    ]
    clear_sales_state_cache()
    _approve_for_sales(borrower_id)
    _approve_for_sales(other_id)

    manager_client = TestClient(app)
    manager_client.headers.update({"X-Forwarded-Email": manager_email})

    unscoped = manager_client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "manual_import",
            "source_record_ref": "manual_unscoped_alpha",
            "loan_amount": 525000,
            "request_id": str(uuid4()),
        },
    )
    assert unscoped.status_code == 403

    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": managed_lo_email,
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert assigned.status_code == 200

    scoped = manager_client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "manual_import",
            "source_record_ref": "manual_scoped_alpha",
            "assigned_to_email": managed_lo_email,
            "loan_amount": 525000,
            "request_id": str(uuid4()),
        },
    )
    assert scoped.status_code == 200, scoped.text

    scoped_without_payload_assignee = manager_client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "manual_import",
            "source_record_ref": "manual_scoped_inferred_alpha",
            "loan_amount": 525000,
            "request_id": str(uuid4()),
        },
    )
    assert scoped_without_payload_assignee.status_code == 200, scoped_without_payload_assignee.text
    assert scoped_without_payload_assignee.json()["outcome"]["assigned_to_email"] == (
        managed_lo_email
    )

    wrong_assignee = manager_client.post(
        f"/api/leads/{borrower_id}/outcome",
            json={
            "outcome_type": "closed_funded",
            "source_system": "manual_import",
            "source_record_ref": "manual_wrong_assignee_alpha",
            "assigned_to_email": alternate_lo_email,
            "loan_amount": 525000,
            "request_id": str(uuid4()),
        },
    )
    assert wrong_assignee.status_code == 403, wrong_assignee.text

    fake_lakebase_client.assignments = [
        row for row in fake_lakebase_client.assignments if row.get("borrower_id") != other_id
    ]
    clear_sales_state_cache()
    in_scope_unassigned = manager_client.post(
        f"/api/leads/{other_id}/outcome",
        json={
            "outcome_type": "closed_funded",
            "source_system": "manual_import",
            "source_record_ref": "manual_unassigned_in_scope_alpha",
            "assigned_to_email": managed_lo_email,
            "loan_amount": 425000,
            "request_id": str(uuid4()),
        },
    )
    assert in_scope_unassigned.status_code == 403, in_scope_unassigned.text


def test_sales_manager_outcome_stale_active_assignment_is_forbidden(
    fake_lakebase_client,
) -> None:
    manager_email = "manager-stale@summit.example"
    stale_lo_email = "lo-stale@summit.example"
    fake_lakebase_client.sales_team = [
        row
        for row in fake_lakebase_client.sales_team
        if row.get("email") not in {manager_email, stale_lo_email}
    ]
    fake_lakebase_client.sales_team.extend(
        [
            {
                "email": manager_email,
                "display_label": "Stale Manager",
                "role": "sales_manager",
                "manager_email": None,
                "region": "IL",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": stale_lo_email,
                "display_label": "Stale LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
        ]
    )
    borrower_id = mock_data.BORROWERS[12].borrower_id
    _approve_for_sales(borrower_id)
    clear_sales_state_cache()

    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": stale_lo_email,
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert assigned.status_code == 200, assigned.text

    next(row for row in fake_lakebase_client.sales_team if row["email"] == stale_lo_email)[
        "active"
    ] = False

    manager_client = TestClient(app)
    manager_client.headers.update({"X-Forwarded-Email": manager_email})
    response = manager_client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "manual_import",
            "source_record_ref": "manual_stale_assignment_alpha",
            "loan_amount": 425000,
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "sales operation is outside the actor scope"
    assert not [
        row
        for row in fake_lakebase_client.outcomes
        if str(row.get("source_record_ref") or "").startswith("manual_stale_assignment_")
    ]


def test_sales_manager_outcome_released_cached_assignment_is_forbidden(
    fake_lakebase_client,
) -> None:
    manager_email = "manager-released@summit.example"
    released_lo_email = "lo-released@summit.example"
    fake_lakebase_client.sales_team = [
        row
        for row in fake_lakebase_client.sales_team
        if row.get("email") not in {manager_email, released_lo_email}
    ]
    fake_lakebase_client.sales_team.extend(
        [
            {
                "email": manager_email,
                "display_label": "Released Manager",
                "role": "sales_manager",
                "manager_email": None,
                "region": "IL",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": released_lo_email,
                "display_label": "Released LO",
                "role": "loan_officer",
                "manager_email": manager_email,
                "region": "IL",
                "capacity_per_day": 20,
                "active": True,
            },
        ]
    )
    borrower_id = mock_data.BORROWERS[13].borrower_id
    _approve_for_sales(borrower_id)

    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": released_lo_email,
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert assigned.status_code == 200, assigned.text

    store = SalesStateStore(fake_lakebase_client)
    assert store.active_assignment_for(borrower_id, use_cache=True) is not None
    for row in fake_lakebase_client.assignments:
        if row.get("borrower_id") == borrower_id and row.get("released_at") is None:
            row["released_at"] = datetime.now(UTC)

    manager_client = TestClient(app)
    manager_client.headers.update({"X-Forwarded-Email": manager_email})
    response = manager_client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "manual_import",
            "source_record_ref": "manual_released_assignment_alpha",
            "loan_amount": 425000,
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 403, response.text
    assert not [
        row
        for row in fake_lakebase_client.outcomes
        if row.get("source_record_ref") == "manual_released_assignment_alpha"
    ]


def test_sales_outcome_rejects_future_occurred_at() -> None:
    borrower_id = mock_data.BORROWERS[2].borrower_id
    _approve_for_sales(borrower_id)

    future = client.post(
        f"/api/leads/{borrower_id}/outcome",
        json={
            "outcome_type": "application_submitted",
            "source_system": "manual_import",
            "source_record_ref": "manual_future",
            "occurred_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert future.status_code == 422


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

    changed_strategy = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "score_balanced",
            "request_id": request_id,
        },
    )
    assert changed_strategy.status_code == 403

    changed_expiry = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "expires_in_hours": 48,
            "request_id": request_id,
        },
    )
    assert changed_expiry.status_code == 403

    still_active = client.get(f"/api/leads/{borrower_id}/assignment")
    assert still_active.status_code == 200
    assert still_active.json()["assigned_to_email"] == "lo01@summit.example"

    reassigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo02@summit.example",
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert reassigned.status_code == 200

    replay_after_release = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "request_id": request_id,
        },
    )
    assert replay_after_release.status_code == 200
    assert replay_after_release.json()["assignment"]["assigned_to_email"] == "lo01@summit.example"
    assert replay_after_release.json()["assignment"]["released_at"] is not None
    assert replay_after_release.json()["audit_event_id"] == ""

    collision = client.post(
        f"/api/leads/{other_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "request_id": request_id,
        },
    )
    assert collision.status_code == 403


def test_sales_distribution_request_id_replay_is_strict() -> None:
    borrower_ids = [mock_data.BORROWERS[8].borrower_id, mock_data.BORROWERS[9].borrower_id]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)
    request_id = str(uuid4())
    payload = {
        "borrower_ids": borrower_ids,
        "lo_emails": ["lo01@summit.example", "lo02@summit.example"],
        "strategy": "round_robin",
        "expires_in_hours": 24,
        "request_id": request_id,
    }

    first = client.post("/api/sales/distribute", json=payload)
    assert first.status_code == 200
    assert first.json()["assigned_count"] == 2

    replay = client.post("/api/sales/distribute", json=payload)
    assert replay.status_code == 200
    assert replay.json()["assigned_count"] == 2
    assert replay.json()["audit_event_id"] == ""

    changed_allocation = client.post(
        "/api/sales/distribute",
        json={**payload, "lo_emails": ["lo02@summit.example", "lo01@summit.example"]},
    )
    assert changed_allocation.status_code == 403

    changed_strategy = client.post(
        "/api/sales/distribute",
        json={**payload, "strategy": "score_balanced"},
    )
    assert changed_strategy.status_code == 403

    for borrower_id, expected_lo in zip(borrower_ids, payload["lo_emails"], strict=True):
        assignment = client.get(f"/api/leads/{borrower_id}/assignment")
        assert assignment.status_code == 200
        assert assignment.json()["assigned_to_email"] == expected_lo


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
    with pytest.raises(AuditMetadataValueViolation):
        store.write(
            actor="skyler@entrada.ai",
            action="lead.outcome",
            entity_type="borrower",
            entity_id=_borrower_id(),
            event_type="LEAD_OUTCOME",
            payload_json={
                "borrower_id": _borrower_id(),
                "lead_outcome_id": str(uuid4()),
                "lead_outcome_type": "lost_to_competitor",
                "source_system": "salesforce",
                "competitor_lender_label": "555-123-4567",
            },
        )


def test_lead_outcome_audit_event_is_server_owned() -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "skyler@entrada.ai",
            "action": "lead.outcome",
            "entity_type": "borrower",
            "entity_id": _borrower_id(),
            "event_type": "LEAD_OUTCOME",
            "payload_json": {
                "borrower_id": _borrower_id(),
                "lead_outcome_type": "closed_funded",
                "source_system": "los_pos",
            },
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "event type is owned by a governed server route"


def test_audit_rollups_group_by_is_validated() -> None:
    ok = client.get("/api/audit/rollups?period=week&groupBy=actor")
    assert ok.status_code == 200

    action = client.get("/api/audit/rollups?period=week&groupBy=action")
    assert action.status_code == 200

    bad = client.get("/api/audit/rollups?period=week&groupBy=borrower_email")
    assert bad.status_code == 422
