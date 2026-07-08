from __future__ import annotations

from backend.services.genie_client import GenieResponse
from backend.services.repositories.databricks_repo import (
    _CANONICAL_STRATEGY_BOARD_SQL,
    DatabricksGenieRepository,
    _adapt_genie_response,
    _canonical_strategy_board_scope,
    _needs_genie_sql_repair,
    _sql_uses_stale_evidence_signal_enum,
)

BORROWER_360 = "mip.gold.borrower_360"
EVIDENCE_EVENTS = "mip.gold.evidence_events"


class _SqlClient:
    def __init__(self) -> None:
        self.sql: str | None = None
        self.executed_sql: str | None = None
        self.executed_params: dict[str, object] | None = None

    def execute_one(
        self,
        sql: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        _ = params
        self.sql = sql
        return {
            "retention_risk_borrowers": 6638,
            "refreshed_at": "2026-05-09T00:00:00Z",
        }

    def execute(
        self,
        sql: str,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        self.executed_sql = sql
        self.executed_params = params
        if "exploded_segments" in sql:
            return [
                {
                    "state": "IL",
                    "segment_code": "itm",
                    "marketable_borrowers": 8200,
                    "avg_score": 91.2,
                    "leading_offer_code": "refi_plus_heloc",
                    "leading_recommended_offer": "Refinance + HELOC",
                    "leading_offer_borrowers": 5100,
                    "refreshed_at": "2026-05-09T00:00:00Z",
                },
                {
                    "state": "CA",
                    "segment_code": "equity",
                    "marketable_borrowers": 7400,
                    "avg_score": 89.7,
                    "leading_offer_code": "heloc",
                    "leading_recommended_offer": "HELOC",
                    "leading_offer_borrowers": 4600,
                    "refreshed_at": "2026-05-09T00:00:00Z",
                },
                {
                    "state": "TX",
                    "segment_code": "investor",
                    "marketable_borrowers": 4300,
                    "avg_score": 86.5,
                    "leading_offer_code": "dscr",
                    "leading_recommended_offer": "DSCR Review",
                    "leading_offer_borrowers": 2100,
                    "refreshed_at": "2026-05-09T00:00:00Z",
                },
                {
                    "state": "FL",
                    "segment_code": "retention",
                    "marketable_borrowers": 3900,
                    "avg_score": 84.8,
                    "leading_offer_code": "retention",
                    "leading_recommended_offer": "Retention Review",
                    "leading_offer_borrowers": 1800,
                    "refreshed_at": "2026-05-09T00:00:00Z",
                },
            ]
        return [
            {
                "borrower_id": "B-102FL7THC6Q3L",
                "city": "Calumet City",
                "state": "IL",
                "recommended_offer_code": "retention",
                "opportunity_score": 88,
                "latest_competitor_lien_at": "2026-05-09T00:00:00Z",
                "total_matching_borrowers": 304,
            }
        ]


class _GenieClient:
    def __init__(
        self, responses: list[GenieResponse], breaker_state: str = "closed"
    ) -> None:
        self.responses = responses
        self.questions: list[str] = []

        class _Breaker:
            state = breaker_state

        class _Resilient:
            breaker = _Breaker()

        self.resilient = _Resilient()

    def ask(self, question: str, conversation_id: str | None = None) -> GenieResponse:
        _ = conversation_id
        self.questions.append(question)
        return self.responses.pop(0)


def test_genie_strategy_board_uses_canonical_marketable_offer_lanes() -> None:
    question = (
        "Where should the configured tenant lender spend its next 10000 "
        "outreach touches this week, and why?"
    )
    result = GenieResponse(
        answer_text=(
            "Prioritize the next outreach touches by state, segment, and offer lane."
        ),
        sql_query=None,
        sql_result_rows=[],
        trusted_assets=[],
        conversation_id="conv-strategy",
        message_id="msg-strategy",
    )

    assert _canonical_strategy_board_scope(question) is True

    sql = _SqlClient()
    response = _adapt_genie_response(question, result, sql_client=sql)  # type: ignore[arg-type]

    assert response.source == "trusted_sql"
    assert response.row_count == 4
    assert response.sql_query == _CANONICAL_STRATEGY_BOARD_SQL
    assert sql.executed_sql == response.sql_query
    assert response.trusted_assets == [BORROWER_360]
    assert response.proof is not None
    assert response.proof.trusted is True
    assert response.visualization is not None
    assert response.visualization.kind == "strategy_board"
    # Voice-first: Genie's own narrative leads, plus an appended verification note.
    assert response.answer.startswith(
        "Prioritize the next outreach touches by state, segment, and offer lane."
    )
    assert "Verified against" in response.answer
    answer_lc = response.answer.lower()
    assert "state" in answer_lc
    assert "segment" in answer_lc
    assert "offer" in answer_lc
    assert "marketing_eligible = TRUE" in (response.sql_query or "")
    assert "consent_status = 'opt_in'" in (response.sql_query or "")
    assert "recommended_offer_code <> 'nurture'" in (response.sql_query or "")
    assert response.table_rows and len(response.table_rows) >= 4


def test_genie_repairs_impossible_current_customer_competitor_lien_retention_query() -> None:
    question = "How many current Summit customers are at risk of going to a competitor?"
    result = GenieResponse(
        answer_text="There are 0 current Summit customers at risk.",
        sql_query=(
            f"SELECT COUNT(*) FROM {BORROWER_360} "
            "WHERE is_current_customer = TRUE AND is_competitor_lien = TRUE"
        ),
        sql_result_rows=[{"count": 0}],
        trusted_assets=[BORROWER_360],
        conversation_id="conv-retention",
        message_id="msg-retention",
    )

    assert _needs_genie_sql_repair(question, result) is True

    sql = _SqlClient()
    response = _adapt_genie_response(question, result, sql_client=sql)  # type: ignore[arg-type]

    assert response.source == "trusted_sql"
    assert response.metric_value == "6,638"
    # Voice-first: Genie's narrative is preserved; the verified gold-grain count
    # is appended as a correction note rather than replacing the text.
    assert response.answer.startswith("There are 0 current Summit customers at risk.")
    assert "Verified against" in response.answer
    assert "6,638" in response.answer
    assert "is_competitor_lien = TRUE" not in (response.sql_query or "")
    assert "array_contains(segment_codes, 'retention')" in (response.sql_query or "")
    assert sql.sql == response.sql_query
    cohort_action = next(action for action in response.actions if action.id == "open-cohort")
    assert "lender_relationship=Current+customer" in (cohort_action.route or "")
    assert "product=Retention" in (cohort_action.route or "")
    assert cohort_action.criteria["result_filters"]["portfolio_criteria"] == {
        "product": "Retention",
        "lender_relationship": "Current customer",
    }


def test_genie_repairs_retention_risk_phrase_without_current_customer_wording() -> None:
    question = "How many retention-risk borrowers do we have?"
    result = GenieResponse(
        answer_text="There are 0 retention-risk borrowers.",
        sql_query=(
            f"SELECT COUNT(*) FROM {BORROWER_360} "
            "WHERE is_current_customer = TRUE AND is_competitor_lien = TRUE"
        ),
        sql_result_rows=[{"count": 0}],
        trusted_assets=[BORROWER_360],
        conversation_id="conv-retention-risk",
        message_id="msg-retention-risk",
    )

    assert _needs_genie_sql_repair(question, result) is True

    sql = _SqlClient()
    response = _adapt_genie_response(question, result, sql_client=sql)  # type: ignore[arg-type]

    assert response.source == "trusted_sql"
    assert "is_competitor_lien = TRUE" not in (response.sql_query or "")
    assert "recommended_offer_code = 'retention'" in (response.sql_query or "")


def test_genie_retention_list_uses_canonical_row_lookup_not_count_metric() -> None:
    question = (
        "Which borrowers on our retention list have a competitor lien filed in the last 30 days?"
    )
    result = GenieResponse(
        answer_text="Rows.",
        sql_query=(
            "SELECT b.borrower_id, e.signal_type "
            f"FROM {BORROWER_360} b "
            f"JOIN {EVIDENCE_EVENTS} e ON b.clip = e.clip "
            "WHERE array_contains(b.segment_codes, 'retention') "
            "AND e.signal_type = 'competitor_lien'"
        ),
        sql_result_rows=[{"borrower_id": "B-102FL7THC6Q3L", "signal_type": "competitor_lien"}],
        trusted_assets=[BORROWER_360, EVIDENCE_EVENTS],
        conversation_id="conv-retention-list",
        message_id="msg-retention-list",
    )

    response = _adapt_genie_response(question, result, sql_client=_SqlClient())  # type: ignore[arg-type]

    assert response.source == "trusted_sql"
    assert "COUNT(*) AS retention_risk_borrowers" not in (response.sql_query or "")
    assert "e.signal_type = 'competitor_lien'" in (response.sql_query or "")
    assert "e.signal_type = 'lien-change'" not in (response.sql_query or "")
    assert response.table_rows == [
        {
            "borrower_id": "B-102FL7THC6Q3L",
            "city": "Calumet City",
            "state": "IL",
            "recommended_offer_code": "retention",
            "opportunity_score": 88,
            "latest_competitor_lien_at": "2026-05-09T00:00:00Z",
            "total_matching_borrowers": 304,
        }
    ]
    assert response.metric_value == "304"
    # Voice-first: Genie's narrative leads; the verified total is appended.
    assert response.answer.startswith("Rows.")
    assert "Verified against" in response.answer
    assert "304" in response.answer


def test_genie_retention_list_uses_canonical_competitor_lien_signal() -> None:
    question = "retention borrowers with competitor-lien evidence in Illinois"
    result = GenieResponse(
        answer_text="No rows.",
        sql_query=(
            "SELECT b.borrower_id "
            f"FROM {BORROWER_360} b "
            f"JOIN {EVIDENCE_EVENTS} e ON b.clip = e.clip "
            "WHERE array_contains(b.segment_codes, 'retention') "
            "AND e.signal_type = 'lien-change' "
            "AND e.signal_value = 'competitor'"
        ),
        sql_result_rows=[],
        trusted_assets=[BORROWER_360, EVIDENCE_EVENTS],
        conversation_id="conv-retention-list",
        message_id="msg-retention-list",
    )
    repaired_result = GenieResponse(
        answer_text="Rows.",
        sql_query=(
            "SELECT b.borrower_id "
            f"FROM {BORROWER_360} b "
            f"JOIN {EVIDENCE_EVENTS} e ON b.clip = e.clip "
            "WHERE array_contains(b.segment_codes, 'retention') "
            "AND e.signal_type = 'competitor_lien' "
            "AND to_timestamp(e.`timestamp`) >= current_timestamp() - interval 30 days"
        ),
        sql_result_rows=[{"borrower_id": "B-102FL7THC6Q3L"}],
        trusted_assets=[BORROWER_360, EVIDENCE_EVENTS],
        conversation_id="conv-retention-list",
        message_id="msg-retention-list-repaired",
    )

    sql = _SqlClient()
    # Live-first posture: the canonical competitor-lien SQL is now the honest
    # degraded fallback, exercised here with the breaker open (live Genie down).
    client = _GenieClient([result, repaired_result], breaker_state="open")
    repo = DatabricksGenieRepository(client, sql)  # type: ignore[arg-type]
    response = repo.respond(question)

    assert response.source == "trusted_sql"
    assert response.row_count == 1
    assert response.table_rows == [
        {
            "borrower_id": "B-102FL7THC6Q3L",
            "city": "Calumet City",
            "state": "IL",
            "recommended_offer_code": "retention",
            "opportunity_score": 88,
            "latest_competitor_lien_at": "2026-05-09T00:00:00Z",
            "total_matching_borrowers": 304,
        }
    ]
    assert "signal_type = 'competitor_lien'" in (response.sql_query or "")
    assert "b.state = :state" in (response.sql_query or "")
    assert sql.executed_params == {"state": "IL"}
    assert "lien-change" not in (response.sql_query or "")
    assert "signal_value = 'competitor'" not in (response.sql_query or "")
    assert "to_timestamp(e.`timestamp`)" in (response.sql_query or "")
    assert response.proof is not None
    assert response.proof.trusted is True
    assert sql.executed_sql == response.sql_query
    assert response.metric_value == "304"
    assert "in illinois" in response.answer.lower()
    assert "There are 304 retention-list borrowers" in response.answer
    assert client.questions == []
    assert response.proof is not None
    assert any(
        "Live Genie is temporarily unavailable" in gap
        for gap in response.proof.known_data_gaps
    )
    cohort_action = next(action for action in response.actions if action.id == "open-cohort")
    assert "borrower_ids=B-102FL7THC6Q3L" in (cohort_action.route or "")
    assert "lender_relationship=Competitor" not in (cohort_action.route or "")


def test_genie_repairs_and_blocks_stale_evidence_signal_enums() -> None:
    assert _sql_uses_stale_evidence_signal_enum(
        f"SELECT * FROM {EVIDENCE_EVENTS} WHERE signal_type = 'lien-change'"
    )
    assert _sql_uses_stale_evidence_signal_enum(
        f"SELECT * FROM {EVIDENCE_EVENTS} WHERE signal_type IN ('rate_spread', 'competitor')"
    )
    assert not _sql_uses_stale_evidence_signal_enum(
        f"SELECT * FROM {EVIDENCE_EVENTS} WHERE signal_type = 'competitor_lien'"
    )

    result = GenieResponse(
        answer_text="Rows.",
        sql_query=(
            f"SELECT borrower_id FROM {EVIDENCE_EVENTS} " "WHERE signal_type = 'lien-change'"
        ),
        sql_result_rows=[{"borrower_id": "B-102FL7THC6Q3L"}],
        trusted_assets=[EVIDENCE_EVENTS],
        conversation_id="conv-stale-enum",
        message_id="msg-stale-enum",
    )

    assert _needs_genie_sql_repair("Which borrowers have competitor lien evidence?", result) is True
    response = _adapt_genie_response(
        "Which borrowers have competitor lien evidence?",
        result,
        sql_client=_SqlClient(),  # type: ignore[arg-type]
    )

    assert response.source == "policy_blocked"
    assert response.sql_query is None


def test_genie_retention_risk_repairs_wrong_evidence_enums() -> None:
    question = "How many current Summit customers are at risk of going to a competitor?"
    bad_result = GenieResponse(
        answer_text="There are 0 current Summit customers at risk.",
        sql_query=(
            f"SELECT COUNT(*) FROM {BORROWER_360} b "
            f"JOIN {EVIDENCE_EVENTS} e ON b.clip = e.clip "
            "WHERE b.is_current_customer = TRUE "
            "AND e.signal_type = 'lien-change' "
            "AND e.signal_value = 'competitor'"
        ),
        sql_result_rows=[{"count": 0}],
        trusted_assets=[BORROWER_360, EVIDENCE_EVENTS],
        conversation_id="conv-retention",
        message_id="msg-retention",
    )
    repaired_result = GenieResponse(
        answer_text="There are 6,638 current Summit customers in the retention-risk cohort.",
        sql_query=(
            "SELECT COUNT(*) AS retention_risk_borrowers "
            f"FROM {BORROWER_360} "
            "WHERE is_current_customer = TRUE "
            "AND array_contains(segment_codes, 'retention')"
        ),
        sql_result_rows=[{"retention_risk_borrowers": 6638}],
        trusted_assets=[BORROWER_360],
        conversation_id="conv-retention",
        message_id="msg-retention-repaired",
    )

    assert _needs_genie_sql_repair(question, bad_result) is True
    sql = _SqlClient()
    # Live-first posture: canonical retention-risk SQL is the degraded fallback,
    # exercised with the breaker open (live Genie unavailable).
    client = _GenieClient([bad_result, repaired_result], breaker_state="open")
    repo = DatabricksGenieRepository(client, sql)  # type: ignore[arg-type]
    response = repo.respond(question)

    assert response.source == "trusted_sql"
    assert response.metric_value == "6,638"
    assert response.table_rows == [
        {
            "retention_risk_borrowers": 6638,
            "refreshed_at": "2026-05-09T00:00:00Z",
        }
    ]
    assert "lien-change" not in (response.sql_query or "")
    assert "signal_value = 'competitor'" not in (response.sql_query or "")
    assert "array_contains(segment_codes, 'retention')" in (response.sql_query or "")
    assert response.proof is not None
    assert response.proof.trusted is True
    assert sql.sql == response.sql_query
    assert client.questions == []
    assert any(
        "Live Genie is temporarily unavailable" in gap
        for gap in response.proof.known_data_gaps
    )
