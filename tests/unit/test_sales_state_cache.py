from __future__ import annotations

import contextlib
from datetime import UTC, datetime

from backend.schemas.lead import LeadSummary
from backend.services.sales_state import (
    SalesStateStore,
    clear_sales_state_cache,
    hydrate_leads_with_sales_state,
)


class _Client:
    def __init__(self) -> None:
        self.fetchall_calls = 0
        self.fetchone_calls = 0
        self.label = "LO One"

    def fetchall(
        self,
        _sql: str,
        _params: dict[str, object] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        _ = limit
        self.fetchall_calls += 1
        return [
            {
                "assignment_id": "7b9a9c78-c222-4e03-b63e-c5a80f2695f6",
                "borrower_id": "B-CACHED",
                "assigned_to_email": "lo01@summit.example",
                "assigned_to_label": self.label,
                "assigned_by": "manager@summit.example",
                "assigned_at": datetime(2026, 5, 18, tzinfo=UTC),
                "expires_at": None,
                "released_at": None,
                "strategy": "manual",
            }
        ]

    def fetchone(
        self,
        _sql: str,
        _params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        self.fetchone_calls += 1
        return None


def test_sales_state_bulk_assignment_reads_are_cached_and_copied() -> None:
    clear_sales_state_cache()
    client = _Client()
    store = SalesStateStore(client)  # type: ignore[arg-type]

    first = store.assignments_for(["B-CACHED"])
    first["B-CACHED"].assigned_to_label = "mutated"
    second = store.assignments_for(["B-CACHED"])

    assert client.fetchall_calls == 1
    assert second["B-CACHED"].assigned_to_label == "LO One"
    clear_sales_state_cache()


def test_sales_state_cache_clear_exposes_new_lakebase_rows() -> None:
    clear_sales_state_cache()
    client = _Client()
    store = SalesStateStore(client)  # type: ignore[arg-type]

    assert store.assignments_for(["B-CACHED"])["B-CACHED"].assigned_to_label == "LO One"
    client.label = "LO Two"
    assert store.assignments_for(["B-CACHED"])["B-CACHED"].assigned_to_label == "LO One"
    clear_sales_state_cache()

    assert store.assignments_for(["B-CACHED"])["B-CACHED"].assigned_to_label == "LO Two"
    assert client.fetchall_calls == 2
    clear_sales_state_cache()


def test_missing_sales_team_actor_is_negative_cached() -> None:
    clear_sales_state_cache()
    client = _Client()
    store = SalesStateStore(client)  # type: ignore[arg-type]

    for _ in range(2):
        with contextlib.suppress(KeyError):
            store.visible_lo_emails(actor="outside@example.com")

    assert client.fetchone_calls == 1
    clear_sales_state_cache()


def test_lead_hydration_skips_workflow_queries_for_invisible_actor() -> None:
    clear_sales_state_cache()
    client = _Client()
    store = SalesStateStore(client)  # type: ignore[arg-type]
    lead = LeadSummary(
        borrower_id="B-CACHED",
        display_name="Borrower cached",
        city="Chicago",
        state="IL",
        zip="60617",
        segment_codes=["itm"],
        equity_estimate=100000,
        rate_spread_bps=150,
        opportunity_score=80,
        confidence=80,
        recommended_offer="Refinance + HELOC",
        why_now="test",
        evidence_ids=[],
        approval_status="pending",
    )

    hydrated = hydrate_leads_with_sales_state([lead], store, actor="outside@example.com")

    assert hydrated[0].borrower_id == "B-CACHED"
    assert client.fetchone_calls == 1
    assert client.fetchall_calls == 0
    clear_sales_state_cache()
