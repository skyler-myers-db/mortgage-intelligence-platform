"""The derived state of one approval-request borrower (D-approval-flow-c item 9).

The order is binding: a linked finalized decision wins, then withdrawn, then
expired (closed or older than 30 days), then a later decision without the
link, else open. These pin the pure function the request reads feed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.services.approval_request_state import ItemFacts, derive_row, rows_by_batch

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
BATCH = "11111111-1111-4111-8111-111111111111"
APPROVAL = "22222222-2222-4222-8222-222222222222"


def _facts(**overrides: object) -> ItemFacts:
    values: dict[str, object] = {
        "batch_id": BATCH,
        "borrower_id": "B-ARQTESTX00001",
        "status": "open",
        "batch_created_at": NOW - timedelta(days=2),
        "linked_action": None,
        "linked_approval_id": None,
        "outside_action": None,
        "read_at": NOW,
    }
    values.update(overrides)
    return ItemFacts(**values)  # type: ignore[arg-type]


def test_an_untouched_item_is_open() -> None:
    row = derive_row(_facts())
    assert (row.state, row.approval_id) == ("open", None)


@pytest.mark.parametrize(("action", "state"), [("approve", "approved"), ("reject", "rejected")])
def test_a_linked_decision_wins_over_every_other_fact(action: str, state: str) -> None:
    row = derive_row(
        _facts(
            linked_action=action,
            linked_approval_id=APPROVAL,
            status="expired",
            outside_action="approve",
            batch_created_at=NOW - timedelta(days=40),
        )
    )
    assert (row.state, row.approval_id) == (state, APPROVAL)


def test_withdrawn_comes_before_expired_and_decided_outside() -> None:
    row = derive_row(_facts(status="withdrawn", outside_action="approve", batch_created_at=NOW - timedelta(days=31)))
    assert row.state == "withdrawn"


@pytest.mark.parametrize(
    "facts",
    [
        {"status": "expired"},
        {"batch_created_at": NOW - timedelta(days=31)},
        {"batch_created_at": NOW - timedelta(days=31), "outside_action": "reject"},
    ],
    ids=["closed-as-expired", "31-days-old", "31-days-old-and-decided-outside"],
)
def test_expired_after_thirty_days_or_when_closed(facts: dict[str, object]) -> None:
    assert derive_row(_facts(**facts)).state == "expired"


def test_a_request_just_under_thirty_days_is_still_open() -> None:
    assert derive_row(_facts(batch_created_at=NOW - timedelta(days=29, hours=23))).state == "open"


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_an_unlinked_later_decision_reads_decided_outside(action: str) -> None:
    row = derive_row(_facts(outside_action=action))
    assert (row.state, row.approval_id) == ("decided_outside", None)


@pytest.mark.parametrize("action", ["hold", "revoke"])
def test_hold_and_revoke_never_decide_a_request(action: str) -> None:
    assert derive_row(_facts(linked_action=action, outside_action=action)).state == "open"


def test_rows_group_by_request_in_read_order_and_accept_naive_timestamps() -> None:
    other = "33333333-3333-4333-8333-333333333333"
    naive = (NOW - timedelta(days=1)).replace(tzinfo=None)
    rows = [
        {"batch_id": BATCH, "borrower_id": "B-ARQTESTX00002", "status": "open",
         "batch_created_at": naive, "read_at": NOW.replace(tzinfo=None)},
        {"batch_id": other, "borrower_id": "B-ARQTESTX00003", "status": "withdrawn",
         "batch_created_at": NOW, "read_at": NOW},
        {"batch_id": BATCH, "borrower_id": "B-ARQTESTX00001", "status": "open",
         "batch_created_at": naive, "read_at": NOW, "linked_action": "approve",
         "linked_approval_id": APPROVAL},
    ]
    grouped = rows_by_batch(rows)
    assert [(row.borrower_id, row.state) for row in grouped[BATCH]] == [
        ("B-ARQTESTX00002", "open"),
        ("B-ARQTESTX00001", "approved"),
    ]
    assert [row.state for row in grouped[other]] == ["withdrawn"]
