"""Lead distribution records the allocation that actually ran (audit wow-power-5 step 1).

The store allocates ``borrower_ids[i] -> lo_emails[i % len(lo_emails)]`` for
every strategy: nothing score-orders the ids and capacity/region are not read.
The Lead Queue used to send ``score_balanced`` whenever several leads went to
one officer, so the LEAD_DISTRIBUTE audit row claimed a balancing that never
happened. The queue now sends ``manual`` for one officer and ``round_robin``
for two or more; these tests pin that both are recorded verbatim, on the
assignment rows AND on the audit payload, and that round-robin alternates in
request order.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import sales_state_release, sales_state_writes
from backend.services.sales_distribution_plan import (
    Candidate,
    OfficerSlot,
    plan_score_balanced,
)
from tests.fixtures import mock_population as mock_data
from tests.fixtures.reviewed_approval import draft_proof

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "operator@summit.example"})


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
            "channel": "email",
            **draft_proof(draft.json()),
            "request_id": str(uuid4()),
        },
    )
    assert approved.status_code == 200


def _distribute_audit_payloads(fake_lakebase_client: Any) -> list[dict[str, Any]]:
    """The LEAD_DISTRIBUTE rows' stored metadata (the audit payload as written)."""
    payloads: list[dict[str, Any]] = []
    for row in fake_lakebase_client.audit_events:
        if row.get("event_type") != "LEAD_DISTRIBUTE":
            continue
        raw = row.get("metadata")
        payloads.append(json.loads(raw) if isinstance(raw, str) else dict(raw or {}))
    return payloads


def test_manual_distribution_to_one_officer_records_manual(fake_lakebase_client: Any) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (10, 11, 12)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)

    response = client.post(
        "/api/sales/distribute",
        json={
            "borrower_ids": borrower_ids,
            "lo_emails": ["lo01@summit.example"],
            "strategy": "manual",
            "expires_in_hours": 24,
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["strategy"] == "manual"
    assert body["assigned_count"] == 3
    assert [a["borrower_id"] for a in body["assignments"]] == borrower_ids
    assert {a["strategy"] for a in body["assignments"]} == {"manual"}
    assert {a["assigned_to_email"] for a in body["assignments"]} == {"lo01@summit.example"}
    payloads = _distribute_audit_payloads(fake_lakebase_client)
    assert len(payloads) == 1
    assert payloads[0]["strategy"] == "manual"
    assert payloads[0]["per_lo_counts"] == {"lo01@summit.example": 3}


def test_round_robin_alternates_in_request_order(fake_lakebase_client: Any) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (10, 11, 12)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)
    lo_emails = ["lo02@summit.example", "lo01@summit.example"]

    response = client.post(
        "/api/sales/distribute",
        json={
            "borrower_ids": borrower_ids,
            "lo_emails": lo_emails,
            "strategy": "round_robin",
            "expires_in_hours": 24,
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 200, response.text
    assignments = response.json()["assignments"]
    # Request order, not score order: lo02, lo01, lo02.
    assert [(a["borrower_id"], a["assigned_to_email"]) for a in assignments] == [
        (borrower_ids[0], "lo02@summit.example"),
        (borrower_ids[1], "lo01@summit.example"),
        (borrower_ids[2], "lo02@summit.example"),
    ]
    assert {a["strategy"] for a in assignments} == {"round_robin"}
    payloads = _distribute_audit_payloads(fake_lakebase_client)
    assert [payload["strategy"] for payload in payloads] == ["round_robin"]


# -- wow-power-5 step 2: the greedy fill (pure planner) ------------------------


def _slot(email: str, region: str | None, capacity: int, open_count: int = 0) -> OfficerSlot:
    return OfficerSlot(email, email.split("@")[0], region, capacity, open_count)


def _placed(plan: Any) -> dict[str, str]:
    return dict(plan.placements)


def test_the_highest_scores_are_placed_first_when_capacity_runs_out() -> None:
    plan = plan_score_balanced(
        [Candidate("B-1", 10, "IL"), Candidate("B-2", 90, "IL"), Candidate("B-3", 50, "IL")],
        [_slot("a@summit.example", "IL", 2)],
        expires_in_hours=24,
    )
    assert [borrower_id for borrower_id, _ in plan.placements] == ["B-2", "B-3"]
    assert plan.unplaced_no_capacity == 1
    assert plan.placed_score_sum == 140


def test_capacity_is_measured_against_the_open_book_and_zero_never_receives() -> None:
    plan = plan_score_balanced(
        [Candidate(f"B-{n}", 50, "TX") for n in range(5)],
        [
            _slot("full@summit.example", None, 3, open_count=3),
            _slot("one@summit.example", None, 2, open_count=1),
            _slot("zero@summit.example", None, 0),
        ],
        expires_in_hours=24,
    )
    assert set(_placed(plan).values()) == {"one@summit.example"}
    assert len(plan.placements) == 1
    assert plan.unplaced_no_capacity == 4


def test_an_in_region_officer_comes_before_a_less_busy_generalist() -> None:
    plan = plan_score_balanced(
        [Candidate("B-IL", 70, "IL"), Candidate("B-CA", 60, "CA")],
        [_slot("il@summit.example", "IL", 10, open_count=8), _slot("nat@summit.example", "National", 10)],
        expires_in_hours=24,
    )
    assert _placed(plan) == {"B-IL": "il@summit.example", "B-CA": "nat@summit.example"}
    loads = {load.slot.email: load for load in plan.officers}
    assert loads["il@summit.example"].in_region_count == 1
    assert loads["nat@summit.example"].in_region_count == 0


def test_national_or_empty_regions_never_count_as_in_region() -> None:
    plan = plan_score_balanced(
        [Candidate("B-1", 50, "National"), Candidate("B-2", 50, None)],
        [_slot("nat@summit.example", "National", 5), _slot("none@summit.example", None, 5)],
        expires_in_hours=24,
    )
    assert len(plan.placements) == 2
    assert all(load.in_region_count == 0 for load in plan.officers)


def test_ties_break_on_utilisation_then_score_sum_then_email() -> None:
    # Utilisation: b is 1/2 busy, a is 0/4, so a takes the lead.
    first = plan_score_balanced(
        [Candidate("B-1", 50, "TX")],
        [_slot("b@summit.example", None, 2, 1), _slot("a@summit.example", None, 4)],
        expires_in_hours=24,
    )
    assert _placed(first) == {"B-1": "a@summit.example"}
    # Equal utilisation and score sum: the email decides the first lead. The
    # second ties on utilisation again (1/4 vs 0/4 is not a tie, so use 4 vs 8).
    second = plan_score_balanced(
        [Candidate("B-1", 90, "TX"), Candidate("B-2", 10, "TX")],
        [_slot("y@summit.example", None, 8), _slot("x@summit.example", None, 4)],
        expires_in_hours=24,
    )
    # B-1: both 0 utilised, both 0 score -> x by email. B-2: x is 1/4, y 0/8 -> y.
    assert _placed(second) == {"B-1": "x@summit.example", "B-2": "y@summit.example"}
    third = plan_score_balanced(
        [Candidate("B-1", 90, "TX"), Candidate("B-2", 10, "TX"), Candidate("B-3", 5, "TX")],
        [_slot("y@summit.example", None, 2), _slot("x@summit.example", None, 2)],
        expires_in_hours=24,
    )
    # B-3: x and y are both 1/2 busy; y added less score (10 vs 90), so y.
    assert _placed(third)["B-3"] == "y@summit.example"


def test_the_plan_is_deterministic_for_any_input_order() -> None:
    candidates = [Candidate(f"B-{n:03d}", (n * 37) % 100, ["IL", "CA", "TX"][n % 3]) for n in range(40)]
    officers = [
        _slot("il@summit.example", "IL", 12, 3),
        _slot("ca@summit.example", "CA", 9, 0),
        _slot("nat@summit.example", "National", 10, 4),
    ]
    baseline = plan_score_balanced(candidates, officers, expires_in_hours=24)
    rng = random.Random(7)
    for _ in range(20):
        shuffled_candidates, shuffled_officers = candidates[:], officers[:]
        rng.shuffle(shuffled_candidates)
        rng.shuffle(shuffled_officers)
        assert plan_score_balanced(shuffled_candidates, shuffled_officers, expires_in_hours=24) == baseline


def test_the_fingerprint_is_the_canonical_hash_of_the_placements() -> None:
    candidates = [Candidate("B-2", 60, "IL"), Candidate("B-1", 70, "IL")]
    plan = plan_score_balanced(candidates, [_slot("a@summit.example", "IL", 5)], expires_in_hours=24)
    canonical = json.dumps(
        {
            "expires_in_hours": 24,
            "placements": [["B-1", "a@summit.example"], ["B-2", "a@summit.example"]],
            "strategy": "score_balanced",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert plan.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()
    longer = plan_score_balanced(candidates, [_slot("a@summit.example", "IL", 5)], expires_in_hours=48)
    assert longer.fingerprint != plan.fingerprint


# -- the Board's routes: dry run, commit, honesty ------------------------------


def _board(borrower_ids: list[str], **extra: Any) -> Any:
    body: dict[str, Any] = {
        "borrower_ids": borrower_ids,
        "lo_emails": ["lo01@summit.example", "lo02@summit.example"],
        "strategy": "score_balanced",
        "expires_in_hours": 24,
        **extra,
    }
    return client.post("/api/sales/distribute", json=body)


@pytest.fixture
def cache_clears(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    for module in (sales_state_writes, sales_state_release):
        monkeypatch.setattr(module, "clear_sales_state_cache", lambda: calls.append(1))
    return calls


def test_the_dry_run_writes_nothing_and_names_no_borrower(
    fake_lakebase_client: Any, cache_clears: list[int]
) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (0, 4, 1)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)
    assignments_before = [dict(row) for row in fake_lakebase_client.assignments]
    audits_before = len(fake_lakebase_client.audit_events)

    response = _board(borrower_ids, dry_run=True)

    assert response.status_code == 200, response.text
    body = response.json()
    preview = body["preview"]
    assert body["assigned_count"] == 0
    assert body["assignments"] == []
    assert body["audit_event_id"] is None
    assert preview["strategy"] == "score_balanced"
    assert preview["placed_count"] == 3
    assert preview["pool_count"] == 3
    assert {row["email"]: row["in_region_count"] for row in preview["officers"]} == {
        "lo01@summit.example": 2,  # the two IL borrowers
        "lo02@summit.example": 1,  # the CA borrower
    }
    assert fake_lakebase_client.assignments == assignments_before
    assert len(fake_lakebase_client.audit_events) == audits_before
    assert cache_clears == []
    assert not any(borrower_id in response.text for borrower_id in borrower_ids)


def test_a_commit_records_score_balanced_only_on_the_greedy_path(fake_lakebase_client: Any) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (2, 15)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)
    preview = _board(borrower_ids, dry_run=True).json()["preview"]

    committed = _board(
        borrower_ids, request_id=str(uuid4()), expected_plan_fingerprint=preview["plan_fingerprint"]
    )

    assert committed.status_code == 200, committed.text
    body = committed.json()
    assert body["strategy"] == "score_balanced"
    assert {a["strategy"] for a in body["assignments"]} == {"score_balanced"}
    assert body["preview"]["plan_fingerprint"] == preview["plan_fingerprint"]
    assert [p["strategy"] for p in _distribute_audit_payloads(fake_lakebase_client)] == ["score_balanced"]


def test_a_single_assign_refuses_score_balanced(fake_lakebase_client: Any) -> None:
    borrower_id = mock_data.BORROWERS[3].borrower_id
    _approve_for_sales(borrower_id)
    response = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={
            "assigned_to_email": "lo01@summit.example",
            "strategy": "score_balanced",
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert fake_lakebase_client.assignments == []


def test_board_fields_need_the_greedy_strategy_and_a_dry_run_takes_no_request_id() -> None:
    borrower_id = mock_data.BORROWERS[3].borrower_id
    assert _board([borrower_id], dry_run=True, request_id=str(uuid4())).status_code == 422
    assert _board([borrower_id], dry_run=True, strategy="round_robin").status_code == 422
    assert _board([borrower_id], expected_plan_fingerprint="0" * 64, strategy="manual").status_code == 422
    assert _board([borrower_id], expected_plan_fingerprint="not-hex").status_code == 422


def test_a_moved_plan_is_a_409_and_writes_nothing(fake_lakebase_client: Any) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (5, 6)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)
    preview = _board(borrower_ids, dry_run=True).json()["preview"]
    # Someone else takes one lead between the preview and the commit.
    taken = client.post(
        f"/api/leads/{borrower_ids[0]}/assign",
        json={"assigned_to_email": "lo01@summit.example", "strategy": "manual", "request_id": str(uuid4())},
    )
    assert taken.status_code == 200
    rows_before = len(fake_lakebase_client.assignments)
    audits_before = len(fake_lakebase_client.audit_events)

    response = _board(
        borrower_ids, request_id=str(uuid4()), expected_plan_fingerprint=preview["plan_fingerprint"]
    )

    assert response.status_code == 409
    assert response.json()["reason"] == "distribution_plan_changed"
    assert len(fake_lakebase_client.assignments) == rows_before
    assert len(fake_lakebase_client.audit_events) == audits_before


def test_unassignable_and_already_assigned_leads_are_skipped_and_counted(fake_lakebase_client: Any) -> None:
    approved = [mock_data.BORROWERS[index].borrower_id for index in (7, 8)]
    unapproved = mock_data.BORROWERS[9].borrower_id
    for borrower_id in approved:
        _approve_for_sales(borrower_id)
    taken = client.post(
        f"/api/leads/{approved[0]}/assign",
        json={"assigned_to_email": "lo02@summit.example", "strategy": "manual", "request_id": str(uuid4())},
    )
    assert taken.status_code == 200

    response = _board([*approved, unapproved], dry_run=True)

    assert response.status_code == 200, response.text
    preview = response.json()["preview"]
    assert preview["skipped_not_assignable"] == 1
    assert preview["skipped_already_assigned"] == 1
    assert preview["pool_count"] == 1
    assert preview["placed_count"] == 1


def test_a_replay_never_re_plans(fake_lakebase_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (10, 11)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)
    request_id = str(uuid4())
    first = _board(borrower_ids, request_id=request_id)
    assert first.status_code == 200, first.text

    def _no_plan(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a replay re-planned")

    monkeypatch.setattr(sales_state_writes, "plan_score_balanced", _no_plan)
    replay = _board(borrower_ids, request_id=request_id)

    assert replay.status_code == 200, replay.text
    assert replay.json()["assignments"] == first.json()["assignments"]
    assert replay.json()["audit_event_id"] == ""
    assert [p["strategy"] for p in _distribute_audit_payloads(fake_lakebase_client)] == ["score_balanced"]
