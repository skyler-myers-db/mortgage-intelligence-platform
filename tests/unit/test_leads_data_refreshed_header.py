"""GET /api/leads states its data freshness in X-Data-Refreshed-At (audit delivery-08).

The Lead Queue used to fire a whole-book ``POST /api/portfolio/preview`` on
every mount only to stamp ``refreshed_at`` on a CSV export it might never
make. The ranked rows already come from gold tables that carry
``refreshed_at``, so the list response now states the newest of them in a
header with no extra statement, and the frontend reads it from there.

Pinned here at the layers the value crosses: the gold projection, the
redactor, the ``LeadSummary`` model (``exclude=True``: never in a JSON body or
the public schema), the repository TTL cache (``model_copy(deep=True)``), the
sales-state hydration (``model_copy``) and the route that formats the header.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.leads import _data_refreshed_at_header
from backend.main import app
from backend.schemas.lead import LeadSummary
from backend.services.repositories import get_lead_repository
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.repositories.databricks_shared import (
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)
from tests.fixtures import mock_population as mock_data

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})

HEADER = "X-Data-Refreshed-At"


def _gold_row(borrower_id: str, refreshed_at: object) -> dict[str, Any]:
    """A gold.lead_population row as the SQL client returns it."""
    return {
        "clip": "1234567890",
        "borrower_id": borrower_id,
        "display_name": "Owner 1a2b3c4d",
        "city": "Chicago",
        "state": "IL",
        "zip": "60617",
        "segment_codes": ["itm"],
        "equity_estimate": 120000,
        "rate_spread_bps": 110,
        "opportunity_score": 82,
        "confidence": 74,
        "recommended_offer_code": "refi",
        "recommended_offer": "Rate-and-term refinance",
        "why_now": "Rate spread above threshold.",
        "evidence_ids": ["ev-1"],
        "approval_status": "pending",
        "marketing_eligible": True,
        "consent_status": "opt_in",
        "refreshed_at": refreshed_at,
    }


class _SqlClient:
    """Answers the lead list and count statements; records every list SQL."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.list_statements: list[str] = []

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        _ = params
        self.list_statements.append(sql)
        return [dict(row) for row in self.rows]

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        _ = (sql, params)
        return {"n": len(self.rows)}


@pytest.fixture
def gold_leads() -> Iterator[_SqlClient]:
    """Serve GET /api/leads from the REAL Databricks repository over fake rows."""
    sql = _SqlClient(
        [
            _gold_row("B-AAAAAAAAAAAA1", "2026-09-20T08:00:00.000Z"),
            # Newest, but stated in another zone: the header must say it in UTC.
            _gold_row("B-AAAAAAAAAAAA2", "2026-09-21T09:30:00+02:00"),
            _gold_row("B-AAAAAAAAAAAA3", None),
        ]
    )
    repo = DatabricksLeadRepository(sql, cache_ttl_s=300)  # type: ignore[arg-type]
    previous = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: repo
    try:
        yield sql
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = previous


def test_header_is_the_newest_row_refresh_in_utc(gold_leads: _SqlClient) -> None:
    response = client.get("/api/leads")

    assert response.status_code == 200, response.text
    assert response.headers[HEADER] == "2026-09-21T07:30:00Z"
    # The statement that ran projects the column the header is computed from.
    assert re.search(r"\b(lp|b)\.refreshed_at\s+FROM\b", gold_leads.list_statements[0])


def test_both_lead_projections_end_with_refreshed_at() -> None:
    # Appended LAST so no positional consumer of the projection shifts.
    assert _LEAD_POPULATION_SELECT_FROM_LP.endswith("lp.refreshed_at")
    assert _LEAD_POPULATION_SELECT_FROM_B360.endswith("b.refreshed_at")


def test_the_value_never_reaches_a_json_body(gold_leads: _SqlClient) -> None:
    rows = client.get("/api/leads").json()

    assert [row["borrower_id"] for row in rows] == [
        "B-AAAAAAAAAAAA1",
        "B-AAAAAAAAAAAA2",
        "B-AAAAAAAAAAAA3",
    ]
    for row in rows:
        assert "row_refreshed_at" not in row
        assert "refreshed_at" not in row


def test_borrower_dossier_body_carries_no_row_refreshed_at() -> None:
    # Borrower360 inherits LeadSummary, so the excluded field rides along
    # on the model and must stay out of the dossier body too.
    borrower_id = mock_data.BORROWERS[0].borrower_id

    response = client.get(f"/api/borrowers/{borrower_id}")

    assert response.status_code == 200, response.text
    assert "row_refreshed_at" not in response.json()


def test_the_value_survives_the_ttl_cache_and_hydration(gold_leads: _SqlClient) -> None:
    first = client.get("/api/leads")
    second = client.get("/api/leads")

    # The second read is the repository's cached copy, hydrated again.
    assert len(gold_leads.list_statements) == 1
    assert first.headers[HEADER] == second.headers[HEADER] == "2026-09-21T07:30:00Z"


def test_header_absent_when_no_row_has_a_value(gold_leads: _SqlClient) -> None:
    gold_leads.rows = [_gold_row("B-AAAAAAAAAAAA1", None), _gold_row("B-AAAAAAAAAAAA2", None)]

    response = client.get("/api/leads?limit=2")

    assert response.status_code == 200, response.text
    assert HEADER not in response.headers


def test_a_crlf_value_is_refused_and_never_fails_the_list(gold_leads: _SqlClient) -> None:
    gold_leads.rows = [
        _gold_row("B-AAAAAAAAAAAA1", "2026-09-20T08:00:00Z\r\nX-Injected: 1"),
        _gold_row("B-AAAAAAAAAAAA2", "2026-09-19T08:00:00Z"),
    ]

    response = client.get("/api/leads?limit=2")

    assert response.status_code == 200, response.text
    assert "X-Injected" not in response.headers
    # The refused row contributes nothing; the clean row still states freshness.
    assert response.headers[HEADER] == "2026-09-19T08:00:00Z"


def _lead(refreshed: object) -> LeadSummary:
    base = mock_data.BORROWERS[0]
    return LeadSummary(
        **{
            key: value
            for key, value in base.model_dump().items()
            if key in LeadSummary.model_fields
        },
        row_refreshed_at=refreshed,
    )


def test_header_formatter_normalizes_naive_and_offset_stamps() -> None:
    naive = datetime(2026, 9, 21, 7, 0, 0)
    offset = datetime(2026, 9, 21, 9, 45, 0, tzinfo=timezone(timedelta(hours=2)))

    assert _data_refreshed_at_header([_lead(naive), _lead(offset)]) == "2026-09-21T07:45:00Z"
    assert _data_refreshed_at_header([_lead(naive)]) == "2026-09-21T07:00:00Z"
    assert _data_refreshed_at_header([_lead(None)]) is None
    assert _data_refreshed_at_header([]) is None


def test_malformed_values_are_dropped_on_the_model() -> None:
    assert _lead("not a timestamp").row_refreshed_at is None
    assert _lead("2026-09-20T08:00:00Z\nX: y").row_refreshed_at is None
    assert _lead(12345).row_refreshed_at is None
    assert _lead("2026-09-20T08:00:00Z").row_refreshed_at == datetime(2026, 9, 20, 8, tzinfo=UTC)
    # exclude=True: no serializer path carries it.
    assert "row_refreshed_at" not in _lead("2026-09-20T08:00:00Z").model_dump()
    assert "row_refreshed_at" not in _lead("2026-09-20T08:00:00Z").model_dump_json()
