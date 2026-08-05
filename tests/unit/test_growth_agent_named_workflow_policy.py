"""Named-workflow and scoped safe-context proof for Growth requests."""

from typing import Any

import pytest
from fastapi import HTTPException

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as growth_agent_compose_api
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.growth_agent_objective_intent import (
    GrowthNamedWorkflowFamily,
    classify_growth_objective_intent,
)
from backend.services.growth_agent_workflows import planned_workflow
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)

_NAMED_WORKFLOW_BASE_COMMANDS = (
    "Find prime refinance opportunities for branch review.",
    "Prepare borrower story dossiers.",
    "Track listed-for-sale purchase opportunities.",
    "Monitor competitor recapture opportunities.",
    "Find high equity or HELOC opportunities.",
    "Find stale approved leads.",
    "Check source freshness.",
)

_GENERIC_COMMAND_CLAUSES = (
    "Build a reviewed custom workflow.",
    "Create a reviewed segment workflow.",
    "Open a governed cohort.",
)

_SOURCE_FRESHNESS_VALID_COMMANDS = (
    "Check source freshness.",
    "Check source readiness.",
    "Review source freshness.",
    "Review source readiness.",
    "Monitor feed readiness.",
    "Monitor refreshed sources.",
    "Check freshness for several feeds.",
    "Run the source freshness sentinel.",
)

_SOURCE_FRESHNESS_REQUIRED_REJECTIONS = (
    "Check source freshness and create a plan.",
    "Review source readiness and compose a plan.",
    "Create a cohort around source freshness.",
    "Compose a source freshness plan covering live, stale, and pending feeds.",
)

_SOURCE_FRESHNESS_MIXED_CROSS_PRODUCT = tuple(
    dict.fromkeys(
        case_variant(f"{source_command}{separator}{unsupported_command}.")
        for source_command in (
            "Check source freshness",
            "Review source readiness",
            "Monitor feed freshness",
            "Check readiness for several feeds",
        )
        for unsupported_command in (
            "create a plan",
            "compose a reviewed plan",
            "build a segment plan",
            "open a governed cohort",
        )
        for separator in (" and ", ". ", "; ", "\n", ", then ", " — ")
        for case_variant in (str.lower, str.upper, str.title)
    )
)

_NAMED_WORKFLOW_NEGATIVES = (
    "Do not check source freshness.",
    "Avoid stale approved leads.",
    "Do not review branch capacity.",
    "Don't prepare borrower story dossiers.",
    "Source freshness is not required.",
    "Check source freshness but do not act.",
    "Prepare dossiers without preparing them.",
    "Please under no circumstances check source freshness.",
    "For this request please do not check source freshness.",
    "In this workflow we should avoid source freshness review.",
    "I would prefer that you avoid borrower story dossiers.",
    "In this workflow please avoid stale approved leads.",
    "Do not check sources.",
    "Do not prepare customer 360 dossiers.",
    "Do not review loan officer capacity.",
    "Prepare borrower story dossiers for high equity opportunities.",
    "Find high equity opportunities for branch capacity review.",
    "Check source freshness and prepare borrower story dossiers.",
    "Monitor competitor recapture opportunities and find high equity or HELOC opportunities.",
    "Track listed-for-sale purchase opportunities and monitor competitor recapture opportunities.",
    "Track listed-for-sale purchase opportunities and find high equity or HELOC opportunities.",
    "Monitor competitor recapture opportunities and find refi candidates.",
    "Track listed-for-sale purchase opportunities and find HELOC candidates.",
    "Check source freshness. Build a reviewed custom workflow.",
    "Prepare borrower story dossiers. Build a reviewed custom workflow.",
    "Find stale approved leads. Build a reviewed custom workflow.",
    *_SOURCE_FRESHNESS_REQUIRED_REJECTIONS,
) + tuple(
    f"{named_command} {generic_command}"
    for named_command in _NAMED_WORKFLOW_BASE_COMMANDS
    for generic_command in _GENERIC_COMMAND_CLAUSES
)

_NAMED_WORKFLOW_POSITIVES = (
    (
        "Find prime refinance opportunities for branch review.",
        GrowthNamedWorkflowFamily.REFI_BRANCH,
        "daily_refi_brief",
    ),
    (
        "Prepare borrower story dossiers for the top opportunities.",
        GrowthNamedWorkflowFamily.DOSSIER,
        "borrower_dossier_review",
    ),
    (
        "Track listed-for-sale purchase opportunities in Illinois.",
        GrowthNamedWorkflowFamily.LISTING,
        "listing_watch",
    ),
    (
        "Monitor competitor recapture opportunities.",
        GrowthNamedWorkflowFamily.COMPETITOR_RECAPTURE,
        "competitor_recapture_monitor",
    ),
    (
        "Find high equity or HELOC opportunities.",
        GrowthNamedWorkflowFamily.HIGH_EQUITY_HELOC,
        "high_equity_heloc_watch",
    ),
    (
        "Find stale approved leads.",
        GrowthNamedWorkflowFamily.BRANCH_CAPACITY,
        "branch_capacity_review",
    ),
    (
        "Check source freshness before I demo this.",
        GrowthNamedWorkflowFamily.SOURCE_FRESHNESS,
        "source_freshness_sentinel",
    ),
)


@pytest.mark.parametrize("objective", _SOURCE_FRESHNESS_VALID_COMMANDS)
def test_source_freshness_closed_grammar_accepts_only_reviewed_command_objects(
    objective: str,
) -> None:
    run_request = GrowthAgentPromptRunRequest(prompt=objective)
    compose_request = ComposePlanRequest(objective=objective)

    assert run_request.prompt == objective
    assert compose_request.objective == objective
    assert (
        classify_growth_objective_intent(objective).named_family
        is GrowthNamedWorkflowFamily.SOURCE_FRESHNESS
    )
    assert planned_workflow(run_request)[0].id == "source_freshness_sentinel"


@pytest.mark.parametrize("objective", _SOURCE_FRESHNESS_MIXED_CROSS_PRODUCT)
def test_source_freshness_closed_grammar_rejects_mixed_commands_across_syntax(
    objective: str,
) -> None:
    with pytest.raises(HTTPException, match="one reviewed named workflow"):
        classify_growth_objective_intent(objective)
    with pytest.raises((HTTPException, ValueError)):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises((HTTPException, ValueError)):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize(("objective", "family", "workflow_id"), _NAMED_WORKFLOW_POSITIVES)
def test_typed_named_workflow_classification_routes_exact_catalog_workflow(
    objective: str,
    family: GrowthNamedWorkflowFamily,
    workflow_id: str,
) -> None:
    request = GrowthAgentPromptRunRequest(prompt=objective)
    assert ComposePlanRequest(objective=objective).objective == objective
    assert classify_growth_objective_intent(objective).named_family is family
    workflow, _ = planned_workflow(request)

    assert workflow.id == workflow_id


@pytest.mark.parametrize(
    ("objective", "family", "workflow_id"),
    [
        (
            "Check source freshness. Monitor refreshed sources.",
            GrowthNamedWorkflowFamily.SOURCE_FRESHNESS,
            "source_freshness_sentinel",
        ),
        (
            "Prepare borrower story dossiers. Review the dossier.",
            GrowthNamedWorkflowFamily.DOSSIER,
            "borrower_dossier_review",
        ),
        (
            "Find stale approved leads. Review branch capacity.",
            GrowthNamedWorkflowFamily.BRANCH_CAPACITY,
            "branch_capacity_review",
        ),
        (
            "Check source freshness. Before I demo this.",
            GrowthNamedWorkflowFamily.SOURCE_FRESHNESS,
            "source_freshness_sentinel",
        ),
        (
            "Prepare borrower story dossiers. For the top opportunities.",
            GrowthNamedWorkflowFamily.DOSSIER,
            "borrower_dossier_review",
        ),
        (
            "Find stale approved leads. For branch manager review.",
            GrowthNamedWorkflowFamily.BRANCH_CAPACITY,
            "branch_capacity_review",
        ),
    ],
)
def test_named_workflow_accepts_only_same_family_commands_or_closed_context(
    objective: str,
    family: GrowthNamedWorkflowFamily,
    workflow_id: str,
) -> None:
    request = GrowthAgentPromptRunRequest(prompt=objective)
    compose_request = ComposePlanRequest(objective=objective)

    assert request.prompt == objective
    assert compose_request.objective == objective
    assert classify_growth_objective_intent(objective).named_family is family
    assert planned_workflow(request)[0].id == workflow_id


@pytest.mark.parametrize(
    ("objective", "workflow_id"),
    [
        ("Track listed-for-sale purchase opportunities in Texas.", "listing_watch"),
        ("Track listed-for-sale purchase opportunities across current coverage.", "listing_watch"),
        ("Monitor competitor recapture opportunities.", "competitor_recapture_monitor"),
        ("Find high equity or HELOC opportunities.", "high_equity_heloc_watch"),
    ],
)
def test_exact_named_catalog_objectives_execute_their_reviewed_workflow(
    objective: str,
    workflow_id: str,
) -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": objective},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["id"] == workflow_id
    assert lakebase.runs[0]["workflow_id"] == workflow_id
    assert len(sql.calls) == 1


@pytest.mark.parametrize("objective", _NAMED_WORKFLOW_NEGATIVES)
def test_named_workflow_policy_rejects_before_planners_and_stores(
    objective: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner_calls: list[str] = []
    monkeypatch.setattr(
        growth_agent_api,
        "plan_growth_agent_prompt",
        lambda *args, **kwargs: planner_calls.append("run"),
    )
    monkeypatch.setattr(
        growth_agent_compose_api,
        "compose_growth_agent_plan",
        lambda *args, **kwargs: planner_calls.append("compose"),
    )
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        run_response = client.post(
            "/api/growth-agent/agent/run",
            json={
                "prompt": objective,
                "save_monitor": True,
                "monitor_name": "Named Workflow Review",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        compose_response = client.post(
            "/api/growth-agent/agent/compose",
            json={"objective": objective, "execute": True},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert run_response.status_code == 422, run_response.text
    assert compose_response.status_code == 422, compose_response.text
    assert objective not in run_response.text
    assert objective not in compose_response.text
    assert planner_calls == []
    assert sql.calls == []
    assert lakebase.executes == []
    assert lakebase.fetchalls == []
    assert lakebase.runs == []
    assert lakebase.audit_events == []
    assert lakebase.monitors == []
    assert lakebase.notification_drafts == []


@pytest.mark.parametrize(
    ("prompt", "segment_code"),
    [
        (
            "Build a refi cohort; health information is excluded from campaign eligibility.",
            "itm",
        ),
        (
            "Select households with high equity. Eligibility does not depend on health status.",
            "equity",
        ),
        (
            "Find refinance opportunities. Our carrier routes inbound replies to a staffed team. "
            "Reply YES to review options.",
            "itm",
        ),
        (
            "Find refinance opportunities. Borrowers may reply YES to request a staffed review.",
            "itm",
        ),
    ],
)
def test_safe_nonrouting_health_and_cta_clauses_reach_real_run_policy(
    prompt: str,
    segment_code: str,
) -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": prompt},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["workflow"]["id"] == "custom_segment_watch"
    assert body["criteria"]["lead_queue_filters"]["segment_codes"] == [segment_code]
    assert f"array_contains(b.segment_codes, '{segment_code}')" in sql.calls[0][0]
    assert len(lakebase.runs) == 1
    assert len(lakebase.audit_events) == 1
