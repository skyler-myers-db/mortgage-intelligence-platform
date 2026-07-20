"""Round 17 Growth preflight and no-side-effect governance proof."""

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

_CONSENT_UNSAFE_OBJECTIVES = (
    "A request to pause calls is on file. Call us to review options.",
    "A request to refrain from emails is recorded. Email us to review options.",
    "Incoming emails terminate before staff sees them. Email us to review options.",
    "Replies are dumped before staff sees them. Reply YES to review options.",
    "Replies are swallowed before staff sees them. Reply YES to review options.",
    "Replies are held indefinitely. Reply YES to review options.",
    "STOP was received from the borrower. Text us to review options.",
    "They told us to stop. Text us to review options.",
    "It suppresses every inbound reply. Reply YES to review options.",
    "Replies go to nowhere. Reply YES to review options.",
    (
        "The provider drops all inbound replies, but phone calls route to a staffed team. "
        "Reply YES to review options."
    ),
    "The borrower instructed us to refrain from texting. Text us to review options.",
    "Inbound replies are consigned to nowhere. Reply YES to review options.",
    "Inbound replies go unseen by staff. Reply YES to review options.",
    "Every inbound response is quarantined indefinitely. Reply YES to review options.",
    (
        "The provider blocks every reply; email is monitored by staff. "
        "Reply YES to review options."
    ),
    "No one monitors incoming texts. Text us to review options.",
)

_PII_UNSAFE_OBJECTIVES = (
    "Please call us; assign this offer to talia quist.",
    "Please call us; make quincy zabinski the focus.",
    "Please call us; keep quincy zabinski in the campaign.",
    "Please call us; this offer concerns quincy zabinski.",
    "Please call us about the home owned by quincy zabinski.",
    "Please call us about the mortgage belonging to quincy zabinski.",
    "Please call us about quincy zabinski's home.",
    "Please call us; the applicant is quincy zabinski.",
    "Please call us; the account holder is quincy zabinski.",
    "Please call us; send this to quincy zabinski.",
    "Please call us; this offer is intended for quincy zabinski.",
    "Please call us; route this offer to velnora quibble.",
    "Please call us; deliver this message to velnora quibble.",
    "Please call us; velnora quibble is the recipient.",
    "Please call us; focus on velnora quibble.",
    "Please call us; this is for velnora quibble.",
    "Please call us; review velnora quibble mortgage.",
)

_SAFE_OBJECTIVES = (
    "A request to pause calls is on file. Instead, email us to review options.",
    "A request to refrain from emails is recorded. Instead, call us to review options.",
    "Replies are held indefinitely. Replacement channel is phone; call us.",
    (
        "Our provider filters inbound replies for malware then delivers them to a "
        "staffed team. Reply YES to review options."
    ),
    (
        "Our provider filters inbound replies for malware, and staff reads the replies. "
        "Reply YES to review options."
    ),
    "Please call us; assign this offer to the campaign queue.",
    "Please call us; make campaign queue the focus.",
    "Please call us; keep branch review in the queue.",
    "Please call us; this offer is intended for human review.",
    "Please call us; the applicant is fully eligible.",
    "Please call us; the account holder is in good standing.",
    "Please call us; send this to the servicing team.",
    "Please call us; this offer concerns customer experience.",
    "Please call us; focus on mortgage growth.",
    "Please call us; route this offer to product review.",
    "Please call us; deliver this message to the compliance team.",
    "Review residents dealing with high mortgage rates and report aggregate trends.",
    "Review homeowners with current loan balances and report aggregate trends.",
    "Review refi economics and mortgage underwriting conditions.",
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
def test_round17_unsafe_objectives_reject_before_planners_and_side_effects(
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
                "monitor_name": "Round 17 Consent Review",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        compose_response = client.post(
            "/api/growth-agent/agent/compose",
            json={"objective": objective, "execute": True},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )

    assert run_response.status_code == 422, run_response.text
    assert compose_response.status_code == 422, compose_response.text
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
def test_round17_growth_preflights_reject_action_bound_contradictions(
    objective: str,
) -> None:
    assert _contains_borrower_cta_contradiction(objective)
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _PII_UNSAFE_OBJECTIVES)
def test_round17_growth_preflights_reject_contextual_lowercase_names(
    objective: str,
) -> None:
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _SAFE_OBJECTIVES)
def test_round17_growth_preflights_preserve_safe_controls(objective: str) -> None:
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective
