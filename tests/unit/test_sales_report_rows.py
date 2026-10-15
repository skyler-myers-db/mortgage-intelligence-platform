"""Typed Sales report rows (audit quality-04 P2, W5d w5-sales-loop deferred[4], W5e w5-wire-types A5).

The standup, conversion and outcome-summary reports used to declare their
rows as ``list[dict[str, object]]``, so the generated client type was an
open object and three wire-drift entries masked the hand types. Each row is
now a model that matches its emitter exactly. These tests run every emitter
over the shared fake Lakebase client and require each emitted row to carry
exactly its model's keys (a dropped or an undeclared key fails) and to
validate, then validate the whole response model the route returns.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from backend.schemas.sales import (
    SalesConversionResponse,
    SalesConversionRow,
    SalesOutcomeLoRow,
    SalesOutcomeSourceRow,
    SalesOutcomeSourceStatus,
    SalesOutcomeSummaryResponse,
    SalesStandupLoRow,
    SalesStandupResponse,
    SalesTopCompetitorRow,
)
from backend.services.sales_state import SalesStateStore, clear_sales_state_cache

DAY = "2026-10-14"
BORROWERS = ("B-0000000000001", "B-0000000000002", "B-0000000000003")


def _exact(model: type[BaseModel], row: dict[str, Any]) -> None:
    assert set(row) == set(model.model_fields), f"{model.__name__}: {sorted(set(row) ^ set(model.model_fields))}"
    model.model_validate(row)


@pytest.fixture
def store(fake_lakebase_client: Any) -> SalesStateStore:
    clear_sales_state_cache()
    fake_lakebase_client.dispositions.extend(
        [
            {"lo_email": "lo01@summit.example", "outcome": "connected", "borrower_id": BORROWERS[0]},
            {"lo_email": "lo01@summit.example", "outcome": "application_started", "borrower_id": BORROWERS[0]},
            {"lo_email": "lo02@summit.example", "outcome": "callback_scheduled", "borrower_id": BORROWERS[1]},
            {"lo_email": "lo02@summit.example", "outcome": "dead", "borrower_id": BORROWERS[2]},
        ]
    )
    fake_lakebase_client.outcomes.extend(
        [
            {
                "outcome_type": "lost_to_competitor",
                "source_system": "salesforce",
                "assigned_to_email": "lo01@summit.example",
                "competitor_lender_label": "Competitor D",
                "borrower_id": BORROWERS[0],
            },
            {
                "outcome_type": "application_submitted",
                "source_system": "manual_import",
                "assigned_to_email": None,
                "competitor_lender_label": None,
                "borrower_id": BORROWERS[1],
            },
        ]
    )
    return SalesStateStore(fake_lakebase_client)


def test_standup_rows_match_the_model_exactly(store: SalesStateStore) -> None:
    report = store.standup(date=DAY)

    assert [row["lo_email"] for row in report["by_lo"]] == ["lo01@summit.example", "lo02@summit.example"]
    for row in report["by_lo"]:
        _exact(SalesStandupLoRow, row)
    assert set(report) == set(SalesStandupResponse.model_fields)
    assert SalesStandupResponse(**report).by_lo[0].outcomes == {"application_started": 1, "connected": 1}


@pytest.mark.parametrize("group_by", ["lo", "cohort"])
def test_conversion_rows_match_the_model_exactly(store: SalesStateStore, group_by: str) -> None:
    rows = store.conversion(from_date=DAY, to_date=DAY, group_by=group_by)

    assert rows, group_by
    for row in rows:
        _exact(SalesConversionRow, row)
    response = SalesConversionResponse(from_date=DAY, to_date=DAY, group_by=group_by, rows=rows)
    keys = {row.group_key for row in response.rows}
    assert keys == ({"all_cohorts"} if group_by == "cohort" else {"lo01@summit.example", "lo02@summit.example"})


def test_outcome_summary_rows_match_their_models_exactly(store: SalesStateStore) -> None:
    summary = store.outcome_summary(from_date=DAY, to_date=DAY)

    assert set(summary) == set(SalesOutcomeSummaryResponse.model_fields)
    for model, field in (
        (SalesOutcomeSourceRow, "by_source_system"),
        (SalesOutcomeSourceStatus, "source_statuses"),
        (SalesOutcomeLoRow, "by_lo"),
        (SalesTopCompetitorRow, "top_competitors"),
    ):
        assert summary[field], field
        for row in summary[field]:
            _exact(model, row)
    response = SalesOutcomeSummaryResponse(**summary)
    assert response.top_competitors[0].competitor_lender_label == "Competitor D"
    assert {row.lo_email for row in response.by_lo} == {"lo01@summit.example", "unassigned"}


def test_a_row_missing_a_key_or_carrying_an_extra_one_is_refused() -> None:
    row = {"lo_email": "lo01@summit.example", "outcomes": {"connected": 1}, "calls_logged": 1}

    with pytest.raises(AssertionError):
        _exact(SalesStandupLoRow, {key: value for key, value in row.items() if key != "calls_logged"})
    with pytest.raises(AssertionError):
        _exact(SalesStandupLoRow, {**row, "phantom": 1})
    _exact(SalesStandupLoRow, row)
