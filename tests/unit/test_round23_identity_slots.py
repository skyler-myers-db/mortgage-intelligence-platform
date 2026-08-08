"""Round 23 identity-slot grammar and business-copy safe controls."""

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
from tests.unit.growth_refusal_contract import (
    GROWTH_REFUSAL_MESSAGE_RE,
    assert_only_refusal_audit_events,
)
from tests.unit.test_growth_agent_api import _FakeLakebaseClient, _FakeSqlClient

_DISCLOSURE = (
    "Summit Mortgage, NMLS #123456. Equal Housing Lender. "
    "Reply unsubscribe to opt out."
)

_IDENTITY_SLOTS = (
    # Copular correspondence headers.
    "Subject is siobhan quigley",
    "Topic is xochitl quintero",
    "Reference is temilade okafor",
    "Ref line is mireille adeyemi",
    # Delivery roles and destinations.
    "Delivery recipient: aveline maris quade",
    "Delivery destination is nerys alouette",
    "Distribution addressee iolanthe zvonarek",
    "Dispatch destination: saoirse nkomo",
    # Direct-object copy plus middle-initial and multi-part variants.
    "Please copy siobhan quigley",
    "Please carbon copy xochitl q quintero",
    "Recipient: siobhan q. quigley",
    "Addressee aveline maris quade",
    "Copy to nerys j alouette",
    "Please deliver to mireille s adeyemi",
)

_SAFE_BUSINESS_SLOTS = (
    # Reported ordinary correspondence values.
    "Subject: payment update",
    "Topic: refinance savings",
    "Reference: your mortgage",
    "Recipient: legal counsel",
    "Addressee: compliance officer",
    "Delivery destination: branch office",
    # The safe grammar composes unseen mortgage concepts and organizational
    # roles rather than admitting only the six pairs above.
    "Subject is escrow analysis",
    "Topic is home equity options",
    "Reference is monthly payment reminder",
    "Delivery recipient: privacy officer",
    "Delivery destination: regional branch office",
    "Please copy underwriting manager",
    "Please deliver to customer support team",
    "Subject: summit mortgage",
)
_SAFE_GROWTH_SLOTS = tuple(
    safe_slot
    for safe_slot in _SAFE_BUSINESS_SLOTS
    if safe_slot not in {"Topic: refinance savings", "Please deliver to customer support team"}
)


def _variant(*, subject: str = "Mortgage options review", body: str) -> object:
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


@pytest.mark.parametrize("identity_slot", _IDENTITY_SLOTS)
def test_round23_identity_slots_fail_every_shared_schema_boundary(identity_slot: str) -> None:
    objective = f"{identity_slot}. Build a custom cohort for refi signals."
    assert contains_borrower_copy_contextual_name(identity_slot)

    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body=f"{identity_slot}. Reply YES to review mortgage options.")
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(subject=identity_slot, body="Reply YES to review mortgage options.")
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=objective)
    with pytest.raises(AuditMetadataValueViolation, match="human-name-shaped"):
        build_safe_audit_metadata({"draft_subject": identity_slot}, action="outreach.approve")


@pytest.mark.parametrize("safe_slot", _SAFE_BUSINESS_SLOTS)
def test_round23_business_slot_grammar_survives_shared_boundaries(safe_slot: str) -> None:
    assert not contains_borrower_copy_contextual_name(safe_slot)
    assert _variant(body=f"{safe_slot}. Reply YES to review mortgage options.")

    assert (
        build_safe_audit_metadata({"draft_subject": safe_slot}, action="outreach.approve")[
            "draft_subject"
        ]
        == safe_slot
    )


@pytest.mark.parametrize("safe_slot", _SAFE_GROWTH_SLOTS)
def test_round23_business_slot_grammar_survives_growth_schemas(safe_slot: str) -> None:
    objective = f"{safe_slot}. Review governed mortgage opportunities."
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective


@pytest.mark.parametrize("identity_slot", _IDENTITY_SLOTS)
def test_round23_final_body_and_subject_reject_identity_slots(identity_slot: str) -> None:
    body = f"{identity_slot}. Reply YES to review mortgage options. {_DISCLOSURE}"
    with pytest.raises(HTTPException, match="human-name-shaped"):
        outreach_mod._assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=SimpleNamespace(body=_DISCLOSURE),
            channel="email",
        )
    with pytest.raises(HTTPException, match="human-name-shaped"):
        outreach_mod._assert_final_draft_subject(
            draft_subject=identity_slot,
            channel="email",
        )


@pytest.mark.parametrize("safe_slot", _SAFE_BUSINESS_SLOTS)
def test_round23_final_body_and_subject_preserve_business_slots(safe_slot: str) -> None:
    body = f"{safe_slot}. Reply YES to review mortgage options. {_DISCLOSURE}"
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
            draft_subject=safe_slot,
            channel="email",
        )
        == safe_slot
    )


@pytest.mark.parametrize("identity_slot", _IDENTITY_SLOTS[::4])
def test_round23_identity_slots_stop_before_planners_and_writes(
    identity_slot: str,
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
    audit = InMemoryAuditStore()
    objective = f"{identity_slot}. Build a custom cohort for refi signals."
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
    assert identity_slot not in run_response.text
    assert identity_slot not in compose_response.text
    assert sql.calls == []
    assert lakebase.executes == []
    assert lakebase.fetchalls == []
    assert lakebase.runs == []
    assert lakebase.audit_events == []
    assert lakebase.monitors == []
    assert lakebase.notification_drafts == []
    # The refusal is recorded; no run/monitor/draft write happens.
    assert_only_refusal_audit_events(audit)


def test_round23_configured_multiword_lender_remains_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings, "mip_lender_name", "Entrada Home Finance")
    safe_slot = "Subject is entrada home finance"

    assert not contains_borrower_copy_contextual_name(safe_slot)
    assert _variant(body=f"{safe_slot}. Reply YES to review mortgage options.")
    assert contains_borrower_copy_contextual_name("Subject is entrada home borrower")
