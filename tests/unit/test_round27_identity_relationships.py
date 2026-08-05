"""Round 27 borrower-identity relationship grammar regressions."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.api import outreach as outreach_mod
from backend.main import app
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
    get_audit_store,
)
from backend.services.databricks_sql import get_sql_client
from backend.services.lakebase import get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.test_growth_agent_api import _FakeLakebaseClient, _FakeSqlClient

_DISCLOSURE = (
    "Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out."
)

_IDENTITY_RELATIONSHIPS = (
    # Copular correspondence headers and accountable-party roles.
    "Attn is siobhan q. quigley",
    "Attention is xochitl q quintero",
    "To is mireille s. adeyemi",
    "Sender is siobhan q quigley",
    "The sender is mireille s. adeyemi",
    "Author is xochitl q. quintero",
    "Signer is mireille s adeyemi",
    "Contact is siobhan q. quigley",
    "Correspondent is xochitl q quintero",
    "Authorized representative is mireille s. adeyemi",
    "Point of contact: siobhan q quigley",
    # Attribution and source relations.
    "Signed by xochitl q. quintero",
    "This email was authored by mireille s adeyemi",
    "This notice comes from siobhan q. quigley",
    # Artifact-bounded interaction relations.
    "Write to xochitl q quintero about this notice",
    "Reply with mireille s. adeyemi about this email",
    "Reach out to siobhan q quigley about this mortgage review",
    "Get in touch with xochitl q. quintero regarding this communication",
    "Discuss this mortgage review with mireille s adeyemi",
    # Complete notification and benefit predicates.
    "Keep siobhan q. quigley informed",
    "For benefit of xochitl q quintero",
    "For the benefit of mireille s. adeyemi",
)

_SAFE_RELATIONSHIP_VALUES = (
    "Attn is compliance team",
    "Attention is our compliance team",
    "To is compliance team",
    "Sender is compliance team",
    "The sender is compliance team",
    "Author is legal counsel",
    "Signer is compliance officer",
    "Contact is customer support team",
    "Correspondent is servicing team",
    "Authorized representative is compliance team",
    "Point of contact: customer support team",
    "Signed by compliance team",
    "This email was authored by legal counsel",
    "This notice comes from Summit Mortgage",
    "Write to compliance team about this notice",
    "Reply with customer support team about this email",
    "Reach out to servicing team about this notice",
    "Get in touch with compliance team about this email",
    "Discuss this mortgage review with compliance team",
    "Keep compliance team informed",
    "Please keep compliance team fully informed",
    "For the benefit of compliance team",
    "Attention is required before product review",
    "Sender is configured for compliance review",
)

# These safe values also remain admissible at the stricter Growth objective
# boundary. Other organization-valued phrases above are valid borrower copy
# but intentionally retain independent Growth intent/CTA restrictions.
_SAFE_GROWTH_RELATIONSHIPS = (
    "Attn is compliance team",
    "Sender is compliance team",
    "The sender is compliance team",
    "Author is legal counsel",
    "Signer is compliance officer",
    "Correspondent is servicing team",
    "Authorized representative is compliance team",
    "Signed by compliance team",
    "This email was authored by legal counsel",
    "This notice comes from Summit Mortgage",
    "Write to compliance team about this notice",
    "Get in touch with compliance team about this email",
    "Discuss this mortgage review with compliance team",
    "Keep compliance team informed",
    "For the benefit of compliance team",
)


def _variant(*, subject: str = "Mortgage options", body: str) -> object:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@contextmanager
def _endpoint_client(
    sql: _FakeSqlClient,
    lakebase: _FakeLakebaseClient,
    audit: InMemoryAuditStore,
) -> Iterator[TestClient]:
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_sql_client, None)
        app.dependency_overrides.pop(get_lakebase_client, None)
        app.dependency_overrides.pop(get_audit_store, None)


@pytest.mark.parametrize("relationship", _IDENTITY_RELATIONSHIPS)
def test_identity_relationships_reject_every_shared_copy_boundary(
    relationship: str,
) -> None:
    """Names in governed relationship slots must never reach approval."""

    objective = f"{relationship}. Review governed mortgage opportunities."
    body = f"{relationship}. Reply YES to review mortgage options. {_DISCLOSURE}"

    assert contains_borrower_copy_contextual_name(relationship)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body=body)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(subject=relationship, body="Reply YES to review mortgage options.")
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        ComposePlanRequest(objective=objective)
    with pytest.raises(HTTPException, match="human-name-shaped"):
        outreach_mod._assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=SimpleNamespace(body=_DISCLOSURE),
            channel="email",
        )
    with pytest.raises(HTTPException, match="human-name-shaped"):
        outreach_mod._assert_final_draft_subject(
            draft_subject=relationship,
            channel="email",
        )
    with pytest.raises(AuditMetadataValueViolation, match="human-name-shaped"):
        build_safe_audit_metadata(
            {"draft_subject": relationship},
            action="outreach.approve",
        )


@pytest.mark.parametrize("relationship", _SAFE_RELATIONSHIP_VALUES)
def test_closed_organization_values_remain_valid_borrower_copy(
    relationship: str,
) -> None:
    """Reviewed organization/content values must not be mistaken for people."""

    body = f"{relationship}. Reply YES to review mortgage options. {_DISCLOSURE}"
    assert not contains_borrower_copy_contextual_name(relationship)
    assert _variant(body=body)
    assert _variant(subject=relationship, body="Reply YES to review mortgage options.")
    assert (
        outreach_mod._assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=SimpleNamespace(body=_DISCLOSURE),
            channel="email",
        )
        == body
    )
    assert (
        outreach_mod._assert_final_draft_subject(
            draft_subject=relationship,
            channel="email",
        )
        == relationship
    )
    assert (
        build_safe_audit_metadata(
            {"draft_subject": relationship},
            action="outreach.approve",
        )["draft_subject"]
        == relationship
    )


@pytest.mark.parametrize("relationship", _SAFE_GROWTH_RELATIONSHIPS)
def test_closed_organization_values_remain_valid_growth_objectives(
    relationship: str,
) -> None:
    objective = f"{relationship}. Review governed mortgage opportunities."

    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective


@pytest.mark.parametrize("relationship", _IDENTITY_RELATIONSHIPS[::4])
def test_identity_relationships_stop_before_planners_and_writes(
    relationship: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Representative HTTP requests fail closed before planning or mutation."""

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
    audit = InMemoryAuditStore()
    objective = f"{relationship}. Build a custom cohort for refi signals."

    with _endpoint_client(sql, lakebase, audit) as client:
        run_response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": objective, "save_monitor": True},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        compose_response = client.post(
            "/api/growth-agent/agent/compose",
            json={"objective": objective, "execute": True},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )

    assert run_response.status_code == 422
    assert compose_response.status_code == 422
    assert relationship not in run_response.text
    assert relationship not in compose_response.text
    assert sql.calls == []
    assert lakebase.executes == []
    assert lakebase.fetchalls == []
    assert lakebase.runs == []
    assert lakebase.audit_events == []
    assert lakebase.monitors == []
    assert lakebase.notification_drafts == []
    assert audit.list() == []
