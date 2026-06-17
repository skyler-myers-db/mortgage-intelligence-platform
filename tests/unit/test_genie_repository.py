"""Unit tests for ``DatabricksGenieRepository`` degraded/live behavior.

Contract under test:

1. Breaker CLOSED + happy path: calls ``ResilientGenieClient.ask`` and
   adapts the response to ``GenieMessageResponse(source="genie")``.
2. Breaker OPEN: returns the honest "warming up" message with
   ``source="degraded"``. No local answer catalog, borrower rows, counts,
   or source claims are served while Genie reconnects.
3. Breaker OPEN + unknown question: also returns the honest "warming
   up" message with ``source="degraded"``, never fabricated data.
4. ``DependencyDownError`` bubbled from the client with the breaker
   just opening: returns the same degraded message.
5. ``GenieClientError`` from the live client (401, 500, malformed JSON)
   is re-raised -- it is NOT silently masked by a catalog answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_client import GenieClientError, GenieResponse
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ITM_TOP_ZIPS_SQL,
)
from backend.services.repositories.databricks_repo import (
    DatabricksGenieRepository,
)
from backend.services.resilience import (
    CircuitBreaker,
    DependencyDownError,
    Resilient,
)


class _StubClient:
    """Minimal ``ResilientGenieClient`` shape for the repository.

    We don't use the real ``Resilient`` machinery here because these
    tests want to simulate the breaker state directly; the real
    wrapper is exercised in ``test_genie_client.py``.
    """

    def __init__(self, breaker: CircuitBreaker, response: Any) -> None:
        self._breaker = breaker
        self._response = response
        self.ask_calls: list[str] = []
        self.ask_conversation_ids: list[str | None] = []
        self._response_index = 0

    class _ResilientView:
        def __init__(self, breaker: CircuitBreaker) -> None:
            self.breaker = breaker

    @property
    def resilient(self) -> _StubClient._ResilientView:
        return _StubClient._ResilientView(self._breaker)

    def ask(self, question: str, conversation_id: str | None = None) -> Any:  # noqa: ARG002
        self.ask_calls.append(question)
        self.ask_conversation_ids.append(conversation_id)
        response = self._response
        if isinstance(response, list):
            response = response[min(self._response_index, len(response) - 1)]
            self._response_index += 1
        if isinstance(response, Exception):
            raise response
        return response


class _StubSqlClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.parameters: list[Any] = []

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:  # noqa: ARG002
        self.statements.append(statement)
        self.parameters.append(parameters)
        return self.rows

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:  # noqa: ARG002
        self.statements.append(statement)
        self.parameters.append(parameters)
        return self.rows[0] if self.rows else None


def _make_breaker(state: str = "closed") -> CircuitBreaker:
    cb = CircuitBreaker(
        "genie",
        failure_threshold=1,
        cooldown_s=60.0,
    )
    if state == "open":
        cb.record_failure()
    return cb


# ---------------------------------------------------------------------------
# Happy path: breaker closed, real Genie returns a live answer.
# ---------------------------------------------------------------------------


def test_breaker_closed_calls_live_genie_and_stamps_source() -> None:
    live = GenieResponse(
        answer_text="123 borrowers are currently in the money.",
        sql_query="SELECT count(*) FROM mip.gold.lead_scores WHERE in_the_money",
        sql_result_rows=[{"count": 123}],
        conversation_id="conv-abc",
        message_id="msg-1",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are in the money?")

    assert isinstance(result, GenieMessageResponse)
    assert result.source == "genie"
    assert result.question == "How many borrowers are in the money?"
    assert "123" in result.answer
    assert result.table_rows == [{"count": 123}]
    assert "mip.gold.lead_scores" in result.trusted_assets
    assert result.message_id == "msg-1"
    assert result.question_hash
    assert result.sql_query == live.sql_query
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.proof.row_count == 1
    assert result.visualization is not None
    assert result.actions
    # Live call must have been made.
    assert stub.ask_calls == ["How many borrowers are in the money?"]


def test_zip_rows_plan_zip_as_dimension_not_numeric_measure() -> None:
    live = GenieResponse(
        answer_text="ZIP 60617 has the most in-the-money borrowers.",
        sql_query=(
            "SELECT zip, state, COUNT(*) AS borrowers, AVG(opportunity_score) AS avg_score "
            "FROM mip.gold.borrower_360 GROUP BY zip, state"
        ),
        sql_result_rows=[
            {"zip": 60617, "state": "IL", "borrowers": 1503, "avg_score": 60.3},
            {"zip": 60628, "state": "IL", "borrowers": 1482, "avg_score": 60.3},
            {"zip": 60629, "state": "IL", "borrowers": 1387, "avg_score": 59.0},
        ],
        conversation_id="conv-zip",
        message_id="msg-zip",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which zips have the most in-the-money refi candidates?")

    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert result.visualization.x == "zip"
    assert result.visualization.y == "borrowers"


def test_zip_rows_prefer_in_the_money_borrowers_over_avg_score() -> None:
    live = GenieResponse(
        answer_text="ZIP 60617 has the most in-the-money borrowers.",
        sql_query=(
            "SELECT zip, state, COUNT(*) AS in_the_money_borrowers, "
            "AVG(opportunity_score) AS avg_score "
            "FROM mip.gold.borrower_360 GROUP BY zip, state"
        ),
        sql_result_rows=[
            {
                "zip": "60617",
                "state": "IL",
                "in_the_money_borrowers": 1503,
                "avg_score": 60.3,
            },
            {
                "zip": "60628",
                "state": "IL",
                "in_the_money_borrowers": 1482,
                "avg_score": 60.3,
            },
        ],
        conversation_id="conv-zip-itm",
        message_id="msg-zip-itm",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which zips have the most in-the-money refi candidates?")

    assert result.visualization is not None
    assert result.visualization.x == "zip"
    assert result.visualization.y == "in_the_money_borrowers"


def test_zip_rollup_open_cohort_action_routes_to_filtered_lead_queue() -> None:
    live = GenieResponse(
        answer_text="ZIP 60617 has the most in-the-money borrowers.",
        sql_query=(
            "SELECT zip, state, COUNT(*) AS in_the_money_borrowers "
            "FROM mip.gold.borrower_360 WHERE array_contains(segment_codes, 'itm') "
            "GROUP BY zip, state ORDER BY in_the_money_borrowers DESC LIMIT 10"
        ),
        sql_result_rows=[
            {"zip": "60617", "state": "IL", "in_the_money_borrowers": 1503},
            {"zip": "60628", "state": "IL", "in_the_money_borrowers": 1482},
        ],
        conversation_id="conv-zip-action",
        message_id="msg-zip-action",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which ZIPs have the most in-the-money refinance candidates?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.route == "/lead-queue?zips=60617%2C60628&segment=itm"
    assert action.criteria["source"] == "genie"
    assert action.criteria["result_filters"] == {
        "zips": ["60617", "60628"],
        "segment_codes": ["itm"],
        "segment_mode": "any",
    }
    assert action.criteria["sql_hash"]


def test_canonical_itm_zip_cohort_uses_lead_queue_visible_population() -> None:
    assert "FROM mip.gold.lead_population" in _CANONICAL_ITM_TOP_ZIPS_SQL
    assert "array_contains(segment_codes, 'itm')" in _CANONICAL_ITM_TOP_ZIPS_SQL
    assert "marketing_eligible = TRUE" in _CANONICAL_ITM_TOP_ZIPS_SQL
    assert "consent_status = 'opt_in'" in _CANONICAL_ITM_TOP_ZIPS_SQL
    assert "FROM mip.gold.borrower_360" not in _CANONICAL_ITM_TOP_ZIPS_SQL


def test_open_cohort_action_preserves_sql_derived_portfolio_predicates() -> None:
    live = GenieResponse(
        answer_text="ZIP 60617 has the most borrowers.",
        sql_query=(
            "SELECT zip, state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 "
            "WHERE is_owner_occupied = TRUE "
            "AND COALESCE(second_pos_amount, 0) > 0 "
            "GROUP BY zip, state ORDER BY borrowers DESC LIMIT 10"
        ),
        sql_result_rows=[
            {"zip": "60617", "state": "IL", "borrowers": 1503},
        ],
        conversation_id="conv-sql-criteria",
        message_id="msg-sql-criteria",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which ZIPs have the most candidates?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.criteria["result_filters"] == {
        "zips": ["60617"],
        "portfolio_criteria": {
            "occupancy": "Owner-occupied",
            "lien_status": "Open HELOC",
        },
    }


def test_cash_out_question_routes_to_offer_filter_not_equity_segment() -> None:
    live = GenieResponse(
        answer_text="Illinois has the most cash-out borrowers.",
        sql_query=(
            "SELECT state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 "
            "WHERE recommended_offer_code = 'cash_out' "
            "GROUP BY state ORDER BY borrowers DESC LIMIT 1"
        ),
        sql_result_rows=[
            {"state": "IL", "borrowers": 1114730},
        ],
        conversation_id="conv-cash-out",
        message_id="msg-cash-out",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which state has the most cash-out opportunity right now?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.route == "/lead-queue?states=IL&product=Cash-out"
    assert action.criteria["result_filters"] == {
        "states": ["IL"],
        "portfolio_criteria": {"product": "Cash-out"},
    }


def test_singular_state_ranked_question_routes_to_top_state_only() -> None:
    live = GenieResponse(
        answer_text="Illinois has the most cash-out borrowers.",
        sql_query=(
            "SELECT state, COUNT(*) AS cash_out_borrowers "
            "FROM mip.gold.borrower_360 "
            "WHERE recommended_offer_code = 'cash_out' "
            "GROUP BY state ORDER BY cash_out_borrowers DESC LIMIT 10"
        ),
        sql_result_rows=[
            {"state": "IL", "cash_out_borrowers": 1114730},
            {"state": "CA", "cash_out_borrowers": 845000},
            {"state": "FL", "cash_out_borrowers": 621000},
        ],
        conversation_id="conv-cash-out-states",
        message_id="msg-cash-out-states",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which state has the most cash-out opportunity right now?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.route == "/lead-queue?states=IL&product=Cash-out"
    assert action.criteria["result_filters"] == {
        "states": ["IL"],
        "portfolio_criteria": {"product": "Cash-out"},
    }


def test_cash_out_state_text_only_answer_uses_canonical_trusted_sql() -> None:
    live = GenieResponse(
        answer_text="Illinois has about 1.1 million cash-out opportunities.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="conv-cash-out-canonical",
        message_id="msg-cash-out-canonical",
    )
    stub = _StubClient(_make_breaker("closed"), response=[live, live])
    sql = _StubSqlClient(
        [
            {
                "state": "IL",
                "cash_out_borrowers": 1124230,
                "refreshed_at": "2026-05-21T00:00:00Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql_client=sql)  # type: ignore[arg-type]

    result = repo.respond("Which state has the most cash-out opportunity right now?")

    assert result.source == "trusted_sql"
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.sql_query is not None
    assert "recommended_offer_code = 'cash_out'" in result.sql_query
    assert "GROUP BY state" in result.sql_query
    assert result.table_rows == [
        {
            "state": "IL",
            "cash_out_borrowers": 1124230,
            "refreshed_at": "2026-05-21T00:00:00Z",
        }
    ]
    action = next(row for row in result.actions if row.id == "open-cohort")
    assert action.route == "/lead-queue?states=IL&product=Cash-out"


def test_top_borrowers_by_state_uses_canonical_lead_population_rows() -> None:
    live = GenieResponse(
        answer_text="The top borrower has score 91, but no rows were attached.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="conv-top-state",
        message_id="msg-top-state",
    )
    rows = [
        {
            "borrower_id": f"B-IL{i:03d}",
            "display_name": f"Borrower {i}",
            "city": "Chicago",
            "state": "IL",
            "zip": "60617",
            "lead_score": 90 - i,
            "recommended_offer_code": "refi",
            "recommended_offer": "Rate Refi",
            "rank_within_state": i + 1,
            "refreshed_at": "2026-05-21T00:00:00Z",
        }
        for i in range(10)
    ]
    stub = _StubClient(_make_breaker("closed"), response=[live, live])
    sql = _StubSqlClient(rows)
    repo = DatabricksGenieRepository(stub, sql_client=sql)  # type: ignore[arg-type]

    result = repo.respond("Show me the top 10 borrowers by lead score in Illinois.")

    assert result.source == "trusted_sql"
    assert result.trusted_assets == ["mip.gold.lead_population"]
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.sql_query is not None
    assert "FROM mip.gold.lead_population" in result.sql_query
    assert "state = :state" in result.sql_query
    assert sql.parameters == [{"state": "IL"}]
    assert result.row_count == 10
    assert result.table_rows == rows
    assert "masked borrower B-IL000" in result.answer


def test_heloc_zip_text_only_answer_uses_equity_canonical_trusted_sql() -> None:
    live = GenieResponse(
        answer_text="Genie found HELOC ZIP opportunities, but did not attach SQL.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="conv-heloc-canonical",
        message_id="msg-heloc-canonical",
    )
    stub = _StubClient(_make_breaker("closed"), response=[live, live])
    rows = [
        {
            "zip": "60617",
            "state": "IL",
            "heloc_eligible_borrowers": 1000,
            "avg_equity_pct": 42.0,
            "avg_score": 71.1,
            "refreshed_at": "2026-05-21T00:00:00Z",
        },
        {
            "zip": "60628",
            "state": "IL",
            "heloc_eligible_borrowers": 900,
            "avg_equity_pct": 40.5,
            "avg_score": 70.2,
            "refreshed_at": "2026-05-21T00:00:00Z",
        },
        {
            "zip": "60629",
            "state": "IL",
            "heloc_eligible_borrowers": 800,
            "avg_equity_pct": 39.7,
            "avg_score": 69.8,
            "refreshed_at": "2026-05-21T00:00:00Z",
        },
        {
            "zip": "77084",
            "state": "TX",
            "heloc_eligible_borrowers": 700,
            "avg_equity_pct": 38.4,
            "avg_score": 68.9,
            "refreshed_at": "2026-05-21T00:00:00Z",
        },
        {
            "zip": "33186",
            "state": "FL",
            "heloc_eligible_borrowers": 600,
            "avg_equity_pct": 37.2,
            "avg_score": 67.5,
            "refreshed_at": "2026-05-21T00:00:00Z",
        },
    ]
    sql = _StubSqlClient(rows)
    repo = DatabricksGenieRepository(stub, sql_client=sql)  # type: ignore[arg-type]

    result = repo.respond("Which ZIPs have the most HELOC-eligible borrowers with equity >= 35%?")

    assert result.source == "trusted_sql"
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.sql_query is not None
    assert "equity_pct >= 35" in result.sql_query
    assert "GROUP BY zip, state" in result.sql_query
    assert result.row_count == 5
    assert result.table_rows == rows
    assert "Building Permits signals remain pending" in result.answer


def test_numeric_zip_rows_are_padded_before_cohort_routing() -> None:
    live = GenieResponse(
        answer_text="ZIP 02139 has the most borrowers.",
        sql_query=(
            "SELECT zip, state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 GROUP BY zip, state"
        ),
        sql_result_rows=[
            {"zip": 2139, "state": "MA", "borrowers": 12},
            {"zip": "60617", "state": "IL", "borrowers": 10},
        ],
        conversation_id="conv-zip-padding",
        message_id="msg-zip-padding",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which ZIPs have the most borrowers?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.criteria["result_filters"]["zips"] == ["02139", "60617"]
    assert "zips=02139%2C60617" in action.route


def test_county_fips_rows_route_to_county_filtered_lead_queue() -> None:
    live = GenieResponse(
        answer_text="Broward County has the most borrowers.",
        sql_query=(
            "SELECT county_fips_5, state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 GROUP BY county_fips_5, state"
        ),
        sql_result_rows=[
            {"county_fips_5": "12011", "state": "FL", "borrowers": 308},
        ],
        conversation_id="conv-county-route",
        message_id="msg-county-route",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which counties have the most in-the-money borrowers?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.criteria["result_filters"]["county"] == "12011"
    assert action.route == "/lead-queue?county=12011&segment=itm"


def test_multiple_county_rows_preserve_all_counties_for_cohort_action() -> None:
    live = GenieResponse(
        answer_text="Broward and Cook have the most borrowers.",
        sql_query=(
            "SELECT county_fips_5, state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 GROUP BY county_fips_5, state"
        ),
        sql_result_rows=[
            {"county_fips_5": "12011", "state": "FL", "borrowers": 308},
            {"county_fips_5": "17031", "state": "IL", "borrowers": 250},
        ],
        conversation_id="conv-county-list-action",
        message_id="msg-county-list-action",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which counties have the most in-the-money refinance candidates?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.criteria["result_filters"]["counties"] == ["12011", "17031"]
    assert action.route == "/lead-queue?counties=12011%2C17031&segment=itm"


def test_non_replayable_aggregate_answer_does_not_offer_lead_queue_action() -> None:
    live = GenieResponse(
        answer_text="There are 5,156,184 borrowers in the population.",
        sql_query="SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360",
        sql_result_rows=[{"borrowers": 5_156_184}],
        conversation_id="conv-aggregate",
        message_id="msg-aggregate",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are in the population?")

    assert all(action.id != "open-cohort" for action in result.actions)
    assert all(action.id != "create-campaign-draft" for action in result.actions)


def test_zip_rollup_open_cohort_preserves_answer_zip_filters() -> None:
    rows = [
        {"zip": f"{60000 + i:05d}", "state": "IL", "in_the_money_borrowers": 1000 - i}
        for i in range(40)
    ]
    live = GenieResponse(
        answer_text="Many ZIPs qualify.",
        sql_query=(
            "SELECT zip, state, COUNT(*) AS in_the_money_borrowers "
            "FROM mip.gold.borrower_360 WHERE array_contains(segment_codes, 'itm') "
            "GROUP BY zip, state ORDER BY in_the_money_borrowers DESC"
        ),
        sql_result_rows=rows,
        conversation_id="conv-many-zips",
        message_id="msg-many-zips",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which ZIPs have the most in-the-money refinance candidates?")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.route is not None
    assert str(action.route).count("%2C") == 39
    assert len(action.criteria["result_filters"]["zips"]) == 40


def test_borrower_rows_open_cohort_action_routes_to_exact_borrower_ids() -> None:
    live = GenieResponse(
        answer_text="Here are the highest-score borrowers.",
        sql_query=(
            "SELECT borrower_id, city, state, zip, opportunity_score "
            "FROM mip.gold.borrower_360 ORDER BY opportunity_score DESC LIMIT 2"
        ),
        sql_result_rows=[
            {
                "borrower_id": "B-11111",
                "city": "Seattle",
                "state": "WA",
                "zip": "98118",
                "opportunity_score": 92,
            },
            {
                "borrower_id": "B-22222",
                "city": "Chicago",
                "state": "IL",
                "zip": "60617",
                "opportunity_score": 91,
            },
        ],
        conversation_id="conv-borrower-action",
        message_id="msg-borrower-action",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Show the top borrowers by score.")
    action = next(row for row in result.actions if row.id == "open-cohort")

    assert action.route == "/lead-queue?borrower_ids=B-11111%2CB-22222"
    assert action.borrower_ids == ["B-11111", "B-22222"]
    assert action.criteria["result_filters"] == {
        "borrower_ids": ["B-11111", "B-22222"],
    }


def test_data_question_without_query_gets_generic_sql_repair() -> None:
    text_only = GenieResponse(
        answer_text="The top ZIP is 60617.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="stale-conv",
        message_id="msg-stale",
    )
    repaired = GenieResponse(
        answer_text="ZIP 60617 has the most in-the-money refinance candidates.",
        sql_query=(
            "SELECT zip, state, COUNT(*) AS in_the_money_borrowers "
            "FROM mip.gold.borrower_360 "
            "WHERE in_the_money = TRUE "
            "GROUP BY zip, state ORDER BY in_the_money_borrowers DESC LIMIT 10"
        ),
        sql_result_rows=[
            {"zip": 60617, "state": "IL", "in_the_money_borrowers": 1503},
            {"zip": 60628, "state": "IL", "in_the_money_borrowers": 1482},
        ],
        conversation_id="repair-conv",
        message_id="repair-msg",
        trusted_assets=["mip.gold.borrower_360"],
    )
    stub = _StubClient(_make_breaker("closed"), response=[text_only, repaired])
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond(
        "Which zips have the most in-the-money refi candidates?",
        conversation_id="stale-conv",
    )

    assert result.source == "genie"
    assert result.conversation_id == "stale-conv"
    assert len(stub.ask_calls) == 2
    assert stub.ask_conversation_ids == ["stale-conv", "stale-conv"]
    assert stub.ask_calls[0] == "Which zips have the most in-the-money refi candidates?"
    assert (
        "Regenerate the following Mortgage Intelligence Platform data question" in stub.ask_calls[1]
    )
    assert result.sql_query is not None
    assert "FROM mip.gold.borrower_360" in result.sql_query
    assert "GROUP BY zip, state" in result.sql_query
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert result.visualization.x == "zip"
    assert result.visualization.y == "in_the_money_borrowers"
    assert result.table_rows is not None
    assert result.table_rows[0]["zip"] == 60617


def test_top_zip_question_uses_direct_canonical_gold_sql_without_genie_call() -> None:
    text_only = GenieResponse(
        answer_text="The top ZIP is 60617.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="stale-conv",
        message_id="msg-stale",
    )
    still_text_only = GenieResponse(
        answer_text="The answer is still ZIP 60617, but no query was attached.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="repair-conv",
        message_id="repair-msg",
    )
    stub = _StubClient(_make_breaker("closed"), response=[text_only, still_text_only])
    sql = _StubSqlClient(
        [
            {
                "zip": "60617",
                "state": "IL",
                "in_the_money_borrowers": 1503,
                "avg_score": 60.3,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            },
            {
                "zip": "60628",
                "state": "IL",
                "in_the_money_borrowers": 1482,
                "avg_score": 60.3,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            },
        ],
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("Which zips have the most in-the-money refi candidates?")

    assert result.source == "trusted_sql"
    assert stub.ask_calls == []
    assert result.conversation_id == ""
    assert result.sql_query is not None
    assert "FROM mip.gold.lead_population" in result.sql_query
    assert "marketing_eligible = TRUE" in result.sql_query
    assert "GROUP BY zip, state" in result.sql_query
    assert sql.statements == [result.sql_query]
    assert result.table_rows is not None
    assert result.table_rows[0]["zip"] == "60617"
    assert result.visualization is not None
    assert result.visualization.x == "zip"
    assert result.visualization.y == "in_the_money_borrowers"
    assert result.actions[0].route == "/lead-queue?zips=60617%2C60628&segment=itm"
    assert result.actions[0].criteria["source"] == "trusted_sql"
    assert result.actions[0].criteria["result_filters"] == {
        "zips": ["60617", "60628"],
        "segment_codes": ["itm"],
        "segment_mode": "any",
    }
    assert result.proof is not None
    assert result.proof.trusted is True


@pytest.mark.parametrize(
    ("question", "rows", "asset", "sql_marker", "expected_phrase"),
    [
        (
            "Show me the top 10 borrowers by lead score in Illinois.",
            [
                {
                    "borrower_id": "B-IL-1",
                    "display_name": "Borrower IL-1",
                    "city": "Chicago",
                    "state": "IL",
                    "zip": "60617",
                    "lead_score": 98,
                    "recommended_offer_code": "refi",
                    "recommended_offer": "Rate refinance",
                    "rank_within_state": 1,
                    "refreshed_at": "2026-06-15T00:00:00Z",
                }
            ],
            "mip.gold.lead_population",
            "FROM mip.gold.lead_population",
            "ranked the top",
        ),
        (
            "Top 5 ZIP codes by HELOC-eligible borrowers (equity >= 35%).",
            [
                {
                    "zip": "60617",
                    "state": "IL",
                    "heloc_eligible_borrowers": 321,
                    "avg_equity_pct": 48.2,
                    "avg_score": 78.4,
                    "refreshed_at": "2026-06-15T00:00:00Z",
                }
            ],
            "mip.gold.borrower_360",
            "equity_pct >= 35",
            "HELOC-eligible borrowers",
        ),
        (
            "Where should the configured tenant lender spend its next 10000 outreach touches this week, and why?",
            [
                {
                    "state": "IL",
                    "segment_code": "itm",
                    "marketable_borrowers": 1200,
                    "avg_score": 91.2,
                    "leading_offer_code": "refi",
                    "leading_recommended_offer": "Rate refinance",
                    "leading_offer_borrowers": 1180,
                    "refreshed_at": "2026-06-15T00:00:00Z",
                }
            ],
            "mip.gold.borrower_360",
            "exploded_segments",
            "state, segment, and offer",
        ),
        (
            "Which state has the most cash-out opportunity right now?",
            [
                {
                    "state": "FL",
                    "cash_out_borrowers": 456,
                    "refreshed_at": "2026-06-15T00:00:00Z",
                }
            ],
            "mip.gold.borrower_360",
            "recommended_offer_code = 'cash_out'",
            "cash-out opportunity",
        ),
        (
            "Show the Investor / Multi-Property segment broken down by state.",
            [
                {
                    "segment_code": "investor",
                    "state": "CA",
                    "investor_borrowers": 789,
                    "avg_score": 76,
                    "delta_vs_prior": "+2%",
                    "refreshed_at": "2026-06-15T00:00:00Z",
                }
            ],
            "mip.gold.segment_population",
            "segment_code = 'investor'",
            "Investor / Multi-Property segment",
        ),
        (
            "Compare mean lead score by MSA for our top five markets.",
            [
                {
                    "market": "Chicago, IL (CBSA 16980)",
                    "msa_cbsa_code": "16980",
                    "borrowers": 5000,
                    "avg_score": 82.1,
                    "refreshed_at": "2026-06-15T00:00:00Z",
                }
            ],
            "mip.gold.borrower_360",
            "situs_cbsa_code",
            "situs_cbsa_code",
        ),
    ],
)
def test_eval_canonical_questions_use_direct_trusted_sql_without_genie_call(
    question: str,
    rows: list[dict[str, Any]],
    asset: str,
    sql_marker: str,
    expected_phrase: str,
) -> None:
    stub = _StubClient(
        _make_breaker("closed"),
        response=DependencyDownError("genie", reason="test should not call Genie"),
    )
    sql = _StubSqlClient(rows)
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond(question)

    assert result.source == "trusted_sql"
    assert stub.ask_calls == []
    assert result.trusted_assets == [asset] or asset in result.trusted_assets
    assert result.sql_query is not None
    assert sql_marker in result.sql_query
    assert expected_phrase in result.answer
    assert result.table_rows == rows
    assert result.proof is not None
    assert result.proof.trusted is True


def test_city_count_question_uses_direct_trusted_sql_without_genie_call() -> None:
    stub = _StubClient(
        _make_breaker("closed"),
        response=DependencyDownError("genie", reason="test should not call Genie"),
    )
    sql = _StubSqlClient(
        [
            {
                "in_the_money_borrowers": 17,
                "refreshed_at": "2026-06-15T00:00:00Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("How many in-the-money borrowers in Chicago?")

    assert result.source == "trusted_sql"
    assert stub.ask_calls == []
    assert result.metric_value == "17"
    assert result.sql_query is not None
    assert "LOWER(city) = LOWER(:city)" in result.sql_query
    assert sql.parameters == [{"city": "Chicago"}]
    assert result.proof is not None
    assert result.proof.trusted is True


@pytest.mark.parametrize(
    ("column", "question", "expected_kind"),
    [
        ("zip", "Which ZIPs have the most in-the-money refi candidates?", "bar"),
        ("zip_code", "Which ZIP codes have the most in-the-money borrowers?", "bar"),
        ("fips_5", "Compare borrower counts by county FIPS.", "bar"),
        ("county_fips_5", "Compare borrower counts by county FIPS.", "bar"),
        ("cbsa_code", "Compare borrower counts by CBSA.", "bar"),
        ("msa_cbsa_code", "Compare borrower counts by MSA CBSA.", "bar"),
        ("borrower_id", "Show the highest-score borrowers.", "borrower_list"),
        ("id", "Show counts by id.", "bar"),
    ],
)
def test_identifier_columns_are_never_treated_as_measures(
    column: str,
    question: str,
    expected_kind: str,
) -> None:
    live = GenieResponse(
        answer_text="Identifier rollup.",
        sql_query=f"SELECT {column}, COUNT(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY {column}",
        sql_result_rows=[
            {column: 1234, "borrowers": 10},
            {column: 5678, "borrowers": 7},
        ],
        conversation_id="conv-id-matrix",
        message_id="msg-id-matrix",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond(question)

    assert result.visualization is not None
    assert result.visualization.kind == expected_kind
    assert result.visualization.x == column
    assert result.visualization.y == "borrowers"


def test_postal_code_rows_plan_postal_column_as_dimension() -> None:
    live = GenieResponse(
        answer_text="Postal code 60617 leads.",
        sql_query=(
            "SELECT postal_code, state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 GROUP BY postal_code, state"
        ),
        sql_result_rows=[
            {"postal_code": 60617, "state": "IL", "borrowers": 1503},
            {"postal_code": 60628, "state": "IL", "borrowers": 1482},
        ],
        conversation_id="conv-postal",
        message_id="msg-postal",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which postal codes have the most borrowers?")

    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert result.visualization.x == "postal_code"
    assert result.visualization.y == "borrowers"


def test_county_question_prefers_county_fips_over_state_dimension() -> None:
    live = GenieResponse(
        answer_text="Top counties.",
        sql_query=(
            "SELECT county_fips_5, state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 GROUP BY county_fips_5, state"
        ),
        sql_result_rows=[
            {"county_fips_5": "12011", "state": "FL", "borrowers": 308},
            {"county_fips_5": "17031", "state": "IL", "borrowers": 250},
        ],
        conversation_id="conv-county-chart",
        message_id="msg-county-chart",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which counties have the most in-the-money borrowers?")

    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert result.visualization.x == "county_fips_5"
    assert result.visualization.y == "borrowers"


def test_raw_clip_identifier_is_policy_blocked_not_charted() -> None:
    live = GenieResponse(
        answer_text="CLIP rollup.",
        sql_query="SELECT clip, COUNT(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY clip",
        sql_result_rows=[{"clip": "9154364327", "borrowers": 10}],
        conversation_id="conv-clip-policy",
        message_id="msg-clip-policy",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Show borrower CLIP counts by CLIP.")

    assert result.source == "policy_blocked"
    assert result.visualization is None
    assert result.table_rows == []


@pytest.mark.parametrize(
    "sql_query",
    [
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE clip = '9154364327'",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE b.clip IN ('9154364327')",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE owner_link_id = 1100000134187756",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE `clip` = '9154364327'",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 b WHERE b.`clip` IN ('9154364327')",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE clip = CAST('9154364327' AS STRING)",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE `borrower_identifier` = '1234567890'",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE `owner_1_identifier` = '1100000134187756'",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE CAST(clip AS STRING) = '9154364327'",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE TRIM(clip) = '9154364327'",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE '9154364327' = clip",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE clip BETWEEN '9154364327' AND '9154364328'",
        "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE clip LIKE '9154364327'",
    ],
)
def test_raw_cotality_identifier_literals_are_policy_blocked(sql_query: str) -> None:
    live = GenieResponse(
        answer_text="One borrower matched.",
        sql_query=sql_query,
        sql_result_rows=[{"borrowers": 1}],
        conversation_id="conv-raw-id-literal",
        message_id="msg-raw-id-literal",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers match this raw identifier?")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.proof is not None
    assert result.proof.sql_query is None
    assert result.table_rows == []


@pytest.mark.parametrize(
    "answer_text",
    [
        "Owner Link 1100000134187756 has 2 related properties.",
        "Owner Link ID 1100000134187756 has 2 related properties.",
        "owner_1_identifier=1100000134187756 matched.",
        "Owner ID: 1100000134187756 matched.",
        "owner_id=1100000134187756 matched.",
        "OwnerID 1100000134187756 matched.",
        "OwnerLink ID 1100000134187756 matched.",
    ],
)
def test_raw_owner_identifier_answer_text_is_policy_blocked(answer_text: str) -> None:
    live = GenieResponse(
        answer_text=answer_text,
        sql_query="SELECT count(*) AS borrowers FROM mip.gold.borrower_360",
        sql_result_rows=[{"borrowers": 2}],
        conversation_id="conv-raw-owner-link-answer",
        message_id="msg-raw-owner-link-answer",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Tell me about Owner Link 1100000134187756.")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.table_rows == []
    assert "1100000134187756" not in result.answer


def test_zip_map_prompt_does_not_emit_state_map_without_state_column() -> None:
    live = GenieResponse(
        answer_text="ZIP 60617 leads.",
        sql_query="SELECT zip, COUNT(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY zip",
        sql_result_rows=[{"zip": 60617, "borrowers": 1503}, {"zip": 60628, "borrowers": 1482}],
        conversation_id="conv-zip-map",
        message_id="msg-zip-map",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Map the top ZIPs with the most in-the-money borrowers.")

    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert result.visualization.x == "zip"
    assert result.visualization.y == "borrowers"


def test_no_sql_or_assets_answer_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="There are many strong opportunities.",
        sql_query=None,
        sql_result_rows=[{"borrowers": 10}],
        conversation_id="conv-no-proof",
        message_id="msg-no-proof",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many opportunities do we have?")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.table_rows == []
    assert result.actions == []
    assert result.visualization is None
    assert result.proof is not None
    assert result.proof.trusted is False


def test_policy_blocked_permit_answer_keeps_pending_feed_visible() -> None:
    live = GenieResponse(
        answer_text="Here are permit-driven HELOC candidates.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="conv-permit-gap",
        message_id="msg-permit-gap",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Show HELOC candidates with recent permits and strong equity.")

    assert result.source == "policy_blocked"
    assert "Building Permits feed is pending" in result.answer
    assert result.proof is not None
    assert result.proof.known_data_gaps


def test_conversation_id_is_forwarded_to_live_genie() -> None:
    live = GenieResponse(
        answer_text="Follow-up answer.",
        sql_query=None,
        sql_result_rows=None,
        conversation_id="conv-existing",
        message_id="msg-2",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("why?", conversation_id="conv-existing")

    assert result.conversation_id == "conv-existing"
    assert stub.ask_calls == ["why?"]
    assert stub.ask_conversation_ids == ["conv-existing"]


def test_untrusted_sql_is_policy_blocked_and_not_rendered() -> None:
    live = GenieResponse(
        answer_text="Audit users by state.",
        sql_query=("SELECT count(*) FROM mip.gold.lead_scores " "JOIN mip_app.action_audit ON 1=1"),
        sql_result_rows=[{"count": 1}],
        conversation_id="conv-policy",
        message_id="msg-policy",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("join the app audit table")

    assert result.source == "policy_blocked"
    assert result.table_rows == []
    assert result.row_count == 0
    assert result.sql_query is None
    assert result.proof is not None
    assert result.proof.trusted is False
    assert result.proof.row_count == 0
    assert "mip_app.action_audit" in result.trusted_assets


def test_canonical_zip_question_prefers_direct_trusted_sql_over_untrusted_genie_sql() -> None:
    live = GenieResponse(
        answer_text="ZIP 60617 has the most in-the-money borrowers.",
        sql_query=(
            "SELECT zip, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 JOIN mip_app.action_audit ON 1=1 "
            "GROUP BY zip"
        ),
        sql_result_rows=[{"zip": "60617", "borrowers": 1503}],
        conversation_id="conv-canonical-policy",
        message_id="msg-canonical-policy",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient([{"zip": "60617", "state": "IL", "in_the_money_borrowers": 1503}])
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("Which ZIPs have the most in-the-money refinance candidates?")

    assert result.source == "trusted_sql"
    assert stub.ask_calls == []
    assert result.table_rows == [{"zip": "60617", "state": "IL", "in_the_money_borrowers": 1503}]
    assert result.sql_query is not None
    assert "FROM mip.gold.lead_population" in result.sql_query
    assert "marketing_eligible = TRUE" in result.sql_query
    assert "mip_app.action_audit" not in result.sql_query
    assert sql.statements == [result.sql_query]


def test_string_literal_asset_spoof_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="The trusted source is mip.gold.borrower_360.",
        sql_query="SELECT 'mip.gold.borrower_360' AS source_asset, 1 AS borrowers",
        sql_result_rows=[{"source_asset": "mip.gold.borrower_360", "borrowers": 1}],
        conversation_id="conv-literal-spoof",
        message_id="msg-literal-spoof",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show a spoofed trusted source")

    assert result.source == "policy_blocked"
    assert result.trusted_assets == []
    assert result.sql_query is None
    assert result.table_rows == []


def test_comment_spoof_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="Counts from the silver table.",
        sql_query=(
            "SELECT count(*) FROM mip.silver.mortgage_events " "/* mip.gold.borrower_360 */"
        ),
        sql_result_rows=[{"count": 1}],
        conversation_id="conv-comment-spoof",
        message_id="msg-comment-spoof",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show a comment spoof")

    assert result.source == "policy_blocked"
    assert result.trusted_assets == []
    assert result.sql_query is None
    assert result.table_rows == []


def test_multi_statement_sql_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="Borrower count.",
        sql_query=(
            "SELECT count(*) FROM mip.gold.borrower_360 LIMIT 1; "
            "DROP TABLE mip.gold.borrower_360"
        ),
        sql_result_rows=[{"count": 1}],
        conversation_id="conv-multi-statement",
        message_id="msg-multi-statement",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("try a multi statement")

    assert result.source == "policy_blocked"
    assert result.trusted_assets == []
    assert result.sql_query is None
    assert result.table_rows == []


def test_pii_column_sql_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="Alice has a high score.",
        sql_query="SELECT owner_name, score FROM mip.gold.borrower_360 LIMIT 5",
        sql_result_rows=[{"owner_name": "Alice", "score": 91}],
        conversation_id="conv-pii-sql",
        message_id="msg-pii-sql",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show borrower names")

    assert result.source == "policy_blocked"
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.sql_query is None
    assert result.table_rows == []
    assert "Alice" not in result.answer


@pytest.mark.parametrize(
    "sql_query",
    [
        "SELECT * FROM mip.gold.borrower_360 LIMIT 10",
        "SELECT b.* FROM mip.gold.borrower_360 b LIMIT 10",
        "SELECT b . * FROM mip.gold.borrower_360 b LIMIT 10",
        "SELECT `b`.* FROM mip.gold.borrower_360 b LIMIT 10",
        "SELECT `b` . * FROM mip.gold.borrower_360 b LIMIT 10",
        "SELECT * EXCEPT (state) FROM mip.gold.borrower_360 LIMIT 10",
        "SELECT b.* EXCEPT (state) FROM mip.gold.borrower_360 b LIMIT 10",
        "SELECT struct(*) AS raw_row FROM mip.gold.borrower_360 LIMIT 10",
        "SELECT struct(b.*) AS raw_row FROM mip.gold.borrower_360 b LIMIT 10",
        "SELECT to_json(struct(*)) AS raw_json FROM mip.gold.borrower_360 LIMIT 10",
        "SELECT `b-1`.* FROM mip.gold.borrower_360 AS `b-1` LIMIT 10",
        "SELECT state FROM (SELECT * FROM mip.gold.borrower_360) b LIMIT 10",
        "SELECT state FROM (SELECT b.* FROM mip.gold.borrower_360 b) x LIMIT 10",
        "SELECT raw_row FROM (SELECT struct(*) AS raw_row FROM mip.gold.borrower_360) x LIMIT 10",
        "SELECT * FROM mip.gold.evidence_events LIMIT 10",
    ],
)
def test_wildcard_trusted_sql_is_policy_blocked(sql_query: str) -> None:
    live = GenieResponse(
        answer_text="Wildcard rows.",
        sql_query=sql_query,
        sql_result_rows=[{"borrower_id": "B-1", "owner_link_id": "raw-owner"}],
        conversation_id="conv-wildcard-pii",
        message_id="msg-wildcard-pii",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show trusted rows")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.table_rows == []


def test_struct_wildcard_nested_pii_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="Nested row projection.",
        sql_query="SELECT struct(*) AS raw_row FROM mip.gold.borrower_360 LIMIT 10",
        sql_result_rows=[
            {
                "raw_row": {
                    "borrower_id": "B-1",
                    "owner_link_id": "raw-owner",
                    "street_address": "123 Main St",
                },
                "borrowers": 1,
            }
        ],
        conversation_id="conv-struct-wildcard",
        message_id="msg-struct-wildcard",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show trusted rows")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.table_rows == []


def test_count_star_inside_function_args_is_not_wildcard_blocked() -> None:
    live = GenieResponse(
        answer_text="There are 0.284 million eligible borrowers in this cohort.",
        sql_query=(
            "SELECT ROUND(COUNT(*) / 1000000.0, 3) AS eligible_borrowers_millions "
            "FROM mip.gold.lead_population"
        ),
        sql_result_rows=[{"eligible_borrowers_millions": "0.284"}],
        conversation_id="conv-count-star-function",
        message_id="msg-count-star-function",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the returned cohort in millions.")

    assert result.source == "genie"
    assert result.sql_query == live.sql_query
    assert result.table_rows == live.sql_result_rows


@pytest.mark.parametrize(
    "column",
    [
        "owner_1_full_name",
        "situs_street_address",
        "mailing_street_address",
        "owner_name_hash",
        "trigger_timeline_json",
    ],
)
def test_raw_gold_pii_column_sql_is_policy_blocked(column: str) -> None:
    live = GenieResponse(
        answer_text="Raw borrower detail.",
        sql_query=f"SELECT {column}, score FROM mip.gold.borrower_360 LIMIT 5",
        sql_result_rows=[{column: "raw value", "score": 91}],
        conversation_id="conv-raw-pii-sql",
        message_id="msg-raw-pii-sql",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond(f"show {column}")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.table_rows == []


def test_pii_answer_text_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="The top borrower email is raw@example.com.",
        sql_query="SELECT count(*) FROM mip.gold.borrower_360",
        sql_result_rows=[{"count": 1}],
        conversation_id="conv-pii-answer",
        message_id="msg-pii-answer",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show the top borrower email")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.table_rows == []
    assert "raw@example.com" not in result.answer


def test_trusted_genie_answer_with_matching_numeric_claim_passes() -> None:
    live = GenieResponse(
        answer_text="There are 123 borrowers in this trusted cohort.",
        sql_query="SELECT 123 AS borrowers FROM mip.gold.borrower_360",
        sql_result_rows=[{"borrowers": 123}],
        conversation_id="conv-numeric-match",
        message_id="msg-numeric-match",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the trusted cohort.")

    assert result.source == "genie"
    assert result.answer == live.answer_text
    assert result.table_rows == [{"borrowers": 123}]


def test_trusted_genie_answer_with_unsupported_numeric_claim_is_policy_blocked() -> None:
    live = GenieResponse(
        answer_text="There are 999 borrowers in this trusted cohort.",
        sql_query="SELECT 123 AS borrowers FROM mip.gold.borrower_360",
        sql_result_rows=[{"borrowers": 123}],
        conversation_id="conv-numeric-mismatch",
        message_id="msg-numeric-mismatch",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the trusted cohort.")

    assert result.source == "policy_blocked"
    assert result.sql_query is None
    assert result.table_rows == []
    assert result.visualization is None
    assert result.actions == []
    assert "999" not in result.answer
    assert result.proof is not None
    assert result.proof.trusted is False
    assert result.proof.known_data_gaps


def test_trusted_genie_numeric_check_accepts_rounded_percent_claim() -> None:
    live = GenieResponse(
        answer_text="The high-equity share is 17% of borrowers.",
        sql_query="SELECT 0.173 AS high_equity_share FROM mip.gold.borrower_360",
        sql_result_rows=[{"high_equity_share": 0.173}],
        conversation_id="conv-percent-rounded",
        message_id="msg-percent-rounded",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("What share of borrowers are high equity?")

    assert result.source == "genie"
    assert result.table_rows == [{"high_equity_share": 0.173}]


def test_trusted_genie_numeric_check_accepts_sum_across_rows() -> None:
    live = GenieResponse(
        answer_text="The two returned states contain 300 borrowers.",
        sql_query=("SELECT state, borrowers FROM mip.gold.borrower_360 GROUP BY state, borrowers"),
        sql_result_rows=[
            {"state": "IL", "borrowers": 100},
            {"state": "CA", "borrowers": 200},
        ],
        conversation_id="conv-sum-supported",
        message_id="msg-sum-supported",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the returned states.")

    assert result.source == "genie"
    assert result.row_count == 2


def test_trusted_genie_numeric_check_accepts_string_backed_row_numbers() -> None:
    live = GenieResponse(
        answer_text=(
            "This week's distribution peaks at score 50 with 47,089 borrowers "
            "and reaches 1 borrower at score 88."
        ),
        sql_query=(
            "SELECT opportunity_score, COUNT(*) AS borrower_count "
            "FROM mip.gold.lead_population GROUP BY opportunity_score"
        ),
        sql_result_rows=[
            {"opportunity_score": "50", "borrower_count": "47089"},
            {"opportunity_score": "88", "borrower_count": "1"},
        ],
        conversation_id="conv-string-numeric",
        message_id="msg-string-numeric",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Compare this week's lead score distribution to last week's.")

    assert result.source == "genie"
    assert result.table_rows == live.sql_result_rows


def test_trusted_genie_numeric_check_scales_word_suffix_claims() -> None:
    live = GenieResponse(
        answer_text="There are 1.2 million borrowers in this returned cohort.",
        sql_query="SELECT 1200000 AS borrowers FROM mip.gold.borrower_360",
        sql_result_rows=[{"borrowers": 1_200_000}],
        conversation_id="conv-word-suffix-supported",
        message_id="msg-word-suffix-supported",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the returned cohort.")

    assert result.source == "genie"
    assert result.table_rows == live.sql_result_rows


def test_trusted_genie_numeric_check_accepts_rounded_word_suffix_claims() -> None:
    live = GenieResponse(
        answer_text="There are 0.28 million eligible borrowers in this cohort.",
        sql_query="SELECT 284475 AS borrowers FROM mip.gold.borrower_360",
        sql_result_rows=[{"borrowers": 284_475}],
        conversation_id="conv-word-suffix-rounded",
        message_id="msg-word-suffix-rounded",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the returned cohort in millions.")

    assert result.source == "genie"
    assert result.table_rows == live.sql_result_rows


def test_trusted_genie_numeric_check_scales_unit_bearing_columns() -> None:
    live = GenieResponse(
        answer_text="There are 0.284 million eligible borrowers in this cohort.",
        sql_query=(
            "SELECT ROUND(COUNT(*) / 1000000.0, 3) AS eligible_borrowers_millions "
            "FROM mip.gold.lead_population"
        ),
        sql_result_rows=[{"eligible_borrowers_millions": "0.284"}],
        conversation_id="conv-unit-column-supported",
        message_id="msg-unit-column-supported",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the returned cohort in millions.")

    assert result.source == "genie"
    assert result.table_rows == live.sql_result_rows


def test_trusted_genie_numeric_check_ignores_borrower_360_product_label() -> None:
    live = GenieResponse(
        answer_text="There are 5.156 million borrowers in Borrower 360.",
        sql_query="SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360",
        sql_result_rows=[{"borrowers": 5_155_960}],
        conversation_id="conv-borrower-360-label",
        message_id="msg-borrower-360-label",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are currently in Borrower 360?")

    assert result.source == "genie"
    assert result.table_rows == live.sql_result_rows


def test_trusted_genie_numeric_check_ignores_week_date_parts() -> None:
    live = GenieResponse(
        answer_text=(
            "The average lead score rose from 45.1 for the week of April 20, "
            "2026, to 45.7 for the week of May 11, 2026."
        ),
        sql_query=(
            "SELECT DATE_TRUNC('WEEK', snapshot_date) AS snapshot_week, "
            "ROUND(AVG(avg_opportunity_score), 1) AS avg_lead_score "
            "FROM mip.gold.funnel_snapshot_daily GROUP BY 1"
        ),
        sql_result_rows=[
            {"snapshot_week": "2026-04-20T00:00:00.000Z", "avg_lead_score": "45.1"},
            {"snapshot_week": "2026-05-04T00:00:00.000Z", "avg_lead_score": "45.3"},
            {"snapshot_week": "2026-05-11T00:00:00.000Z", "avg_lead_score": "45.7"},
        ],
        conversation_id="conv-week-date-parts",
        message_id="msg-week-date-parts",
        trusted_assets=["mip.gold.funnel_snapshot_daily"],
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Show the weekly trend in average lead score by snapshot week.")

    assert result.source == "genie"
    assert result.table_rows == live.sql_result_rows


def test_trusted_genie_numeric_check_rejects_unscaled_word_suffix_claims() -> None:
    live = GenieResponse(
        answer_text="There are 1.2 million borrowers in this returned cohort.",
        sql_query="SELECT 1.2 AS avg_score FROM mip.gold.borrower_360",
        sql_result_rows=[{"avg_score": 1.2}],
        conversation_id="conv-word-suffix-unsupported",
        message_id="msg-word-suffix-unsupported",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize the returned cohort.")

    assert result.source == "policy_blocked"
    assert result.table_rows == []


def test_trusted_genie_numeric_check_ignores_identifier_dates_and_query_limits() -> None:
    live = GenieResponse(
        answer_text=("Top 10 ZIP 60617 refreshed in 2026 has 123 borrowers."),
        sql_query=("SELECT zip, refreshed_at, borrowers FROM mip.gold.borrower_360 LIMIT 10"),
        sql_result_rows=[
            {"zip": "60617", "refreshed_at": "2026-05-16T01:00:00Z", "borrowers": 123}
        ],
        conversation_id="conv-identifier-number",
        message_id="msg-identifier-number",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Show the top 10 ZIPs by borrower count.")

    assert result.source == "genie"
    assert result.table_rows == live.sql_result_rows


def test_trusted_genie_numeric_check_blocks_nonzero_claim_on_empty_rows() -> None:
    live = GenieResponse(
        answer_text="There are 10 borrowers in the returned cohort.",
        sql_query="SELECT state, COUNT(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY state",
        sql_result_rows=[],
        conversation_id="conv-empty-numeric",
        message_id="msg-empty-numeric",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Summarize this empty cohort.")

    assert result.source == "policy_blocked"
    assert result.table_rows == []
    assert "10" not in result.answer


def test_backtick_quoted_trusted_sql_is_accepted() -> None:
    live = GenieResponse(
        answer_text="Borrowers by state.",
        sql_query=(
            "SELECT state, COUNT(*) AS borrowers "
            "FROM `MIP`.`GOLD`.`BORROWER_360` "
            "WHERE array_contains(segment_codes, 'itm') GROUP BY state"
        ),
        sql_result_rows=[{"state": "IL", "borrowers": 70939}],
        conversation_id="conv-quoted",
        message_id="msg-quoted",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("break down in-the-money borrowers by state")

    assert result.source == "genie"
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.proof is not None
    assert result.proof.trusted is True


def test_source_readiness_sql_is_trusted_for_data_gap_answers() -> None:
    live = GenieResponse(
        answer_text=(
            "Building Permits is pending in mip.gold.source_readiness; "
            "do not treat missing permit records as zero demand."
        ),
        sql_query=(
            "SELECT source_name, status "
            "FROM mip.gold.source_readiness "
            "WHERE source_name = 'Building Permits'"
        ),
        sql_result_rows=[{"source_name": "Building Permits", "status": "roadmap"}],
        conversation_id="conv-source-readiness",
        message_id="msg-source-readiness",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many permits were filed in Seattle?")

    assert result.source == "genie"
    assert result.trusted_assets == ["mip.gold.source_readiness"]
    assert result.proof is not None
    assert result.proof.trusted is True


def test_backtick_quoted_untrusted_app_table_is_blocked() -> None:
    live = GenieResponse(
        answer_text="Audit rows.",
        sql_query=(
            "SELECT count(*) FROM `mip`.`gold`.`borrower_360` "
            "JOIN `mip_app`.`action_audit` ON 1=1"
        ),
        sql_result_rows=[{"count": 1}],
        conversation_id="conv-quoted-policy",
        message_id="msg-quoted-policy",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("join audit")

    assert result.source == "policy_blocked"
    assert result.trusted_assets == [
        "mip.gold.borrower_360",
        "mip_app.action_audit",
    ]
    assert result.table_rows == []


def test_unqualified_table_names_are_not_trusted_by_claimed_assets() -> None:
    live = GenieResponse(
        answer_text="Borrower count.",
        sql_query="SELECT COUNT(*) AS borrowers FROM borrower_360",
        sql_result_rows=[{"borrowers": 1}],
        trusted_assets=["mip.gold.borrower_360"],
        conversation_id="conv-unqualified",
        message_id="msg-unqualified",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers?")

    assert result.source == "policy_blocked"
    assert result.table_rows == []


@pytest.mark.parametrize(
    "sql_query",
    [
        (
            "SELECT b.state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 b "
            "JOIN borrower_360 x ON b.borrower_id = x.borrower_id "
            "GROUP BY b.state"
        ),
        (
            "WITH x AS (SELECT borrower_id FROM borrower_360) "
            "SELECT COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 b JOIN x ON b.borrower_id = x.borrower_id"
        ),
        (
            "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 b "
            "WHERE EXISTS (SELECT 1 FROM borrower_360 x WHERE x.borrower_id = b.borrower_id)"
        ),
        (
            "SELECT COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 b, borrower_360 x "
            "WHERE b.borrower_id = x.borrower_id"
        ),
        (
            "SELECT COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 b, mip.raw.borrower_360 r "
            "WHERE b.borrower_id = r.borrower_id"
        ),
        (
            "SELECT COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 b, (mip.raw.borrower_360) r "
            "WHERE b.borrower_id = r.borrower_id"
        ),
        (
            "SELECT COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 b "
            "JOIN (mip.raw.borrower_360) r ON b.borrower_id = r.borrower_id"
        ),
        (
            "SELECT COUNT(*) AS borrowers "
            "FROM (borrower_360) b, mip.gold.borrower_360 g "
            "WHERE b.borrower_id = g.borrower_id"
        ),
    ],
)
def test_mixed_qualified_and_unqualified_relations_are_policy_blocked(sql_query: str) -> None:
    live = GenieResponse(
        answer_text="Borrower count.",
        sql_query=sql_query,
        sql_result_rows=[{"borrowers": 1}],
        trusted_assets=["mip.gold.borrower_360"],
        conversation_id="conv-mixed-unqualified",
        message_id="msg-mixed-unqualified",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers?")

    assert result.source == "policy_blocked"
    assert result.table_rows == []


def test_trusted_clip_join_is_allowed_when_clip_is_not_selected() -> None:
    live = GenieResponse(
        answer_text="Competitor lien evidence rows.",
        sql_query=(
            "SELECT b.borrower_id, e.signal_type "
            "FROM mip.gold.borrower_360 b "
            "JOIN mip.gold.evidence_events e ON b.clip = e.clip "
            "WHERE e.signal_type = 'competitor_lien'"
        ),
        sql_result_rows=[{"borrower_id": "B-12345", "signal_type": "competitor_lien"}],
        conversation_id="conv-clip-join",
        message_id="msg-clip-join",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Which retention borrowers have competitor lien evidence?")

    assert result.source == "genie"
    assert result.table_rows == [{"borrower_id": "B-12345", "signal_type": "competitor_lien"}]


def test_genie_row_redaction_blocks_case_and_camel_pii_aliases() -> None:
    live = GenieResponse(
        answer_text="Rows.",
        sql_query="SELECT count(*) FROM mip.gold.borrower_360",
        sql_result_rows=[
            {
                "borrower_id": "B-1",
                "OwnerName": "Raw Name",
                "OWNER_EMAIL": "raw@example.com",
                "owner_1_full_name": "Raw Owner",
                "SitusStreetAddress": "123 Main St",
                "mailing_street_address": "456 Market Ave",
                "score": 86,
            }
        ],
        conversation_id="conv-pii",
        message_id="msg-pii",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show borrowers")

    assert result.source == "genie"
    assert result.table_rows == [{"borrower_id": "B-1", "score": 86}]


def test_pending_feed_columns_are_blocked_even_when_question_does_not_say_permit() -> None:
    live = GenieResponse(
        answer_text="Rows.",
        sql_query="SELECT state, COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE has_permit = true GROUP BY state",
        sql_result_rows=[{"state": "IL", "borrowers": 1}],
        conversation_id="conv-hidden-permit",
        message_id="msg-hidden-permit",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("Show borrowers with recent remodeling signals by state.")

    assert result.source == "policy_blocked"
    assert result.table_rows == []
    assert result.proof is not None
    assert any("Building Permits" in gap for gap in result.proof.known_data_gaps)


def test_state_breakdown_uses_direct_canonical_gold_sql_without_genie_call() -> None:
    live = GenieResponse(
        answer_text="IL has the most in-the-money borrowers.",
        sql_query=(
            "SELECT state, COUNT(*) AS borrowers "
            "FROM mip.gold.borrower_360 WHERE in_the_money = true GROUP BY state"
        ),
        sql_result_rows=None,
        conversation_id="conv-replay",
        message_id="msg-replay",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient([{"state": "IL", "in_the_money_borrowers": 70939}])
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("break down in-the-money borrowers by state")

    assert result.source == "trusted_sql"
    assert stub.ask_calls == []
    assert result.table_rows == [{"state": "IL", "in_the_money_borrowers": 70939}]
    assert result.row_count == 1
    assert result.proof is not None
    assert result.proof.trusted is True
    assert sql.statements == [result.sql_query]
    assert result.sql_query is not None
    assert "FROM mip.gold.borrower_360" in result.sql_query


def test_in_the_money_count_uses_canonical_gold_grain() -> None:
    live = GenieResponse(
        answer_text=(
            "There are 277,139 borrowers currently in-the-money. "
            "Source: mip.semantics.borrower_opportunity_metric_view."
        ),
        sql_query=(
            "SELECT COUNT(*) AS borrowers "
            "FROM mip.semantics.borrower_opportunity_metric_view "
            "WHERE in_the_money = true"
        ),
        sql_result_rows=[{"borrowers": 277139}],
        conversation_id="conv-itm-count",
        message_id="msg-itm-count",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient(
        [
            {
                "in_the_money_borrowers": 147742,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are currently in-the-money?")

    assert result.source == "trusted_sql"
    assert result.answer.startswith("There are 147,742 borrowers")
    assert "mip.gold.borrower_360" in result.answer
    assert "277,139" not in result.answer
    assert result.table_rows == [
        {
            "in_the_money_borrowers": 147742,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        }
    ]
    assert result.metric_value == "147,742"
    assert result.sql_query == (
        "SELECT COUNT(*) AS in_the_money_borrowers\n"
        "     , MAX(refreshed_at) AS refreshed_at\n"
        "FROM mip.gold.borrower_360\n"
        "WHERE in_the_money = TRUE"
    )


def test_in_the_money_count_direct_canonical_bypasses_genie_breaker() -> None:
    """Canonical count questions should not depend on Genie API availability.

    The answer still comes from live governed SQL via the repository SQL client,
    with ``source=trusted_sql`` and proof. This prevents basic metric questions
    from tripping Genie rate limits during deploy smoke while preserving the
    no-mock posture.
    """

    stub = _StubClient(
        _make_breaker("open"),
        response=GenieClientError("HTTP 429 from Genie API", status_code=429),
    )
    sql = _StubSqlClient(
        [
            {
                "in_the_money_borrowers": 147742,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are currently in-the-money?")

    assert result.source == "trusted_sql"
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.metric_value == "147,742"
    assert stub.ask_calls == []
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.proof.source_assets == ["mip.gold.borrower_360"]
    assert result.proof.data_freshness
    assert result.proof.data_freshness[0].refreshed_at == "2026-05-04T22:08:34.662Z"
    assert sql.statements == [result.sql_query]
    assert sql.parameters == [None]


def test_in_the_money_zip_direct_canonical_bypasses_genie_breaker() -> None:
    stub = _StubClient(
        _make_breaker("open"),
        response=GenieClientError("HTTP 429 from Genie API", status_code=429),
    )
    rows = [
        {
            "zip": "60617",
            "state": "IL",
            "in_the_money_borrowers": 1200,
            "avg_score": 81.5,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
        {
            "zip": "60628",
            "state": "IL",
            "in_the_money_borrowers": 900,
            "avg_score": 79.2,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
    ]
    sql = _StubSqlClient(rows)
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("Which ZIPs have the most in-the-money refinance candidates?")

    assert result.source == "trusted_sql"
    assert result.trusted_assets == ["mip.gold.lead_population"]
    assert result.table_rows == rows
    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert any(action.id == "open-cohort" for action in result.actions)
    assert stub.ask_calls == []
    assert "GROUP BY zip, state" in (result.sql_query or "")
    assert "FROM mip.gold.lead_population" in (result.sql_query or "")


def test_in_the_money_state_breakdown_direct_canonical_bypasses_genie_breaker() -> None:
    stub = _StubClient(
        _make_breaker("open"),
        response=GenieClientError("HTTP 429 from Genie API", status_code=429),
    )
    rows = [
        {
            "state": "IL",
            "in_the_money_borrowers": 4000,
            "avg_rate_spread_bps": 184.2,
            "avg_score": 83.0,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
        {
            "state": "CA",
            "in_the_money_borrowers": 3500,
            "avg_rate_spread_bps": 177.0,
            "avg_score": 82.1,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
    ]
    sql = _StubSqlClient(rows)
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond(
        "Break down in-the-money borrowers by state and return the count as a table."
    )

    assert result.source == "trusted_sql"
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.table_rows == rows
    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert any(action.id == "create-campaign-draft" for action in result.actions)
    assert stub.ask_calls == []
    assert "GROUP BY state" in (result.sql_query or "")


def test_in_the_money_count_applies_state_scope_when_present() -> None:
    live = GenieResponse(
        answer_text="There are 277,139 borrowers currently in-the-money.",
        sql_query=(
            "SELECT COUNT(*) AS borrowers "
            "FROM mip.semantics.borrower_opportunity_metric_view "
            "WHERE in_the_money = true"
        ),
        sql_result_rows=[{"borrowers": 277139}],
        conversation_id="conv-itm-state",
        message_id="msg-itm-state",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient(
        [
            {
                "in_the_money_borrowers": 70939,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers in Illinois are in the money?")

    assert result.answer.startswith("There are 70,939 borrowers")
    assert "in Illinois (IL)" in result.answer
    assert result.table_rows == [
        {
            "in_the_money_borrowers": 70939,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
            "state": "IL",
        }
    ]
    assert result.sql_query == (
        "SELECT COUNT(*) AS in_the_money_borrowers\n"
        "     , MAX(refreshed_at) AS refreshed_at\n"
        "FROM mip.gold.borrower_360\n"
        "WHERE in_the_money = TRUE\n"
        "  AND state = :state"
    )
    assert sql.parameters == [{"state": "IL"}]


def test_in_the_money_count_applies_lowercase_ambiguous_state_code_when_contextual() -> None:
    live = GenieResponse(
        answer_text="There are 12 borrowers currently in-the-money.",
        sql_query="SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE in_the_money = true",
        sql_result_rows=[{"borrowers": 12}],
        conversation_id="conv-itm-ok",
        message_id="msg-itm-ok",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient(
        [
            {
                "in_the_money_borrowers": 12,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers in ok are in the money?")

    assert result.sql_query is not None
    assert "AND state = :state" in result.sql_query
    assert sql.parameters == [{"state": "OK"}]


def test_in_the_money_count_does_not_scope_common_words_as_state_codes() -> None:
    live = GenieResponse(
        answer_text="There are 147,742 borrowers currently in-the-money.",
        sql_query="SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 WHERE in_the_money = true",
        sql_result_rows=[{"borrowers": 147742}],
        conversation_id="conv-itm-no-false-state",
        message_id="msg-itm-no-false-state",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient(
        [
            {
                "in_the_money_borrowers": 147742,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("Hi, how many borrowers are in the money?")

    assert result.sql_query is not None
    assert "AND state = :state" not in result.sql_query
    assert sql.parameters == [None]


@pytest.mark.parametrize(
    ("question", "city", "count"),
    [
        ("How many borrowers are in the money in Chicago?", "Chicago", 5710),
        ("How many in-the-money borrowers in Chicago?", "Chicago", 5710),
        ("How many in-the-money borrowers do we have in Boston?", "Boston", 0),
    ],
)
def test_in_the_money_count_applies_city_scope_when_present(
    question: str,
    city: str,
    count: int,
) -> None:
    live = GenieResponse(
        answer_text="Genie returned the all-footprint count: 147,742.",
        sql_query=(
            "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 " "WHERE in_the_money = true"
        ),
        sql_result_rows=[{"borrowers": 147742}],
        conversation_id="conv-itm-chicago",
        message_id="msg-itm-chicago",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient(
        [
            {
                "in_the_money_borrowers": count,
                "refreshed_at": "2026-05-04T22:08:34.662Z",
            }
        ]
    )
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond(question)

    assert result.answer.startswith(f"There are {count:,} borrowers")
    assert f"in {city}" in result.answer
    assert "overall share total" in result.answer
    assert "147,742" not in result.answer
    assert result.table_rows == [
        {
            "city": city,
            "in_the_money_borrowers": count,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        }
    ]
    assert result.sql_query == (
        "SELECT COUNT(*) AS in_the_money_borrowers\n"
        "     , MAX(refreshed_at) AS refreshed_at\n"
        "FROM mip.gold.borrower_360\n"
        "WHERE in_the_money = TRUE\n"
        "  AND LOWER(city) = LOWER(:city)"
    )
    assert sql.parameters == [{"city": city}]


def test_mean_lead_score_by_msa_uses_direct_sql_before_genie() -> None:
    live = GenieResponse(
        answer_text="Genie tried to use an unsupported MSA lookup.",
        sql_query="SELECT count(*) FROM mip_app.saved_leads",
        sql_result_rows=[{"count": 1}],
        conversation_id="conv-msa-score",
        message_id="msg-msa-score",
    )
    rows = [
        {
            "market": "Chicago, IL (CBSA 16980)",
            "msa_cbsa_code": "16980",
            "borrowers": 1200000,
            "avg_score": 43.1,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
        {
            "market": "Los Angeles, CA (CBSA 31080)",
            "msa_cbsa_code": "31080",
            "borrowers": 900000,
            "avg_score": 42.7,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
        {
            "market": "Dallas, TX (CBSA 19100)",
            "msa_cbsa_code": "19100",
            "borrowers": 800000,
            "avg_score": 41.9,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
        {
            "market": "Seattle, WA (CBSA 42660)",
            "msa_cbsa_code": "42660",
            "borrowers": 700000,
            "avg_score": 42.2,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
        {
            "market": "Miami, FL (CBSA 33100)",
            "msa_cbsa_code": "33100",
            "borrowers": 600000,
            "avg_score": 40.5,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
    ]
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient(rows)
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("Compare mean lead score by MSA for our top five markets.")

    assert result.source == "trusted_sql"
    assert result.table_rows == rows
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.sql_query is not None
    assert "situs_cbsa_code" in result.sql_query
    assert "mip_app.saved_leads" not in result.sql_query
    assert result.proof is not None
    assert result.proof.trusted is True
    assert stub.ask_calls == []
    assert sql.statements == [result.sql_query]


def test_mean_lead_score_by_msa_uses_canonical_cbsa_query_when_live_turn_is_not_unsafe() -> None:
    live = GenieResponse(
        answer_text="Genie did not attach SQL.",
        sql_query=None,
        sql_result_rows=[],
        conversation_id="conv-msa-score",
        message_id="msg-msa-score",
    )
    rows = [
        {
            "market": "Chicago, IL (CBSA 16980)",
            "msa_cbsa_code": "16980",
            "borrowers": 1200000,
            "avg_score": 43.1,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
        {
            "market": "Los Angeles, CA (CBSA 31080)",
            "msa_cbsa_code": "31080",
            "borrowers": 900000,
            "avg_score": 42.7,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        },
    ]
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient(rows)
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("Compare mean lead score by MSA for our top five markets.")

    assert result.source == "trusted_sql"
    assert result.table_rows == rows
    assert result.row_count == 2
    assert "situs_cbsa_code" in result.answer
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.sql_query is not None
    assert "mip.gold.borrower_360" in result.sql_query
    assert "situs_cbsa_code" in result.sql_query
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.visualization is not None
    assert result.visualization.kind == "bar"
    assert sql.statements == [result.sql_query]


# ---------------------------------------------------------------------------
# Degraded response -- only when breaker is open.
# ---------------------------------------------------------------------------


def test_breaker_open_with_catalog_match_returns_degraded() -> None:
    # Breaker open; return the honest "Genie is warming up" message rather
    # than local analytic content.
    stub = _StubClient(_make_breaker("open"), response=None)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show me the in the money segment")

    assert result.source == "degraded"
    assert result.conversation_id == ""
    assert result.proof is not None
    assert result.proof.conversation_id is None
    assert "warming up" in result.answer.lower()
    # No trusted assets on a degraded reply — we are not claiming a
    # source we cannot cite.
    assert result.trusted_assets == []
    # Follow-up suggestions still flow through (static UI hints, not
    # fabricated data).
    assert result.follow_up_questions
    assert result.question == "show me the in the money segment"
    # Live client must NOT have been called while the breaker was open.
    assert stub.ask_calls == []


def test_breaker_open_unknown_question_returns_degraded_message() -> None:
    stub = _StubClient(_make_breaker("open"), response=None)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("what's the weather in tokyo")

    assert result.source == "degraded"
    assert result.conversation_id == ""
    assert "warming up" in result.answer.lower()
    # No trusted assets on a degraded reply -- we are not claiming a
    # source we cannot cite.
    assert result.trusted_assets == []
    assert stub.ask_calls == []


# ---------------------------------------------------------------------------
# Breaker transitions mid-call.
# ---------------------------------------------------------------------------


def test_dependency_down_error_returns_degraded() -> None:
    # Breaker closed at check time but the call itself raises
    # DependencyDownError (simulating the breaker opening during the
    # attempt). We now return the honest degraded message regardless
    # of whether the question resembles a known prompt.
    stub = _StubClient(
        _make_breaker("closed"),
        response=DependencyDownError("genie", reason="circuit opened mid-call"),
    )
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show me the in the money segment")
    assert result.source == "degraded"
    assert "warming up" in result.answer.lower()
    # The live client WAS attempted (breaker was closed at check time);
    # only the call itself raised.
    assert stub.ask_calls == ["show me the in the money segment"]


def test_dependency_down_error_unknown_question_returns_degraded() -> None:
    stub = _StubClient(
        _make_breaker("closed"),
        response=DependencyDownError("genie", reason="circuit opened mid-call"),
    )
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("random unrelated question about tokyo weather")
    assert result.source == "degraded"


# ---------------------------------------------------------------------------
# Non-dependency-down errors propagate -- no silent mock fallback.
# ---------------------------------------------------------------------------


def test_genie_client_error_propagates_without_silent_fallback() -> None:
    stub = _StubClient(
        _make_breaker("closed"),
        response=GenieClientError("HTTP 500 from Genie API", status_code=500),
    )
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    with pytest.raises(GenieClientError):
        repo.respond("how many borrowers are in the money")


# ---------------------------------------------------------------------------
# End-to-end resilient wrapper: verify the breaker trips after failures.
# ---------------------------------------------------------------------------


def test_end_to_end_with_real_resilient_wrapper() -> None:
    """Smoke test that the repository plugs into the real ``Resilient``
    + ``CircuitBreaker`` machinery and returns the degraded message
    after the breaker opens on repeated failures."""

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def ask(self, question: str, conversation_id: str | None = None) -> Any:  # noqa: ARG002
            self.calls += 1
            raise GenieClientError("HTTP 503")

    breaker = CircuitBreaker("genie", failure_threshold=1, cooldown_s=60.0)
    resilient = Resilient[Any](
        breaker=breaker,
        dependency_name="genie",
        attempts=1,
        retry_on=(GenieClientError,),
    )

    class _Wired:
        def __init__(self) -> None:
            self._client = _FlakyClient()

        @property
        def resilient(self) -> Any:
            return resilient

        def ask(self, question: str, conversation_id: str | None = None) -> Any:
            return resilient.call(
                lambda: self._client.ask(question, conversation_id=conversation_id)
            )

    repo = DatabricksGenieRepository(_Wired())  # type: ignore[arg-type]

    # First call -- breaker closed; the flaky client raises and the
    # resilient wrapper translates to DependencyDownError. Question is
    # not in catalog -> degraded message.
    first = repo.respond("completely random query")
    assert first.source == "degraded"
    # After the failure, breaker is open.
    assert breaker.state == "open"

    # Next call with a catalog-matching question -> still degraded
    # message (no fabricated data), no live call attempted.
    second = repo.respond("show me the in the money segment")
    assert second.source == "degraded"
    assert "warming up" in second.answer.lower()
