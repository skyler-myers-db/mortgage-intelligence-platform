"""Round 26 explicit withdrawal and unattended-response governance proof."""

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.api.outreach import (
    _assert_disclosure_backed_draft_body,
    _assert_final_draft_subject,
)
from backend.main import app
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_cta_evidence import (
    contains_borrower_cta_contradiction,
    negative_borrower_cta_evidence,
)
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
    get_audit_store,
)
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out."
)

_WITHDRAWAL_FACTS = (
    "The borrower cancels all future calls.",
    "The customer terminated all future calls.",
    "The recipient withdraws all future calls.",
    "The borrower withdrew from future calls.",
    "The client refused all future calls.",
    "Calls are refused by the borrower.",
    "The applicant vetoed all future calls.",
    "The borrower vetoes future calls.",
    "The homeowner renounces all future calls.",
    "The borrower renounces call permission.",
    "The prospect opted against all future calls.",
    "The borrower rejects being called.",
    "All future calls were canceled by the borrower.",
    "All future calls were terminated by the customer.",
    "All future calls were withdrawn by the borrower.",
    "All future calls were refused by the client.",
    "All future calls were vetoed by the applicant.",
    "All future calls were renounced by the homeowner.",
    "All future calls were opted against by the prospect.",
    "Being called was rejected by the borrower.",
)

_DEAD_RESPONSE_FACTS = (
    "Replies are neglected.",
    "Replies are being neglected.",
    "Replies get ignored.",
    "Replies receive no attention.",
    "Replies received zero staff attention.",
    "Replies are not acted upon.",
    "Replies pile up untouched.",
    "Replies gather unread.",
    "Replies are piling up unread.",
    "Replies lie unread.",
    "Replies are lying untouched.",
    "Replies sit unread.",
    "Replies are sitting unreviewed.",
    "Replies sit without being reviewed.",
    "Replies languish.",
    "Replies languish with no one reading them.",
    "Responses gather unread in the inbox.",
    "No one is reading replies.",
    "No one has reviewed responses.",
    "Nobody paid attention to replies.",
    "Replies collect dust without review.",
)

_UNSAFE_COPY = tuple(f"{fact} Call us to review options." for fact in _WITHDRAWAL_FACTS) + tuple(
    f"{fact} Reply YES to review options." for fact in _DEAD_RESPONSE_FACTS
)

_SAFE_COPY = (
    "The applicant rejected an unreviewed fee. Call us to review options.",
    "Replies receive staff attention. Reply YES to review options.",
    "Replies are neglected by automation, then staff reviews them. Reply YES.",
    "Replies pile up untouched during batching, then staff reviews them. Reply YES.",
    "Replies languish briefly, then staff reviews them. Reply YES.",
    "No one is reading replies during scanning, but staff reviews them. Reply YES.",
    "Replies collect dust temporarily, then staff reviews them. Reply YES.",
)


def _variant(*, subject: str = "Mortgage options", body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize(
    "fact",
    (
        "The borrower is canceling all future calls.",
        "The prospect is opting against all future calls.",
    ),
)
def test_progressive_withdrawal_morphology_is_negative_evidence(fact: str) -> None:
    copy = f"{fact} Call us to review options."

    assert negative_borrower_cta_evidence(copy)
    assert contains_borrower_cta_contradiction(copy)


@pytest.fixture
def isolated_growth_dependencies() -> Iterator[tuple[MagicMock, ...]]:
    """Install inert dependencies so validation must precede all side effects."""

    sql = MagicMock(name="sql_client")
    lakebase = MagicMock(name="lakebase_client")
    audit_store = MagicMock(name="audit_store")
    dependencies = (get_sql_client, get_lakebase_client, get_audit_store)
    previous = {dependency: app.dependency_overrides.get(dependency) for dependency in dependencies}
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_audit_store] = lambda: audit_store
    try:
        yield sql, lakebase, audit_store
    finally:
        for dependency, override in previous.items():
            if override is None:
                app.dependency_overrides.pop(dependency, None)
            else:
                app.dependency_overrides[dependency] = override


@pytest.mark.parametrize("copy", _UNSAFE_COPY)
def test_explicit_withdrawal_and_unattended_response_reject_every_boundary(
    copy: str,
) -> None:
    assert negative_borrower_cta_evidence(copy)
    assert contains_borrower_cta_contradiction(copy)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        ComposePlanRequest(objective=copy)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{copy} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="call to action"):
        _assert_final_draft_subject(draft_subject=copy, channel="email")
    with pytest.raises(AuditMetadataValueViolation, match="contradicts consent"):
        build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")


@pytest.mark.parametrize("copy", _SAFE_COPY)
def test_non_contact_rejection_and_staffed_same_clause_controls_remain_safe(
    copy: str,
) -> None:
    assert negative_borrower_cta_evidence(copy) == []
    assert not contains_borrower_cta_contradiction(copy)
    assert _variant(body=copy).body == copy
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{copy} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(copy)
    assert (
        build_safe_audit_metadata({"draft_body": copy}, action="outreach.approve")["draft_body"]
        == copy
    )


@pytest.mark.parametrize(
    "objective",
    (
        "The borrower cancelled all future calls. Call us to review options.",
        "All future calls were terminated by the customer. Call us to review options.",
        "Replies receive no attention. Reply YES to review options.",
        "Replies collect dust without review. Reply YES to review options.",
    ),
)
def test_new_governance_families_stop_before_planners_and_writes(
    objective: str,
    isolated_growth_dependencies: tuple[MagicMock, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_planner = MagicMock(name="plan_growth_agent_prompt")
    compose_planner = MagicMock(name="compose_growth_agent_plan")
    monkeypatch.setattr(growth_agent_api, "plan_growth_agent_prompt", run_planner)
    monkeypatch.setattr(
        growth_agent_compose_api,
        "compose_growth_agent_plan",
        compose_planner,
    )

    client = TestClient(app)
    run_response = client.post(
        "/api/growth-agent/agent/run",
        json={"prompt": objective, "save_monitor": True, "cadence": "daily"},
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
    run_planner.assert_not_called()
    compose_planner.assert_not_called()
    for dependency in isolated_growth_dependencies:
        assert dependency.mock_calls == []
