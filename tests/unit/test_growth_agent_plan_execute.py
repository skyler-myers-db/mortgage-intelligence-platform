"""Route proofs for ``POST /api/growth-agent/agent/plan/execute`` (audit critic-01).

Execute must run exactly the plan the user reviewed: the compose response's
plan and its server digest go back in, the digest verifies for this actor,
objective and scope, the plan re-validates against the current registry to
the same canonical JSON, and ``execute_plan`` runs it. No model is called,
and any mismatch is a 409 with nothing executed and no audit row written.
"""

from __future__ import annotations

import ast
import inspect
import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import backend.api.growth_agent_compose_routes as compose_routes
import backend.services.growth_agent_composer as composer_module
import backend.services.growth_agent_plan_digest as digest_module
import backend.services.growth_agent_plan_executor as executor_module
import backend.services.growth_agent_reviewed_plan as reviewed_module
from backend.main import app
from backend.schemas.agent_plan import ComposedPlan, ComposePlanRequest, PlanStep
from backend.services.audit_store import get_audit_store
from backend.services.growth_agent_composer import ComposeOutcome, build_validated_plan
from backend.services.growth_agent_plan_digest import issue_plan_digest
from backend.services.growth_agent_plan_executor import PlanStepResult
from tests.eval.compose_scorers import load_compose_cases
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.growth_refusal_contract import assert_refused_with_audit
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)

ACTOR = "operator@example.com"
OTHER_ACTOR = "reviewer@example.com"
OBJECTIVE = "Compose a refi growth plan for review."
EXECUTE_PATH = "/api/growth-agent/agent/plan/execute"
COMPOSE_PATH = "/api/growth-agent/agent/compose"

READ_PLAN: dict[str, Any] = {
    "objective_summary": "Screen refi economics, then gate to eligible leads.",
    "steps": [
        {"step_id": "step-1", "tool": "fn_build_cohort", "params": {"states": ["IL"]}, "rationale": "Broad."},
        {
            "step_id": "step-2",
            "tool": "fn_segment_counts",
            "params": {"segment_codes": ["itm"], "segment_mode": "any", "states": ["IL"]},
            "rationale": "Gate to eligible, opted-in leads.",
        },
    ],
    "expected_outcome": "An eligible refi subset.",
    "risk_notes": "Read-only counts.",
}

GATED_PLAN: dict[str, Any] = {
    "objective_summary": "Screen, hand off for review, then compare offers.",
    "steps": [
        {"step_id": "step-1", "tool": "fn_build_cohort", "params": {}, "rationale": "Broad."},
        {"step_id": "step-2", "tool": "fn_lead_queue_url", "params": {"segment_codes": ["itm"]}, "rationale": "Handoff."},
        {"step_id": "step-3", "tool": "fn_offer_compare", "params": {}, "rationale": "Offer fit."},
    ],
    "expected_outcome": "A reviewed handoff.",
    "risk_notes": "Stops for approval.",
}


def _validated(raw: dict[str, Any], *, objective: str = OBJECTIVE, states: list[str] | None = None) -> ComposedPlan:
    request = ComposePlanRequest(objective=objective, states=states or [])
    outcome = build_validated_plan(raw, request, endpoint="mas-supervisor-endpoint")
    assert outcome.status == "composed" and outcome.plan is not None, outcome.message
    return outcome.plan


def _composed(plan: ComposedPlan) -> ComposeOutcome:
    return ComposeOutcome(
        status="composed",
        endpoint="mas-supervisor-endpoint",
        plan=plan,
        interpreted_intent="Supervisor composed a plan.",
        reasoning_summary="Deterministic tools own execution.",
    )


class _Harness:
    def __init__(self) -> None:
        self.sql = _FakeSqlClient()
        self.lakebase = _FakeLakebaseClient()
        self.audit_store = InMemoryAuditStore()
        self.client: TestClient = _client(self.sql, self.lakebase)
        app.dependency_overrides[get_audit_store] = lambda: self.audit_store

    def compose(self, outcome: ComposeOutcome, monkeypatch: pytest.MonkeyPatch, *, actor: str = ACTOR, states: list[str] | None = None) -> dict[str, Any]:
        monkeypatch.setattr(compose_routes, "compose_growth_agent_plan", lambda payload: outcome)
        response = self.client.post(
            COMPOSE_PATH,
            json={"objective": OBJECTIVE, "states": states or []},
            headers={"X-Forwarded-Email": actor},
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def execute(self, body: dict[str, Any], *, actor: str = ACTOR) -> Any:
        return self.client.post(EXECUTE_PATH, json=body, headers={"X-Forwarded-Email": actor})

    def nothing_written(self) -> bool:
        return self.lakebase.audit_events == [] and list(self.audit_store.list()) == []


@pytest.fixture
def harness() -> Any:
    instance = _Harness()
    try:
        yield instance
    finally:
        _clear_overrides()


@pytest.fixture
def execute_spy(monkeypatch: pytest.MonkeyPatch) -> list[ComposedPlan]:
    """Record the plan ``execute_plan`` receives, then run the real executor."""

    received: list[ComposedPlan] = []
    real = reviewed_module.execute_plan

    def spy(plan: ComposedPlan, **kwargs: Any) -> Any:
        received.append(plan)
        return real(plan, **kwargs)

    monkeypatch.setattr(reviewed_module, "execute_plan", spy)
    return received


def _no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every path to a planning model raises: execute must never reach one."""

    def boom(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("execute must never compose or call a model")

    monkeypatch.setattr(compose_routes, "compose_growth_agent_plan", boom)
    monkeypatch.setattr(composer_module, "compose_growth_agent_plan", boom)
    monkeypatch.setattr(composer_module, "verify_supervisor_runtime", boom)
    monkeypatch.setattr(composer_module, "query_serving_endpoint", boom)
    monkeypatch.setattr(composer_module, "make_workspace_client", boom)


def _body(composed: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "objective": OBJECTIVE,
        "states": [],
        "plan": composed["plan"],
        "plan_digest": composed["plan_digest"],
        "request_id": str(uuid4()),
    }
    body.update(overrides)
    return body


def test_execute_runs_exactly_the_reviewed_plan(
    harness: _Harness, execute_spy: list[ComposedPlan], monkeypatch: pytest.MonkeyPatch
) -> None:
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch)
    assert composed["status"] == "composed"
    assert composed["plan_digest"] and composed["plan_digest"].startswith("v1.")
    assert composed["executed"] is False and composed["trace"] == []
    _no_model(monkeypatch)

    response = harness.execute(_body(composed))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plan"] == composed["plan"]
    assert payload["plan_digest"] == composed["plan_digest"]
    assert payload["executed"] is True
    assert payload["model_endpoint"] is None
    assert payload["plan_id"]
    assert [step["status"] for step in payload["trace"]] == ["completed", "completed"]
    assert len(payload["audit_event_ids"]) == 3
    # execute_plan ran the server's re-validated plan, not the posted object.
    assert len(execute_spy) == 1
    assert type(execute_spy[0]) is ComposedPlan
    assert execute_spy[0].model_dump(mode="json") == composed["plan"]
    actions = {json.loads(row.get("metadata", "{}")).get("action") for row in harness.lakebase.audit_events}
    assert {"growth_agent.plan_step", "growth_agent.compose"} <= actions


def test_compose_signs_composed_plans_only(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch)
    assert composed["plan_digest"].startswith("v1.")
    degraded = harness.compose(
        ComposeOutcome(status="degraded", endpoint="e", degraded_reason="orchestrator_disabled", message="Off."),
        monkeypatch,
    )
    assert degraded["status"] == "degraded" and degraded["plan_digest"] is None
    invalid = harness.compose(ComposeOutcome(status="invalid", endpoint="e", message="Bad plan."), monkeypatch)
    assert invalid["status"] == "invalid" and invalid["plan_digest"] is None


def test_legacy_one_shot_compose_execute_still_signs_its_plan(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compose_routes, "compose_growth_agent_plan", lambda payload: _composed(_validated(READ_PLAN)))
    response = harness.client.post(
        COMPOSE_PATH,
        json={"objective": OBJECTIVE, "execute": True},
        headers={"X-Forwarded-Email": ACTOR},
    )
    assert response.status_code == 200, response.text
    assert response.json()["executed"] is True
    assert response.json()["plan_digest"].startswith("v1.")


@pytest.mark.parametrize(
    "tamper",
    [
        lambda plan: plan["steps"][0]["params"].update({"states": ["TX"]}),
        lambda plan: plan["steps"].reverse(),
        lambda plan: plan["steps"].pop(),
        lambda plan: plan.update({"objective_summary": "A different plan."}),
        lambda plan: plan["steps"][1].update({"rationale": "Edited after review."}),
    ],
    ids=["param", "order", "dropped-step", "summary", "rationale"],
)
def test_an_unsigned_change_is_a_409_and_nothing_runs(
    harness: _Harness,
    execute_spy: list[ComposedPlan],
    monkeypatch: pytest.MonkeyPatch,
    tamper: Any,
) -> None:
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch)
    tamper(composed["plan"])

    response = harness.execute(_body(composed))

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == compose_routes.PLAN_CONFLICT_DETAILS["digest_mismatch"]
    assert execute_spy == []
    assert harness.sql.calls == []
    assert harness.nothing_written()


@pytest.mark.parametrize(
    ("objective", "states"),
    [("Compose a HELOC growth plan for review.", []), (OBJECTIVE, ["IL"])],
    ids=["objective", "states"],
)
def test_a_changed_objective_or_scope_is_a_409(
    harness: _Harness,
    execute_spy: list[ComposedPlan],
    monkeypatch: pytest.MonkeyPatch,
    objective: str,
    states: list[str],
) -> None:
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch)
    response = harness.execute(_body(composed, objective=objective, states=states))
    assert response.status_code == 409, response.text
    assert execute_spy == [] and harness.nothing_written()


def test_a_digest_is_bound_to_the_actor_who_reviewed_it(
    harness: _Harness, execute_spy: list[ComposedPlan], monkeypatch: pytest.MonkeyPatch
) -> None:
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch, actor=ACTOR)
    response = harness.execute(_body(composed), actor=OTHER_ACTOR)
    assert response.status_code == 409, response.text
    assert execute_spy == [] and harness.nothing_written()


def _signed_body(raw_plan: dict[str, Any]) -> dict[str, Any]:
    plan = ComposedPlan.model_validate(raw_plan)
    digest = issue_plan_digest(actor=ACTOR, objective=OBJECTIVE, states=[], plan=plan)
    assert digest is not None
    return _body({"plan": plan.model_dump(mode="json"), "plan_digest": digest})


@pytest.mark.parametrize(
    "step",
    [
        {"step_id": "step-1", "tool": "fn_property_loan_lookup", "params": {"address_line": "1 Main", "zip5": "60601"}, "rationale": "Lookup."},
        {"step_id": "step-1", "tool": "fn_run_arbitrary_sql", "params": {}, "rationale": "Query."},
        {"step_id": "step-1", "tool": "fn_build_cohort", "params": {"segment_codes": ["itm"]}, "rationale": "Wrong params."},
    ],
    ids=["planner-hidden-tool", "unregistered-tool", "bad-params"],
)
def test_a_signed_plan_that_fails_the_registry_is_a_409(
    harness: _Harness, execute_spy: list[ComposedPlan], step: dict[str, Any]
) -> None:
    body = _signed_body({"objective_summary": "Signed but invalid.", "steps": [step]})

    response = harness.execute(body)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == compose_routes.PLAN_CONFLICT_DETAILS["plan_revalidation_failed"]
    assert execute_spy == []
    assert harness.sql.calls == []
    assert harness.nothing_written()


def test_a_signed_plan_that_revalidates_differently_is_a_409(
    harness: _Harness, execute_spy: list[ComposedPlan]
) -> None:
    raw = {
        "objective_summary": "Signed, valid, not canonical.",
        "steps": [{"step_id": "  step-1  ", "tool": "fn_build_cohort", "params": {}, "rationale": "Broad."}],
    }

    response = harness.execute(_signed_body(raw))

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == compose_routes.PLAN_CONFLICT_DETAILS["plan_changed"]
    assert execute_spy == [] and harness.nothing_written()


def test_a_signed_plan_that_understates_its_approval_gate_is_a_409(
    harness: _Harness, execute_spy: list[ComposedPlan]
) -> None:
    raw = dict(GATED_PLAN, requires_approval=False)
    response = harness.execute(_signed_body(raw))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == compose_routes.PLAN_CONFLICT_DETAILS["plan_changed"]
    assert execute_spy == [] and harness.nothing_written()


def test_an_unsafe_objective_is_refused_before_the_digest_is_checked(
    harness: _Harness, execute_spy: list[ComposedPlan], monkeypatch: pytest.MonkeyPatch
) -> None:
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch)
    checked: list[str] = []
    monkeypatch.setattr(reviewed_module, "verify_plan_digest", lambda *a, **k: checked.append("verify"))

    response = harness.execute(_body(composed, objective="Locate zachary quince for a refi review."))

    assert_refused_with_audit(response)
    assert "quince" not in response.text.lower()
    assert checked == []
    assert execute_spy == []
    assert harness.lakebase.audit_events == []
    assert [event.action for event in harness.audit_store.list()] == ["growth_agent.refused_prompt"]


def test_execute_never_composes_or_calls_a_model(
    harness: _Harness, execute_spy: list[ComposedPlan], monkeypatch: pytest.MonkeyPatch
) -> None:
    composed = harness.compose(_composed(_validated(GATED_PLAN)), monkeypatch)
    _no_model(monkeypatch)
    response = harness.execute(_body(composed))
    assert response.status_code == 200, response.text
    assert len(execute_spy) == 1


def test_the_reviewed_plan_module_imports_no_planner() -> None:
    forbidden = {
        "compose_growth_agent_plan",
        "verify_supervisor_runtime",
        "query_serving_endpoint",
        "workspace_client",
        "make_workspace_client",
    }
    tree = ast.parse(inspect.getsource(reviewed_module))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported & forbidden == set()
    assert modules & {
        "backend.agents.mortgage_growth_copilot",
        "backend.services.capability_serving_probes",
        "backend.services.supervisor_runtime",
    } == set()
    function_source = inspect.getsource(reviewed_module.execute_reviewed_plan)
    assert "execute_plan(" in function_source
    assert "build_validated_plan(" in function_source
    assert "verify_plan_digest(" in function_source
    for name in forbidden:
        assert name not in function_source, name


def test_execution_stops_at_the_first_approval_gate(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    composed = harness.compose(_composed(_validated(GATED_PLAN)), monkeypatch)
    assert composed["plan"]["requires_approval"] is True
    after_gate: list[dict[str, Any]] = []

    def offer_compare_spy(_ctx: Any, params: dict[str, Any]) -> PlanStepResult:
        after_gate.append(params)
        return PlanStepResult(detail="should never run", source_asset="mip.gold.borrower_360")

    monkeypatch.setattr(executor_module, "_impl_offer_compare", offer_compare_spy)

    response = harness.execute(_body(composed))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert after_gate == []
    assert [step["step_id"] for step in payload["trace"]] == ["step-1", "step-2"]
    assert payload["trace"][-1]["status"] == "review_required"
    assert payload["trace"][-1]["approval_gate"] is True
    assert payload["approval_gate_step_id"] == "step-2"
    assert payload["approval_required"] is True


def test_an_oversize_plan_is_a_422(harness: _Harness, execute_spy: list[ComposedPlan]) -> None:
    plan = {
        "objective_summary": "Oversize.",
        "steps": [{"step_id": "step-1", "tool": "fn_build_cohort", "params": {"filler": ["IL"] * 4000}, "rationale": ""}],
        "expected_outcome": "",
        "risk_notes": "",
        "requires_approval": False,
    }
    response = harness.execute(_body({"plan": plan, "plan_digest": "v1.v1.1790000000." + "A" * 43}))
    assert response.status_code == 422, response.text
    assert "refusal_reason" not in response.json()
    assert execute_spy == [] and harness.nothing_written()


def test_a_posted_plan_cannot_smuggle_extra_keys(harness: _Harness, execute_spy: list[ComposedPlan], monkeypatch: pytest.MonkeyPatch) -> None:
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch)
    composed["plan"]["steps"][0]["sql"] = "SELECT 1"
    response = harness.execute(_body(composed))
    assert response.status_code == 422, response.text
    assert execute_spy == [] and harness.nothing_written()


def test_a_missing_signing_key_disables_run_honestly(
    harness: _Harness, execute_spy: list[ComposedPlan], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_key(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("MIP_GENIE_ACTION_SECRET_CURRENT is required outside local/test app environments")

    monkeypatch.setattr(digest_module, "_current_action_token_key", no_key)
    monkeypatch.setattr(digest_module, "_action_token_keys", no_key)
    composed = harness.compose(_composed(_validated(READ_PLAN)), monkeypatch)
    assert composed["status"] == "composed"
    assert composed["plan_digest"] is None

    response = harness.execute(_body(composed, plan_digest="v1.v1.1790000000." + "A" * 43))

    assert response.status_code == 503, response.text
    assert "MIP_GENIE" not in response.text
    assert execute_spy == [] and harness.nothing_written()


def test_execute_requires_a_json_body(harness: _Harness) -> None:
    response = harness.client.post(
        EXECUTE_PATH,
        content="objective=x",
        headers={"X-Forwarded-Email": ACTOR, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415


def test_openapi_declares_the_409_and_the_415() -> None:
    responses = app.openapi()["paths"]["/api/v1/growth-agent/agent/plan/execute"]["post"]["responses"]
    assert {"200", "409", "415", "422"} <= set(responses)


def test_every_composed_golden_case_is_a_fixed_point(harness: _Harness, execute_spy: list[ComposedPlan]) -> None:
    cases = [case for case in load_compose_cases() if case.get("expected_status", "composed") == "composed"]
    assert cases, "the golden compose cases hold no composed plan"
    for case in cases:
        objective = str(case["objective"])
        plan = _validated(dict(case["model_plan"]), objective=objective)
        again = _validated(plan.model_dump(mode="json"), objective=objective)
        assert again.model_dump(mode="json") == plan.model_dump(mode="json"), case["id"]
        digest = issue_plan_digest(actor=ACTOR, objective=objective, states=[], plan=plan)
        assert digest is not None
        response = harness.execute(
            {"objective": objective, "states": [], "plan": plan.model_dump(mode="json"), "plan_digest": digest}
        )
        assert response.status_code == 200, (case["id"], response.text)
        assert response.json()["plan"] == plan.model_dump(mode="json"), case["id"]
    assert len(execute_spy) == len(cases)


def test_multi_line_objectives_round_trip(harness: _Harness, execute_spy: list[ComposedPlan], monkeypatch: pytest.MonkeyPatch) -> None:
    objective = "Compose a refi growth plan for review.\nScope it to Illinois borrowers."
    monkeypatch.setattr(compose_routes, "compose_growth_agent_plan", lambda payload: _composed(_validated(READ_PLAN)))
    composed = harness.client.post(
        COMPOSE_PATH, json={"objective": objective, "states": ["IL"]}, headers={"X-Forwarded-Email": ACTOR}
    ).json()
    response = harness.execute(_body(composed, objective=objective, states=["IL"]))
    assert response.status_code == 200, response.text


def test_step_plan_models_share_one_canonical_form() -> None:
    plan = _validated(READ_PLAN)
    posted = compose_routes.ExecutePlanRequest.model_validate(
        {"objective": OBJECTIVE, "plan": plan.model_dump(mode="json"), "plan_digest": "v1.v1.1790000000." + "A" * 43}
    ).plan
    assert digest_module.canonical_plan_json(posted) == digest_module.canonical_plan_json(plan)
    assert isinstance(posted.steps[0], PlanStep)
