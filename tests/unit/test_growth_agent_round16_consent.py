"""Growth endpoint proof for Round 16 consent and contextual-name guards."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.main import app
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.growth_agent import (
    GrowthAgentPromptRunRequest,
    _contains_borrower_cta_contradiction,
)
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.test_growth_agent_api import _FakeLakebaseClient, _FakeSqlClient

_CONSENT_UNSAFE_OBJECTIVES = (
    "The borrower opted out. Contact them.",
    "The borrower no longer wants emails. Email them.",
    "The borrower said no more texts. Text them.",
    "They texted STOP. Send them a text.",
    "A STOP response was received from the borrower. Text them.",
    "No human will read replies. Reply YES to review options.",
    "Every inbound reply is filtered by the provider. Reply YES to review options.",
)

_PII_UNSAFE_OBJECTIVES = (
    "Prepared for xavier patel. Call us to review options.",
    "This offer belongs to xavier patel. Call us to review options.",
)

_SAFE_OBJECTIVES = (
    "The borrower no longer wants emails. Replacement channel is phone; call us.",
    "The borrower no longer wants emails. Instead, call us to review options.",
    "The borrower no longer wants emails. But call us instead.",
    "Call us instead; the borrower no longer wants emails.",
    "The borrower said no more texts. Replacement channel is email; email them.",
    "Our provider filters inbound replies for malware then delivers them to a staffed team. Reply YES to review options.",
    "Our provider filters inbound replies for malware, and staff reads the replies. Reply YES to review options.",
    "Audit borrower opt-out preferences and report aggregate counts.",
    "Prepared for human review. Call us to review options.",
    "Prepared for branch review. Call us to review options.",
    "This offer belongs to the refinance portfolio. Call us to review options.",
    "This offer belongs to the campaign queue. Call us to review options.",
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


@pytest.mark.parametrize(
    "objective",
    (*_CONSENT_UNSAFE_OBJECTIVES, *_PII_UNSAFE_OBJECTIVES),
)
def test_round16_unsafe_objectives_reject_before_planners_and_side_effects(
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
                "monitor_name": "Round 16 Consent Review",
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


@pytest.mark.parametrize("objective", _CONSENT_UNSAFE_OBJECTIVES)
def test_round16_growth_schemas_reject_structural_consent(objective: str) -> None:
    assert _contains_borrower_cta_contradiction(objective)
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _PII_UNSAFE_OBJECTIVES)
def test_round16_growth_schemas_reject_contextual_lowercase_names(objective: str) -> None:
    assert contains_borrower_copy_contextual_name(objective)
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _SAFE_OBJECTIVES)
def test_round16_growth_schemas_preserve_safe_controls(objective: str) -> None:
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective
