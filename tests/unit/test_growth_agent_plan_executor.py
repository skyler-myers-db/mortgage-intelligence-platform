"""Unit tests for the deterministic composed-plan executor."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.schemas.agent_plan import ComposedPlan, PlanStep
from backend.services.growth_agent_plan_executor import (
    PlanStepResult,
    ToolExecutionContext,
    execute_plan,
)
from backend.services.growth_agent_workflows import BORROWER_360


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _FakeConn:
    def __init__(self, store: _FakeLakebase) -> None:
        self.store = store

    def execute(self, sql: str, params: dict[str, Any]) -> _Result:
        if "INSERT INTO mip_app.action_audit" in sql:
            self.store.audits.append(params)
            return _Result({"audit_id": uuid4(), "event_at": datetime.now(UTC)})
        return _Result(None)


class _FakeLakebase:
    def __init__(self) -> None:
        self.audits: list[dict[str, Any]] = []

    @contextmanager
    def transaction(self) -> Any:
        yield _FakeConn(self)


def _cohort_impl(_ctx: ToolExecutionContext, _params: dict[str, Any]) -> PlanStepResult:
    return PlanStepResult(detail="cohort read 100 rows", source_asset=BORROWER_360, row_summary=100)


def _segment_impl(_ctx: ToolExecutionContext, _params: dict[str, Any]) -> PlanStepResult:
    return PlanStepResult(detail="gated to 20 rows", source_asset=BORROWER_360, row_summary=20)


_TOOL_IMPLS = {"fn_build_cohort": _cohort_impl, "fn_segment_counts": _segment_impl}


def _plan(steps: list[PlanStep], *, requires_approval: bool = False) -> ComposedPlan:
    return ComposedPlan(
        objective_summary="test plan",
        steps=steps,
        expected_outcome="",
        risk_notes="",
        requires_approval=requires_approval,
    )


def _execute(plan: ComposedPlan) -> Any:
    return execute_plan(
        plan,
        sql_client=object(),  # unused: tool impls are injected
        lakebase=_FakeLakebase(),
        audit_store=object(),  # unused: no property lookup step
        actor="analyst@entrada.ai",
        request_id=None,
        tool_impls=_TOOL_IMPLS,
    )


def test_executes_read_steps_and_writes_per_step_audit() -> None:
    plan = _plan(
        [
            PlanStep(step_id="step-1", tool="fn_build_cohort", params={}, rationale="broad"),
            PlanStep(step_id="step-2", tool="fn_segment_counts", params={}, rationale="gate"),
        ]
    )
    execution = _execute(plan)
    assert execution.executed_step_count == 2
    assert [t.status for t in execution.trace] == ["completed", "completed"]
    assert execution.trace[0].row_summary == 100
    assert execution.trace[1].row_summary == 20
    # Two per-step audit rows + one compose summary row.
    assert len(execution.audit_event_ids) == 3
    # Each completed trace step is linked to its per-step audit event.
    assert all(t.audit_event_id for t in execution.trace)
    assert execution.approval_gate_step_id is None


def test_approval_gated_tool_stops_the_run() -> None:
    plan = _plan(
        [
            PlanStep(step_id="step-1", tool="fn_build_cohort", params={}, rationale="broad"),
            PlanStep(step_id="step-2", tool="fn_lead_queue_url", params={"segment_codes": ["itm"]}),
            PlanStep(step_id="step-3", tool="fn_segment_counts", params={}, rationale="never runs"),
        ],
        requires_approval=True,
    )
    execution = _execute(plan)
    # Only the first read step executed; the run stopped at the handoff gate.
    assert execution.executed_step_count == 1
    assert len(execution.trace) == 2
    assert execution.trace[0].status == "completed"
    gate = execution.trace[1]
    assert gate.status == "review_required"
    assert gate.approval_gate is True
    assert execution.approval_gate_step_id == "step-2"
    # No audit row was written for the un-executed gate/third step;
    # one read-step audit + one compose summary audit.
    assert len(execution.audit_event_ids) == 2


def test_per_step_audit_metadata_is_governed() -> None:
    plan = _plan([PlanStep(step_id="step-1", tool="fn_build_cohort", params={}, rationale="broad")])
    execution = _execute(plan)
    lakebase_audits = execution.audit_event_ids
    assert len(lakebase_audits) == 2  # step + compose summary
