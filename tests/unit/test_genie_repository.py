"""Unit tests for ``DatabricksGenieRepository`` -- breaker-gated safe corpus.

Contract under test:

1. Breaker CLOSED + happy path: calls ``ResilientGenieClient.ask`` and
   adapts the response to ``GenieMessageResponse(source="genie")``.
2. Breaker OPEN + question in safe corpus: returns the honest "warming
   up" message with ``source="degraded"``. Prior behavior (`source=
   "fallback"` with a curated catalog answer body) was retired
   2026-05-04 — the catalog answers shipped hardcoded specific numbers
   that read as real Cotality data. CLAUDE.md prohibits mock fallback
   in the running app, so the only acceptable degraded response carries
   no fabricated content.
3. Breaker OPEN + unknown question: also returns the honest "warming
   up" message with ``source="degraded"``, never fabricated data.
4. ``DependencyDownError`` bubbled from the client with the breaker
   just opening: falls back to the safe corpus / degraded message.
5. ``GenieClientError`` from the live client (401, 500, malformed JSON)
   is re-raised -- it is NOT silently masked by a catalog answer.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_client import GenieClientError, GenieResponse
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
        answer_text="12,840 borrowers are currently in the money.",
        sql_query="SELECT count(*) FROM mip.gold.lead_scores WHERE in_the_money",
        sql_result_rows=[{"count": 12840}],
        conversation_id="conv-abc",
        message_id="msg-1",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are in the money?")

    assert isinstance(result, GenieMessageResponse)
    assert result.source == "genie"
    assert result.question == "How many borrowers are in the money?"
    assert "12,840" in result.answer
    assert result.table_rows == [{"count": 12840}]
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
    assert result.conversation_id == "repair-conv"
    assert len(stub.ask_calls) == 2
    assert stub.ask_calls[0] == "Which zips have the most in-the-money refi candidates?"
    assert "Regenerate the following Mortgage Intelligence Platform data question" in stub.ask_calls[1]
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
        sql_query=(
            "SELECT count(*) FROM mip.gold.lead_scores "
            "JOIN mip_app.action_audit ON 1=1"
        ),
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
            "SELECT count(*) FROM mip.silver.mortgage_events "
            "/* mip.gold.borrower_360 */"
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


def test_genie_row_redaction_blocks_case_and_camel_pii_aliases() -> None:
    live = GenieResponse(
        answer_text="Rows.",
        sql_query="SELECT count(*) FROM mip.gold.borrower_360",
        sql_result_rows=[
            {
                "borrower_id": "B-1",
                "OwnerName": "Raw Name",
                "OWNER_EMAIL": "raw@example.com",
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


def test_trusted_sql_is_replayed_when_genie_query_rows_are_missing() -> None:
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
    sql = _StubSqlClient([{"state": "IL", "borrowers": 70939}])
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("break down in-the-money borrowers by state")

    assert result.source == "genie"
    assert result.table_rows == [{"state": "IL", "borrowers": 70939}]
    assert result.row_count == 1
    assert result.proof is not None
    assert result.proof.trusted is True
    assert sql.statements
    assert sql.statements[0].startswith("SELECT * FROM (")
    assert "LIMIT 500" in sql.statements[0]


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
    sql = _StubSqlClient([
        {
            "in_the_money_borrowers": 147742,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        }
    ])
    repo = DatabricksGenieRepository(stub, sql)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are currently in-the-money?")

    assert result.source == "genie"
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
    assert result.trusted_assets == ["mip.gold.borrower_360"]
    assert result.proof is not None
    assert result.proof.trusted is True
    assert result.proof.source_assets == ["mip.gold.borrower_360"]
    assert result.proof.data_freshness
    assert result.proof.data_freshness[0].refreshed_at == "2026-05-04T22:08:34.662Z"
    assert sql.statements == [result.sql_query]
    assert sql.parameters == [None]


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
    sql = _StubSqlClient([
        {
            "in_the_money_borrowers": 70939,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        }
    ])
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
            "SELECT COUNT(*) AS borrowers FROM mip.gold.borrower_360 "
            "WHERE in_the_money = true"
        ),
        sql_result_rows=[{"borrowers": 147742}],
        conversation_id="conv-itm-chicago",
        message_id="msg-itm-chicago",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    sql = _StubSqlClient([
        {
            "in_the_money_borrowers": count,
            "refreshed_at": "2026-05-04T22:08:34.662Z",
        }
    ])
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


def test_mean_lead_score_by_msa_uses_canonical_cbsa_query() -> None:
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

    assert result.source == "genie"
    assert result.table_rows == rows
    assert result.row_count == 5
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
# Safe-corpus fallback -- only when breaker is open.
# ---------------------------------------------------------------------------


def test_breaker_open_with_catalog_match_returns_degraded() -> None:
    # Breaker open; even when the question matches a catalog intent we
    # now return the honest "Genie is warming up" message rather than
    # the curated catalog answer body. The catalog used to ship hard-
    # coded specific numbers (counts, dollar amounts, sample borrower
    # IDs) tagged source="fallback"; user feedback 2026-05-04 flagged
    # those as misleading because they read like real Cotality data.
    # CLAUDE.md prohibits mock fallback in the running app, so the
    # only acceptable degraded response carries no fabricated content.
    stub = _StubClient(_make_breaker("open"), response=None)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show me the in the money segment")

    assert result.source == "degraded"
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
    # of whether the question matched the catalog (2026-05-04: see
    # `test_breaker_open_with_catalog_match_returns_degraded` for the
    # rationale — catalog answer bodies were misleading).
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
