"""Round 18 Growth identity and transport governance proof."""

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
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.audit_store import get_audit_store
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.growth_refusal_contract import (
    GROWTH_REFUSAL_MESSAGE_RE,
    assert_only_refusal_audit_events,
)
from tests.unit.test_growth_agent_api import _FakeLakebaseClient, _FakeSqlClient

_PII_UNSAFE_OBJECTIVES = (
    "Earmarked for quorla zembrix. Review mortgage options.",
    "Designate quorla zembrix as the recipient. Review mortgage options.",
    "Treat quorla zembrix as the recipient. Review mortgage options.",
    "Put quorla zembrix on the list. Review mortgage options.",
    "Place quorla zembrix in the queue. Review mortgage options.",
    "The addressee is quorla zembrix. Review mortgage options.",
    "The beneficiary is quorla zembrix. Review mortgage options.",
    "Allocate this offer to quorla zembrix. Review mortgage options.",
    "For quorla zembrix, review mortgage options.",
    "Mortgage review for quorla zembrix.",
    "Give quorla zembrix priority. Review mortgage options.",
    "Prioritize quorla zembrix. Review mortgage options.",
    "Personalize this offer for quorla zembrix. Review mortgage options.",
    "Reserve this offer for quorla zembrix. Review mortgage options.",
    "Channel this notice to quorla zembrix. Review mortgage options.",
    "Nominate quorla zembrix as the beneficiary. Review mortgage options.",
    "Slot quorla zembrix into the cohort. Review mortgage options.",
    "Award quorla zembrix priority. Review mortgage options.",
    "Confer priority on lorven quastor. Review mortgage options.",
    "Mark the recipient as lorven quastor. Review mortgage options.",
    "Name lorven quastor the beneficiary. Review mortgage options.",
    "Place the offer in front of lorven quastor. Review mortgage options.",
    "Grant priority to lorven quastor. Review mortgage options.",
    "Identify lorven quastor as the addressee. Review mortgage options.",
    "Position the notice before lorven quastor. Review mortgage options.",
    "Grant beneficiary status to lorven quastor. Review mortgage options.",
)

_CONSENT_UNSAFE_OBJECTIVES = (
    "A request for no further emails is on file. Email us to review options.",
    "The borrower made a request to hold all calls. Call us to review options.",
    "The borrower requested us to hold all calls. Call us to review options.",
    "The borrower told us to desist from messaging. Message us to review options.",
    "The borrower requested emails be put on hold. Email us to review options.",
    "The provider intercepts incoming replies. Reply YES to review options.",
    "The provider destroys incoming replies. Reply YES to review options.",
    (
        "The SMS provider destroys incoming texts, but staff reads email. "
        "Text us to review options."
    ),
    (
        "The email provider intercepts incoming emails, but SMS is monitored by staff. "
        "Email us to review options."
    ),
    (
        "The provider intercepts incoming replies, but SMS replies route to a staffed "
        "inbox. Reply YES to review options."
    ),
    (
        "An instruction that emails remain suspended is recorded. "
        "Email us to review options."
    ),
    (
        "The customer asked for messages to be discontinued. "
        "Message us to review options."
    ),
    "The provider quarantines incoming email. Email us to review options.",
    "The gateway erases every response. Reply YES to review options.",
)

_SAFE_OBJECTIVES = (
    "For more information, review mortgage options.",
    "Mortgage review for current customers.",
    "A request for no further emails is on file. Replacement channel is phone; call us.",
    "A request to hold calls is on file. Instead, email us to review options.",
    (
        "SMS provider intercepts incoming texts then routes them to a staffed SMS "
        "inbox. Text us to review options."
    ),
    (
        "Email provider intercepts incoming emails then routes them to a staffed "
        "email inbox. Email us to review options."
    ),
    (
        "The provider intercepts incoming replies then routes them to a staffed reply "
        "queue. Reply YES to review options."
    ),
    (
        "The gateway quarantines incoming email then releases it to a staffed email "
        "inbox. Email us to review options."
    ),
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
    (*_PII_UNSAFE_OBJECTIVES, *_CONSENT_UNSAFE_OBJECTIVES),
)
def test_round18_unsafe_objectives_reject_before_planners_and_side_effects(
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
                "monitor_name": "Round 18 Consent Review",
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
    # The refusal is recorded; no run/monitor/draft write happens.
    assert_only_refusal_audit_events(audit_store)


@pytest.mark.parametrize("objective", _PII_UNSAFE_OBJECTIVES)
def test_round18_growth_preflights_reject_identity_relations(objective: str) -> None:
    assert contains_borrower_copy_contextual_name(objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _CONSENT_UNSAFE_OBJECTIVES)
def test_round18_growth_preflights_reject_transport_contradictions(
    objective: str,
) -> None:
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("objective", _SAFE_OBJECTIVES)
def test_round18_growth_preflights_preserve_safe_business_controls(
    objective: str,
) -> None:
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective
