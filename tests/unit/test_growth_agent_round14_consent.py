"""Growth endpoint proof for Round 14 borrower-contact contradictions."""

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
from tests.unit.growth_refusal_contract import assert_only_refusal_audit_events
from tests.unit.test_growth_agent_api import _FakeLakebaseClient, _FakeSqlClient

_UNSAFE_OBJECTIVES = (
    "Create a refi campaign for a borrower who said STOP and send them a text.",
    "Create a refi campaign for the borrower that said STOP and text the borrower.",
    "Create a refi campaign for a recipient who replied STOP and message them.",
    "Create a refi campaign for a customer who has an opt-out on file and contact them.",
    "Create a refi campaign for a customer who has opted out of email and email them.",
    "Create a refi campaign for a recipient that opted out of calls and call them.",
    "Create a refi campaign for a borrower whose email opt-out is on file and email them.",
    "Create a refi campaign for a borrower whose phone opt-out is on file and call them.",
    "Create a refi campaign for a borrower with a documented SMS opt-out and text the borrower.",
    "Create a refi campaign for a borrower. The borrower said STOP and send them a text.",
    "SMS opt-out on file. Message the recipient.",
    "Documented opt-out. Contact the borrower.",
    "Incoming replies are deleted on arrival. Reply to us.",
    "Incoming replies are deleted on arrival. Reply to the borrower.",
    "Incoming replies are deleted on arrival. Reply to them.",
    "Incoming replies are deleted on arrival. Reply to the recipient.",
    "Email opt-out on file. Email us to review options.",
    "Phone opt-out on file. Call us to review options.",
    "SMS opt-out on file. Text us to review options.",
    "Documented opt-out. Contact us to review options.",
    "Zero further contact. Contact us to review options.",
    "You said STOP. Text us to review options.",
    "Your unsubscribe request was logged. Email us to review options.",
    "Incoming replies are deleted on arrival. Reply YES to review options.",
    "This mailbox accepts messages, but nobody ever looks at it. Reply YES to review options.",
    "This inbox is never opened. Reply YES to review options.",
    "Replies are archived unread. Reply YES to review options.",
    "Incoming messages are automatically quarantined. Reply YES to review options.",
    "Our carrier blackholes inbound replies. Reply YES to review options.",
)

_SAFE_OBJECTIVES = (
    "Create a refi campaign for a borrower who said STOP and instead call them.",
    "Create a refi campaign for a recipient who replied STOP and instead call them.",
    "Create a refi campaign for a customer who has opted out of email and instead call them.",
    "Create a refi campaign for a customer who has opted out of email and text them instead.",
    "Create a refi campaign for a recipient that opted out of calls and instead email them.",
    "Create a refi campaign for a borrower whose email opt-out is on file and instead call them.",
    "Create a refi campaign for a borrower whose phone opt-out is on file and instead email them.",
    "Create a refi campaign for a borrower whose documented SMS opt-out is on file and instead email them.",
    "Create a refi campaign for a borrower. The borrower said STOP; instead, call them.",
    "Audit email opt-out records and report aggregate counts.",
    "Document phone opt-out status without calling recipients.",
    "Count documented SMS opt-outs for compliance.",
    "Check that STOP requests are logged by the compliance system.",
    "Email opt-out on file. Instead, call us to review options.",
    "Phone opt-out on file. Instead, email us to review options.",
    "SMS opt-out on file. Instead, call us to review options.",
    "You said STOP. Instead, call us to review options.",
    "Your unsubscribe request was logged. Instead, call us to review options.",
    "Incoming replies are deleted on arrival. Instead, call us to review options.",
    "This mailbox accepts messages, but nobody ever looks at it. Instead, call us to review options.",
    "This inbox is never opened. Instead, call us to review options.",
    "Replies are archived unread. Instead, call us to review options.",
    "Incoming messages are automatically quarantined. Instead, call us to review options.",
    "Our carrier blackholes inbound replies. Instead, call us to review options.",
    "The mailbox accepts messages and an employee reads it daily. Reply YES to review options.",
    "Replies are archived after an employee reviews them. Reply YES to review options.",
    "Our carrier routes inbound replies to a staffed team. Reply YES to review options.",
    "Our compliance system logs unsubscribe requests for audit. Email us to review options.",
    "Incoming messages are quarantined for malware, then released to a staffed team. Reply YES to review options.",
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
def test_round14_unsafe_objectives_reject_before_planners_and_all_side_effects(
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
                "monitor_name": "Round 14 Contact Review",
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
    # The refusal is recorded; no run/monitor/draft write happens.
    assert_only_refusal_audit_events(audit_store)


@pytest.mark.parametrize("objective", _UNSAFE_OBJECTIVES)
def test_round14_growth_request_schemas_reject_unsafe_objectives(objective: str) -> None:
    assert _contains_borrower_cta_contradiction(objective)
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _SAFE_OBJECTIVES)
def test_round14_growth_objective_validator_preserves_safe_controls(objective: str) -> None:
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective
