"""Growth endpoint proof for Round 15 structural consent evidence."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.main import app
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import (
    GrowthAgentPromptRunRequest,
    _contains_borrower_cta_contradiction,
)
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.test_growth_agent_api import _FakeLakebaseClient, _FakeSqlClient

_UNSAFE_OBJECTIVES = (
    "A standing email opt-out remains active. Email that recipient.",
    "The borrower sent STOP yesterday. Send her a text.",
    "Nobody checks this mailbox. Reply YES to review options.",
    "Replies vanish before anyone reads. Reply YES to review options.",
    "Replies are routed into a void. Reply YES to review options.",
    "The email opt-out, which is on file. Email that recipient.",
    "This inbox accepts replies, but no employee reads it. Reply YES to review options.",
    "This inbox accepts replies, but none of the employees reads it. Reply YES to review options.",
    "The borrower, who said STOP, and text them.",
    "The recipient opted out email and email that recipient.",
    "Our provider, which suppresses inbound reply, invites reply YES.",
    "The borrower sent STOP yesterday. Send those borrowers a text.",
    "The recipient opted out of email. That recipient should be emailed.",
    "The recipient opted out of email. That recipient should receive an email.",
    "The recipient opted out of calls. That recipient should receive a call.",
    "A standing phone opt-out remains active. That recipient should be called.",
    "The borrower should be emailed despite a standing email opt-out.",
)

_SAFE_OBJECTIVES = (
    "A standing email opt-out remains active. Instead, call us to review options.",
    "A standing email opt-out remains active. But call us instead.",
    "The email opt-out, which is on file. But call us instead.",
    "Call us instead; a standing email opt-out remains active.",
    "The borrower sent STOP yesterday. But call us instead.",
    "Nobody checks this mailbox. But call us instead.",
    "Replies are routed into a void. Instead, call us to review options.",
    "The recipient opted out of email. But call us instead.",
    "The recipient opted out of calls. But email them instead.",
    "Audit standing email opt-out records and report aggregate counts.",
    "Check mailbox staffing and report aggregate compliance counts.",
    "Our compliance team audits email opt-out records. Call us to review options.",
    "Employees check this mailbox daily. Reply YES to review options.",
    "Replies route to a staffed team before an employee reads them. Reply YES to review options.",
    "Our provider, which delivers inbound replies, is operational. Reply YES to review options.",
)


@contextmanager
def _endpoint_client(
    sql: _FakeSqlClient,
    lakebase: _FakeLakebaseClient,
    audit_store: InMemoryAuditStore,
) -> Iterator[TestClient]:
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_audit_store] = lambda: audit_store
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        app.dependency_overrides.pop(get_audit_store, None)


@pytest.mark.parametrize("objective", _UNSAFE_OBJECTIVES)
def test_round15_unsafe_objectives_reject_before_planners_and_side_effects(
    objective: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def planner_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsafe objective reached a planner")

    monkeypatch.setattr(growth_agent_api, "plan_growth_agent_prompt", planner_must_not_run)
    monkeypatch.setattr(
        growth_agent_compose_api,
        "compose_growth_agent_plan",
        planner_must_not_run,
    )
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    audit_store = InMemoryAuditStore()
    with _endpoint_client(sql, lakebase, audit_store) as client:
        run_response = client.post(
            "/api/growth-agent/agent/run",
            json={
                "prompt": objective,
                "save_monitor": True,
                "monitor_name": "Round 15 Consent Review",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        compose_response = client.post(
            "/api/growth-agent/agent/compose",
            json={"objective": objective, "execute": True},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )

    assert run_response.status_code == 422
    assert compose_response.status_code == 422
    assert objective not in run_response.text
    assert objective not in compose_response.text
    assert sql.calls == []
    assert lakebase.executes == []
    assert lakebase.fetchalls == []
    assert lakebase.runs == []
    assert lakebase.audit_events == []
    assert lakebase.monitors == []
    assert lakebase.notification_drafts == []
    assert audit_store.list() == []


@pytest.mark.parametrize("objective", _UNSAFE_OBJECTIVES)
def test_round15_growth_schemas_reject_structural_contradictions(objective: str) -> None:
    assert _contains_borrower_cta_contradiction(objective)
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _SAFE_OBJECTIVES)
def test_round15_growth_schemas_preserve_replacements_and_safe_operations(
    objective: str,
) -> None:
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective


def test_round15_growth_schema_still_rejects_human_name_pii() -> None:
    objective = "A standing email opt-out remains active. Email Maria Garcia."
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=objective)
