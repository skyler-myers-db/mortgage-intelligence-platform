"""Outcome-triggered read path: unmapped copilot objectives run live analysis."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

import backend.services.growth_agent_live_analysis as live_analysis_service
from backend.main import app
from backend.services.databricks_sql import get_sql_client
from backend.services.genie_answers import GenieMessageResponse, GenieReasoningStep
from backend.services.lakebase import get_lakebase_client


class _StubGenieRepo:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
        **_: Any,
    ) -> GenieMessageResponse:
        self.questions.append(question)
        return GenieMessageResponse(
            conversation_id="conv-live",
            message_id="msg-live",
            question=question,
            question_hash="hash1234",
            answer="Live governed analysis of the objective. Source: mip.gold.borrower_360",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            sql_query="SELECT 1 FROM mip.gold.borrower_360",
            row_count=4,
            reasoning_trace=[
                GenieReasoningStep(
                    kind="live", content="Answered live over mip.gold.borrower_360."
                )
            ],
        )


class _StubSqlClient:
    def execute(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        return []

    def execute_one(self, *_: Any, **__: Any) -> dict[str, Any] | None:
        return None


class _StubLakebase:
    @contextmanager
    def transaction(self) -> Any:
        class _Conn:
            def execute(self, *_: Any, **__: Any) -> Any:
                return None

        yield _Conn()


def _client() -> TestClient:
    app.dependency_overrides[get_sql_client] = lambda: _StubSqlClient()
    app.dependency_overrides[get_lakebase_client] = lambda: _StubLakebase()
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_sql_client, None)
    app.dependency_overrides.pop(get_lakebase_client, None)


def test_unmapped_objective_runs_read_only_live_analysis(monkeypatch) -> None:
    stub_repo = _StubGenieRepo()
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(live_analysis_service, "get_genie_answer_repository", lambda: stub_repo)
    monkeypatch.setattr(
        live_analysis_service,
        "write_audit_event_in_transaction",
        lambda conn, **kw: audits.append(kw),
    )
    client = _client()
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "Ponder the strategic posture of our mortgage book holistically."},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["id"] == "live_analysis"
    assert body["execution_mode"] == "genie_conversation"
    assert body["trace_kind"] == "genie_conversation"
    assert body["agent_reasoning"].startswith("Live governed analysis")
    assert body["genie_conversation_id"] == "conv-live"
    assert body["route"] == "/ask-genie"
    assert stub_repo.questions == [
        "Ponder the strategic posture of our mortgage book holistically."
    ]
    assert audits and audits[0]["action"] == "growth_agent.live_analysis"
    checks = {c["label"]: c["status"] for c in body["policy_checks"]}
    assert checks.get("No state written") == "passed"


def test_guard_hit_objective_still_refuses(monkeypatch) -> None:
    monkeypatch.setattr(
        live_analysis_service,
        "get_genie_answer_repository",
        lambda: (_ for _ in ()).throw(AssertionError("must not run genie for guarded prompts")),
    )
    client = _client()
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "Rank borrowers by race for our next campaign."},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()
    assert response.status_code == 422
