"""Per-step progress of a composed-plan execute (audit 2026-09-21 ``genie-09`` part 4, W5d).

The executor records best-effort progress (running -> gate -> recording) for
an execute that carries a ``request_id``; each write is its own short
statement, never inside the plan's audit transaction, and a failing write
changes nothing about the run. ``GET /growth-agent/agent/plan/progress`` reads
the caller's own row and writes no audit row.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import backend.services.growth_agent_plan_progress as progress_module
from backend.api import growth_agent_compose_routes
from backend.main import app
from backend.schemas.agent_plan import ComposedPlan, PlanStep
from backend.services.growth_agent_plan_executor import (
    PlanStepResult,
    ToolExecutionContext,
    execute_plan,
)
from backend.services.growth_agent_plan_progress import (
    PLAN_PROGRESS_SELECT_SQL,
    PLAN_PROGRESS_UPSERT_SQL,
    LakebasePlanProgressRecorder,
)
from backend.services.growth_agent_workflows import BORROWER_360
from backend.services.lakebase import get_lakebase_client

ACTOR = "lo@summit.example"
OTHER = "other@summit.example"
REQUEST_ID = "5a1e0000-0000-4000-8000-0000000000bb"


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Lakebase:
    """Records every statement and whether it ran inside a transaction."""

    def __init__(self, *, fail_progress: bool = False) -> None:
        self.fail_progress = fail_progress
        self.in_transaction = False
        self.statements: list[tuple[str, dict[str, Any], bool]] = []
        self.audits: list[dict[str, Any]] = []
        self.progress: dict[tuple[str, str], dict[str, Any]] = {}

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        self.statements.append((sql, params, self.in_transaction))
        if "INSERT INTO mip_app.action_audit" in sql:
            self.audits.append(params)
            return _Result({"audit_id": uuid4(), "audit_sequence": len(self.audits), "event_at": datetime.now(UTC)})
        if sql == PLAN_PROGRESS_UPSERT_SQL:
            if self.fail_progress:
                raise RuntimeError("relation mip_app.growth_agent_plan_progress does not exist")
            self.progress[(params["actor_email"], params["request_id"])] = {**params, "updated_at": datetime.now(UTC)}
        return _Result(None)

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        assert sql == PLAN_PROGRESS_SELECT_SQL
        params = params or {}
        return self.progress.get((params["actor_email"], params["request_id"]))

    def phases(self) -> list[tuple[int, int, str, str, bool]]:
        return [
            (p["step_index"], p["step_total"], p["step_tool"], p["phase"], inside)
            for sql, p, inside in self.statements
            if sql == PLAN_PROGRESS_UPSERT_SQL
        ]


def _impl(_ctx: ToolExecutionContext, _params: dict[str, Any]) -> PlanStepResult:
    return PlanStepResult(detail="read 10 rows", source_asset=BORROWER_360, row_summary=10)


def _plan(*tools: str) -> ComposedPlan:
    return ComposedPlan(
        objective_summary="test plan",
        steps=[PlanStep(step_id=f"step-{i}", tool=tool, params={}) for i, tool in enumerate(tools, start=1)],
        expected_outcome="",
        risk_notes="",
        requires_approval="fn_lead_queue_url" in tools,
    )


def _execute(plan: ComposedPlan, lakebase: _Lakebase, *, request_id: str | None = REQUEST_ID) -> Any:
    return execute_plan(
        plan,
        sql_client=object(),  # type: ignore[arg-type]
        lakebase=lakebase,  # type: ignore[arg-type]
        audit_store=object(),  # type: ignore[arg-type]
        actor=ACTOR,
        request_id=request_id,
        tool_impls={"fn_build_cohort": _impl, "fn_segment_counts": _impl},
    )


def test_progress_runs_step_by_step_then_records_before_the_audit_transaction() -> None:
    lakebase = _Lakebase()
    _execute(_plan("fn_build_cohort", "fn_segment_counts"), lakebase)
    assert lakebase.phases() == [
        (1, 2, "fn_build_cohort", "running", False),
        (2, 2, "fn_segment_counts", "running", False),
        (2, 2, "fn_segment_counts", "recording", False),
    ]
    first_audit = next(i for i, (sql, _, _) in enumerate(lakebase.statements) if "action_audit" in sql)
    last_progress = max(i for i, (sql, _, _) in enumerate(lakebase.statements) if sql == PLAN_PROGRESS_UPSERT_SQL)
    assert last_progress < first_audit, "recording is written before the audit rows, never among them"


def test_the_approval_gate_is_recorded_and_the_run_stops_there() -> None:
    lakebase = _Lakebase()
    execution = _execute(_plan("fn_build_cohort", "fn_lead_queue_url", "fn_segment_counts"), lakebase)
    assert lakebase.phases() == [
        (1, 3, "fn_build_cohort", "running", False),
        (2, 3, "fn_lead_queue_url", "gate", False),
        (1, 3, "fn_lead_queue_url", "recording", False),
    ]
    assert execution.approval_gate_step_id == "step-2"


def test_progress_is_never_written_while_a_transaction_is_open() -> None:
    lakebase = _Lakebase()
    _execute(_plan("fn_build_cohort", "fn_lead_queue_url"), lakebase)
    assert all(not inside for *_, inside in lakebase.phases())


def test_a_failing_progress_write_logs_once_and_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(progress_module, "emit", lambda _log, event, **fields: events.append((event, fields)))
    plan = _plan("fn_build_cohort", "fn_segment_counts")
    healthy, failing = _Lakebase(), _Lakebase(fail_progress=True)
    ok = _execute(plan, healthy)
    broken = _execute(plan, failing)
    assert [event for event, _ in events] == ["growth_agent_plan_progress_unavailable"]
    assert set(events[0][1]) == {"level", "exc_type"}
    assert len(failing.phases()) == 1, "writes stop after the first failure"
    assert [(t.status, t.detail, t.row_summary) for t in broken.trace] == [
        (t.status, t.detail, t.row_summary) for t in ok.trace
    ]
    assert len(broken.audit_event_ids) == len(ok.audit_event_ids) == 3


def test_nothing_is_recorded_without_a_request_id() -> None:
    lakebase = _Lakebase()
    _execute(_plan("fn_build_cohort"), lakebase, request_id=None)
    assert lakebase.phases() == []


def test_a_long_tool_name_is_capped_to_the_column() -> None:
    lakebase = _Lakebase()
    LakebasePlanProgressRecorder(lakebase, actor=ACTOR, request_id=REQUEST_ID).record(  # type: ignore[arg-type]
        1, 1, "t" * 80, "running"
    )
    assert len(lakebase.phases()[0][2]) == 64


@pytest.fixture
def client() -> Iterator[tuple[TestClient, _Lakebase]]:
    lakebase = _Lakebase()
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        yield TestClient(app), lakebase
    finally:
        app.dependency_overrides.pop(get_lakebase_client, None)


def _get(client: TestClient, request_id: str, actor: str = ACTOR) -> Any:
    return client.get(
        "/api/growth-agent/agent/plan/progress",
        params={"request_id": request_id},
        headers={"X-Forwarded-Email": actor},
    )


def test_the_route_reads_not_started_then_the_callers_own_row(client: tuple[TestClient, _Lakebase]) -> None:
    http, lakebase = client
    assert _get(http, REQUEST_ID).json() == {
        "state": "not_started", "step_index": None, "step_total": None, "step_tool": None, "updated_at": None,
    }
    LakebasePlanProgressRecorder(lakebase, actor=ACTOR, request_id=REQUEST_ID).record(  # type: ignore[arg-type]
        2, 3, "fn_segment_counts", "running"
    )
    body = _get(http, REQUEST_ID).json()
    assert (body["state"], body["step_index"], body["step_total"], body["step_tool"]) == (
        "running", 2, 3, "fn_segment_counts",
    )
    assert _get(http, REQUEST_ID, actor=OTHER).json()["state"] == "not_started"
    assert lakebase.audits == [], "the read writes no audit row"


@pytest.mark.parametrize("request_id", ["jane@summit.example", "not an id", "x" * 129])
def test_a_malformed_request_id_is_a_schema_422(client: tuple[TestClient, _Lakebase], request_id: str) -> None:
    http, _ = client
    assert _get(http, request_id).status_code == 422


def test_a_failed_read_is_not_started(monkeypatch: pytest.MonkeyPatch, client: tuple[TestClient, _Lakebase]) -> None:
    events: list[str] = []
    monkeypatch.setattr(progress_module, "emit", lambda _log, event, **_fields: events.append(event))
    http, lakebase = client

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(lakebase, "fetchone", broken)
    assert _get(http, REQUEST_ID).json()["state"] == "not_started"
    assert events == ["growth_agent_plan_progress_read_failed"]


def test_the_route_has_no_audit_store_dependency() -> None:
    source = inspect.getsource(growth_agent_compose_routes.growth_agent_plan_progress)
    assert "AUDIT EXEMPT: operational progress of the caller's own execute" in source
    assert "audit_store" not in inspect.signature(growth_agent_compose_routes.growth_agent_plan_progress).parameters
