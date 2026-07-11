"""LoanOfficerStateStore service tests against the fake Lakebase client."""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.loan_officer_state import (
    IllegalStatusTransitionError,
    LoanOfficerStateStore,
)

LO_01 = "55555555-5555-4555-8555-555555555501"
LO_02 = "55555555-5555-4555-8555-555555555502"
ADMIN = "skyler@entrada.ai"
BORROWER = "B-0000000000001"


@pytest.fixture()
def store(fake_lakebase_client: Any) -> LoanOfficerStateStore:
    return LoanOfficerStateStore(fake_lakebase_client)


def test_list_officers_returns_seeded_roster_with_coverage(store: LoanOfficerStateStore) -> None:
    officers = store.list_officers()
    assert [officer.loan_officer_id for officer in officers] == [LO_01, LO_02]
    assert officers[0].coverage_states == ["IL", "IN", "WI"]
    assert officers[0].coverage_counties == ["17031", "17043", "17089"]


def test_assign_lead_writes_assignment_and_audit_row(
    store: LoanOfficerStateStore, fake_lakebase_client: Any
) -> None:
    assignment, audit_event_id = store.assign_lead(
        borrower_id=BORROWER,
        loan_officer_id=LO_01,
        assigned_by=ADMIN,
    )
    assert assignment.borrower_id == BORROWER
    assert assignment.loan_officer_id == LO_01
    assert assignment.loan_officer_email == "lo01@summit.example"
    assert assignment.status == "assigned"
    assert audit_event_id
    events = fake_lakebase_client.audit_events
    assert len(events) == 1
    assert events[0]["event_type"] == "LEAD_ASSIGN"
    assert events[0]["entity_id"] == BORROWER


def test_assign_lead_rejects_unknown_officer(store: LoanOfficerStateStore) -> None:
    with pytest.raises(KeyError):
        store.assign_lead(
            borrower_id=BORROWER,
            loan_officer_id="99999999-9999-4999-8999-999999999999",
            assigned_by=ADMIN,
        )


def test_assign_lead_rejects_non_manager_actor(store: LoanOfficerStateStore) -> None:
    with pytest.raises(PermissionError):
        store.assign_lead(
            borrower_id=BORROWER,
            loan_officer_id=LO_01,
            assigned_by="lo02@summit.example",
        )


def test_assign_lead_is_idempotent_by_request_id(
    store: LoanOfficerStateStore, fake_lakebase_client: Any
) -> None:
    request_id = "11111111-2222-4333-8444-555555555555"
    first, first_audit = store.assign_lead(
        borrower_id=BORROWER,
        loan_officer_id=LO_01,
        assigned_by=ADMIN,
        request_id=request_id,
    )
    replay, replay_audit = store.assign_lead(
        borrower_id=BORROWER,
        loan_officer_id=LO_01,
        assigned_by=ADMIN,
        request_id=request_id,
    )
    assert replay.assignment_id == first.assignment_id
    assert first_audit and replay_audit == ""
    assert len(fake_lakebase_client.assignments) == 1
    assert len(fake_lakebase_client.audit_events) == 1


def test_transition_walks_the_full_lifecycle_with_audit_rows(
    store: LoanOfficerStateStore, fake_lakebase_client: Any
) -> None:
    assignment, _ = store.assign_lead(
        borrower_id=BORROWER, loan_officer_id=LO_01, assigned_by=ADMIN
    )
    for to_status in ("contact_drafted", "approved", "actioned", "outcome_recorded"):
        assignment, audit_event_id = store.transition_status(
            assignment_id=assignment.assignment_id,
            to_status=to_status,  # type: ignore[arg-type]
            actor=ADMIN,
        )
        assert assignment.status == to_status
        assert audit_event_id
    status_events = [
        event
        for event in fake_lakebase_client.audit_events
        if event["event_type"] == "LEAD_ASSIGNMENT_STATUS"
    ]
    assert len(status_events) == 4


def test_illegal_transition_is_rejected_and_writes_nothing(
    store: LoanOfficerStateStore, fake_lakebase_client: Any
) -> None:
    assignment, _ = store.assign_lead(
        borrower_id=BORROWER, loan_officer_id=LO_01, assigned_by=ADMIN
    )
    audit_count = len(fake_lakebase_client.audit_events)
    with pytest.raises(IllegalStatusTransitionError):
        store.transition_status(
            assignment_id=assignment.assignment_id,
            to_status="actioned",
            actor=ADMIN,
        )
    row = fake_lakebase_client.assignments[0]
    assert (row.get("status") or "assigned") == "assigned"
    assert len(fake_lakebase_client.audit_events) == audit_count


def test_assigned_officer_may_advance_but_other_officers_may_not(
    store: LoanOfficerStateStore,
) -> None:
    assignment, _ = store.assign_lead(
        borrower_id=BORROWER, loan_officer_id=LO_01, assigned_by=ADMIN
    )
    advanced, _ = store.transition_status(
        assignment_id=assignment.assignment_id,
        to_status="contact_drafted",
        actor="lo01@summit.example",
    )
    assert advanced.status == "contact_drafted"
    with pytest.raises(PermissionError):
        store.transition_status(
            assignment_id=assignment.assignment_id,
            to_status="approved",
            actor="lo02@summit.example",
        )


def test_list_assignments_filters_by_officer_borrower_and_status(
    store: LoanOfficerStateStore,
) -> None:
    first, _ = store.assign_lead(
        borrower_id=BORROWER, loan_officer_id=LO_01, assigned_by=ADMIN
    )
    other = "B-0000000000002"
    store.assign_lead(borrower_id=other, loan_officer_id=LO_02, assigned_by=ADMIN)
    store.transition_status(
        assignment_id=first.assignment_id, to_status="contact_drafted", actor=ADMIN
    )

    by_officer = store.list_assignments(loan_officer_id=LO_02)
    assert [a.borrower_id for a in by_officer] == [other]

    by_borrower = store.list_assignments(borrower_id=BORROWER)
    assert [a.assignment_id for a in by_borrower] == [first.assignment_id]

    by_status = store.list_assignments(status="contact_drafted")
    assert [a.assignment_id for a in by_status] == [first.assignment_id]
    assert by_status[0].loan_officer_name == "Summit LO 01"
