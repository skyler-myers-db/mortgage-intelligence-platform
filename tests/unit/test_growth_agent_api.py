from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.databricks_sql import get_sql_client
from backend.services.growth_agent_workflows import custom_workflow
from backend.services.lakebase import get_lakebase_client


class _FakeSqlClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_one(self, statement: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        params = parameters or {}
        self.calls.append((statement, params))
        return {
            "broad_total": 117404,
            "actionable_total": 5394,
            "broad_avg_score": 64.2,
            "actionable_avg_score": 73.1,
            "avg_rate_spread_bps": 187.9,
            "avg_equity_pct": 42.4,
        }


class _ImpossibleReconciliationSqlClient(_FakeSqlClient):
    def execute_one(self, statement: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((statement, parameters or {}))
        return {
            "broad_total": 10,
            "actionable_total": 20,
            "broad_avg_score": 61.0,
            "actionable_avg_score": 74.0,
            "avg_rate_spread_bps": 110.0,
            "avg_equity_pct": 45.0,
        }


class _ExecuteResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _FakeConn:
    def __init__(self, lakebase: _FakeLakebaseClient) -> None:
        self.lakebase = lakebase

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> _ExecuteResult:
        return _ExecuteResult(self.lakebase.handle_execute(sql, params or {}))


class _FakeLakebaseClient:
    def __init__(self) -> None:
        self.executes: list[tuple[str, dict[str, Any]]] = []
        self.fetchalls: list[tuple[str, dict[str, Any], int]] = []
        self.audit_events: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.monitors: list[dict[str, Any]] = []
        self.miss_next_run_select = False

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, params or {}, limit))
        return [dict(row) for row in self.monitors[:limit]]

    @contextmanager
    def transaction(self) -> Any:
        yield _FakeConn(self)

    def handle_execute(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self.executes.append((sql, params))
        now = datetime.now(UTC)
        if "FROM mip_app.growth_agent_runs" in sql and "WHERE actor_email" in sql:
            if self.miss_next_run_select:
                self.miss_next_run_select = False
                return None
            for row in self.runs:
                if (
                    row.get("actor_email") == params.get("actor_email")
                    and row.get("request_id") == params.get("request_id")
                ):
                    return dict(row)
            return None
        if "FROM mip_app.growth_agent_monitors" in sql and "last_run_id" in sql:
            for row in self.monitors:
                if (
                    row.get("actor_email") == params.get("actor_email")
                    and str(row.get("last_run_id")) == str(params.get("last_run_id"))
                ):
                    return dict(row)
            return None
        if "INSERT INTO mip_app.action_audit" in sql:
            row = {
                "audit_id": uuid4(),
                "event_at": now,
                **params,
            }
            self.audit_events.append(row)
            return row
        if "INSERT INTO mip_app.growth_agent_runs" in sql:
            for row in self.runs:
                if (
                    row.get("actor_email") == params.get("actor_email")
                    and row.get("request_id") == params.get("request_id")
                    and params.get("request_id") is not None
                ):
                    return None
            row = {
                "run_id": uuid4(),
                "audit_event_id": None,
                "created_at": now,
                **params,
            }
            self.runs.append(row)
            return {
                "run_id": row["run_id"],
                "workflow_id": row["workflow_id"],
                "criteria": row["criteria"],
                "broad_total": row["broad_total"],
                "actionable_total": row["actionable_total"],
                "broad_avg_score": row.get("broad_avg_score"),
                "actionable_avg_score": row.get("actionable_avg_score"),
                "avg_rate_spread_bps": row.get("avg_rate_spread_bps"),
                "avg_equity_pct": row.get("avg_equity_pct"),
                "route": row["route"],
                "source_assets": row["source_assets"],
                "tool_steps": row["tool_steps"],
                "policy_checks": row["policy_checks"],
                "trace_id": row.get("trace_id"),
                "tool_result_hash": row.get("tool_result_hash"),
                "specialist_agent": row.get("specialist_agent"),
                "governance_chips": row.get("governance_chips"),
                "audit_event_id": row.get("audit_event_id"),
                "created_at": row["created_at"],
            }
        if "UPDATE mip_app.growth_agent_runs" in sql:
            for row in self.runs:
                if str(row.get("run_id")) == str(params.get("run_id")):
                    row["audit_event_id"] = params["audit_event_id"]
                    return {
                        "run_id": row["run_id"],
                        "workflow_id": row["workflow_id"],
                        "criteria": row["criteria"],
                        "broad_total": row["broad_total"],
                        "actionable_total": row["actionable_total"],
                        "broad_avg_score": row.get("broad_avg_score"),
                        "actionable_avg_score": row.get("actionable_avg_score"),
                        "avg_rate_spread_bps": row.get("avg_rate_spread_bps"),
                        "avg_equity_pct": row.get("avg_equity_pct"),
                        "route": row["route"],
                        "source_assets": row["source_assets"],
                        "tool_steps": row["tool_steps"],
                        "policy_checks": row["policy_checks"],
                        "trace_id": row.get("trace_id"),
                        "tool_result_hash": row.get("tool_result_hash"),
                        "specialist_agent": row.get("specialist_agent"),
                        "governance_chips": row.get("governance_chips"),
                        "audit_event_id": row.get("audit_event_id"),
                        "created_at": row["created_at"],
                    }
            return None
        if "INSERT INTO mip_app.growth_agent_monitors" in sql:
            for existing in self.monitors:
                if (
                    existing["actor_email"] == params["actor_email"]
                    and existing["workflow_id"] == params["workflow_id"]
                    and existing["name"] == params["name"]
                ):
                    existing.update(
                        {
                            "cadence": params["cadence"],
                            "status": "active",
                            "criteria": json.loads(params["criteria"]),
                            "route": params["route"],
                            "actionable_total": params["actionable_total"],
                            "source_assets": params["source_assets"],
                            "last_run_id": params["last_run_id"],
                            "updated_at": now,
                        }
                    )
                    return existing
            row = {
                "monitor_id": uuid4(),
                "actor_email": params["actor_email"],
                "workflow_id": params["workflow_id"],
                "name": params["name"],
                "cadence": params["cadence"],
                "status": "active",
                "criteria": json.loads(params["criteria"]),
                "route": params["route"],
                "actionable_total": params["actionable_total"],
                "source_assets": params["source_assets"],
                "last_run_id": params["last_run_id"],
                "created_at": now,
                "updated_at": now,
            }
            self.monitors.append(row)
            return row
        return None


class _FailingLakebaseClient:
    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return []

    @contextmanager
    def transaction(self) -> Any:
        class _Conn:
            def execute(self, sql: str, params: dict[str, Any] | None = None) -> Any:
                raise psycopg.errors.UndefinedTable("missing growth_agent_runs")

        yield _Conn()


def _client(sql: _FakeSqlClient, lakebase: Any) -> TestClient:
    app.dependency_overrides[get_sql_client] = lambda: sql
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_sql_client, None)
    app.dependency_overrides.pop(get_lakebase_client, None)


def test_growth_agent_home_lists_governed_workflows() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.get("/api/growth-agent", headers={"X-Forwarded-Email": "operator@example.com"})
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert [workflow["id"] for workflow in body["workflows"]] == [
        "daily_refi_brief",
        "borrower_dossier_review",
        "listing_watch",
        "competitor_recapture_monitor",
        "high_equity_heloc_watch",
        "branch_capacity_review",
        "source_freshness_sentinel",
    ]
    routes = {workflow["id"]: workflow["default_route"] for workflow in body["workflows"]}
    assert routes["daily_refi_brief"].startswith("/lead-queue?")
    assert routes["borrower_dossier_review"].startswith("/lead-queue?funnel_stage=high_opportunity")
    assert routes["branch_capacity_review"].startswith("/lead-queue?")
    assert routes["source_freshness_sentinel"] == "/admin-config?panel=data-operations"
    assert body["monitors"] == []


def test_custom_segment_workflow_uses_reviewed_any_semantics_and_writes_audit() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/custom/run",
            json={
                "states": ["IL", "TX"],
                "segment_codes": ["investor", "listed", "investor"],
                "segment_mode": "any",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["id"] == "custom_segment_watch"
    assert body["workflow"]["title"] == "Custom Segment Workflow"
    assert body["criteria"]["lead_queue_filters"]["segment_codes"] == ["investor", "listed"]
    assert body["criteria"]["lead_queue_filters"]["segment_mode"] == "any"
    assert body["route"] == (
        "/lead-queue?segment_codes=investor%2Clisted&segment_mode=any"
        "&marketing_eligibility=Eligible+only&states=IL%2CTX"
    )
    statement, params = sql.calls[0]
    assert "array_contains(b.segment_codes, 'investor') OR array_contains(b.segment_codes, 'listed')" in statement
    assert "array_contains(b.segment_codes, 'investor') AND array_contains(b.segment_codes, 'listed')" not in statement
    assert "b.marketing_eligible = TRUE" in statement
    assert params == {"state_0": "IL", "state_1": "TX"}
    metadata = json.loads(lakebase.audit_events[0]["metadata"])
    assert metadata["workflow_id"] == "custom_segment_watch"
    assert metadata["workflow_title"] == "Custom Segment Workflow"
    assert metadata["result_filters"]["segment_codes"] == ["investor", "listed"]
    assert metadata["result_filters"]["segment_mode"] == "any"
    assert "Reviewed custom workflow" in json.dumps(metadata["policy_checks"])


def test_custom_segment_workflow_all_mode_and_monitor_are_safe() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    request_id = str(uuid4())
    try:
        first = client.post(
            "/api/growth-agent/custom/run",
            json={
                "states": ["FL"],
                "segment_codes": ["itm", "listed"],
                "segment_mode": "all",
                "save_monitor": True,
                "cadence": "weekly",
                "monitor_name": "Custom Segment Workflow - ITM+LISTED - FL",
                "request_id": request_id,
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        replay = client.post(
            "/api/growth-agent/custom/run",
            json={
                "states": ["FL"],
                "segment_codes": ["itm", "listed"],
                "segment_mode": "all",
                "save_monitor": True,
                "cadence": "weekly",
                "monitor_name": "Custom Segment Workflow - ITM+LISTED - FL",
                "request_id": request_id,
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["run_id"] == first.json()["run_id"]
    statement, params = sql.calls[0]
    assert "array_contains(b.segment_codes, 'itm') AND array_contains(b.segment_codes, 'listed')" in statement
    assert params == {"state_0": "FL"}
    assert len(sql.calls) == 1
    assert len(lakebase.audit_events) == 1
    assert len(lakebase.runs) == 1
    assert len(lakebase.monitors) == 1
    assert first.json()["monitor"]["workflow_id"] == "custom_segment_watch"
    assert first.json()["monitor"]["name"] == "Custom Segment Workflow - ITM+LISTED - FL"
    assert "borrower_id" not in json.dumps(first.json()["monitor"]["criteria"]).lower()


def test_custom_segment_workflow_direct_replay_skips_sql_and_cannot_add_monitor() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    request_id = str(uuid4())
    try:
        first = client.post(
            "/api/growth-agent/custom/run",
            json={
                "segment_codes": ["itm", "listed"],
                "segment_mode": "any",
                "save_monitor": False,
                "request_id": request_id,
            },
                headers={"X-Forwarded-Email": "operator@example.com"},
            )
        replay_with_monitor = client.post(
            "/api/growth-agent/custom/run",
            json={
                "segment_codes": ["itm", "listed"],
                "segment_mode": "any",
                "save_monitor": True,
                "monitor_name": "Custom Segment Workflow - ITM+LISTED",
                "request_id": request_id,
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert first.status_code == 200, first.text
    assert replay_with_monitor.status_code == 200, replay_with_monitor.text
    assert replay_with_monitor.json()["run_id"] == first.json()["run_id"]
    assert replay_with_monitor.json()["monitor"] is None
    assert len(sql.calls) == 1
    assert len(lakebase.audit_events) == 1
    assert lakebase.monitors == []


def test_custom_segment_workflow_insert_conflict_replay_cannot_add_monitor_side_effect() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    request_id = str(uuid4())
    try:
        first = client.post(
            "/api/growth-agent/custom/run",
            json={
                "segment_codes": ["itm", "listed"],
                "segment_mode": "any",
                "save_monitor": False,
                "request_id": request_id,
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        lakebase.miss_next_run_select = True
        replay_with_monitor = client.post(
            "/api/growth-agent/custom/run",
            json={
                "segment_codes": ["itm", "listed"],
                "segment_mode": "any",
                "save_monitor": True,
                "monitor_name": "Custom Segment Workflow - ITM+LISTED",
                "request_id": request_id,
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert first.status_code == 200, first.text
    assert replay_with_monitor.status_code == 200, replay_with_monitor.text
    assert replay_with_monitor.json()["run_id"] == first.json()["run_id"]
    assert replay_with_monitor.json()["monitor"] is None
    assert len(sql.calls) == 2
    assert len(lakebase.audit_events) == 1
    assert lakebase.monitors == []


def test_custom_segment_workflow_rejects_unknown_codes_and_freeform_sql() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        unknown = client.post(
            "/api/growth-agent/custom/run",
            json={"segment_codes": ["raw_clip"], "segment_mode": "any"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        empty = client.post(
            "/api/growth-agent/custom/run",
            json={"segment_codes": [], "segment_mode": "any"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
        bad_mode = client.post(
            "/api/growth-agent/custom/run",
            json={"segment_codes": ["itm"], "segment_mode": "sql: 1=1"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert unknown.status_code == 422
    assert empty.status_code == 422
    assert bad_mode.status_code == 422
    assert not lakebase.runs
    assert not sql.calls


def test_custom_segment_workflow_helper_fails_closed_for_internal_callers() -> None:
    with pytest.raises(HTTPException) as unknown:
        custom_workflow(["itm", "raw_sql"], "all")
    with pytest.raises(HTTPException) as bad_mode:
        custom_workflow(["itm"], "sql:1=1")

    assert unknown.value.status_code == 422
    assert bad_mode.value.status_code == 422
    deduped = custom_workflow(["itm", "itm", "equity"], "any")
    assert deduped.route_filters["segment_codes"] == "itm,equity"
    assert deduped.route_filters["segment_mode"] == "any"


def test_growth_agent_monitor_list_route_reads_actor_scoped_monitors() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    monitor_id = uuid4()
    run_id = uuid4()
    lakebase.monitors.append(
        {
            "monitor_id": monitor_id,
            "workflow_id": "daily_refi_brief",
            "name": "IL Refi Watch",
            "cadence": "daily",
            "status": "active",
            "criteria": {"states": ["IL"]},
            "route": "/lead-queue?segment=itm&states=IL",
            "actionable_total": 2722,
            "source_assets": ["mip.gold.borrower_360"],
            "last_run_id": run_id,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    client = _client(sql, lakebase)
    try:
        response = client.get(
            "/api/growth-agent/monitors",
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()[0]["monitor_id"] == str(monitor_id)
    assert response.json()[0]["last_run_id"] == str(run_id)


def test_prompt_agent_routes_to_source_sentinel_without_storing_raw_prompt() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "check source freshness before I demo this", "states": ["IL"]},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["id"] == "source_freshness_sentinel"
    assert body["specialist_agent"] == "data_ops_agent"
    assert body["interpreted_intent"] == "Data Ops Agent selected the global source/freshness sentinel."
    assert body["route"] == "/admin-config?panel=data-operations"
    assert body["criteria"]["states"] == []
    assert "states" not in body["criteria"]["lead_queue_filters"]
    assert body["broad_label"] == "Sources checked"
    assert body["actionable_label"] == "Live source feeds"
    assert "before i demo" not in json.dumps(lakebase.audit_events, default=str).lower()


def test_prompt_agent_custom_segments_use_reviewed_all_semantics() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={
                "prompt": "build an agent workflow for both refi and listed borrowers",
                "states": ["TX"],
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["id"] == "custom_segment_watch"
    assert body["criteria"]["lead_queue_filters"]["segment_codes"] == ["itm", "listed"]
    assert body["criteria"]["lead_queue_filters"]["segment_mode"] == "all"
    assert body["interpreted_intent"] == "Campaign Agent built a custom ALL segment workflow."
    statement, _params = sql.calls[0]
    assert "array_contains(b.segment_codes, 'itm') AND array_contains(b.segment_codes, 'listed')" in statement


def test_prompt_agent_routes_home_equity_line_to_offer_agent_before_custom_segments() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "find home equity line opportunities"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["id"] == "high_equity_heloc_watch"
    assert body["specialist_agent"] == "offer_agent"
    assert body["interpreted_intent"] == "Offer Agent selected the high-equity HELOC watch."
    assert "fn_offer_compare" in [step["tool_name"] for step in body["tool_steps"]]
    assert body["criteria"]["lead_queue_filters"]["segment_codes"] == ["permit", "equity"]


def test_prompt_agent_routes_dossier_story_to_borrower_dossier_specialist() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={"prompt": "prepare borrower story dossiers for the best opportunities"},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["id"] == "borrower_dossier_review"
    assert body["specialist_agent"] == "borrower_dossier_agent"
    assert body["criteria"]["lead_queue_filters"]["funnel_stage"] == "high_opportunity"
    assert body["route"] == "/lead-queue?funnel_stage=high_opportunity&marketing_eligibility=Eligible+only"
    assert body["tool_steps"][1]["tool_name"] == "fn_borrower_dossier_evidence"
    assert body["tool_steps"][1]["source_asset"] == "mip.gold.borrower_dossier"
    assert "Dossier privacy" in json.dumps(body["policy_checks"])
    statement, _params = sql.calls[0]
    assert "d.opportunity_score >= 75" in statement


def test_prompt_agent_save_monitor_persists_reviewed_filters_without_prompt_text() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/agent/run",
            json={
                "prompt": "Find refinance opportunities for weekly monitoring",
                "states": ["IL"],
                "save_monitor": True,
                "cadence": "weekly",
                "monitor_name": "Mortgage Growth Agent - IL",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow"]["id"] == "daily_refi_brief"
    assert body["monitor"]["name"] == "Mortgage Growth Agent - IL"
    assert body["monitor"]["cadence"] == "weekly"
    assert body["monitor"]["route"] == body["route"]
    assert body["monitor"]["actionable_total"] == body["actionable_total"]
    assert body["monitor"]["criteria"]["lead_queue_filters"]["segment_codes"] == ["itm"]
    prompt_text = "find refinance opportunities"
    response_text = json.dumps(body, default=str).lower()
    run_text = json.dumps(lakebase.runs, default=str).lower()
    persisted = json.dumps(lakebase.monitors, default=str).lower()
    audited = json.dumps(lakebase.audit_events, default=str).lower()
    assert prompt_text not in response_text
    assert prompt_text not in run_text
    assert prompt_text not in persisted
    assert prompt_text not in audited


def test_prompt_agent_rejects_pii_and_raw_identifiers() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        responses = [
            client.post(
                "/api/growth-agent/agent/run",
                json={"prompt": prompt},
                headers={"X-Forwarded-Email": "operator@example.com"},
            )
            for prompt in [
                "run this for alice@example.com",
                "run this for clip_ref_abcdef123456",
                "find borrower for CLIP 123456789",
                "run this for John Smith",
                "John Smith refi opportunities",
                "JANE DOE refi opportunities",
                "JANE Q DOE refi opportunities",
                "JOHN SMITH",
                "show Alice Johnson",
                "find refi for JANE DOE",
                "run this for 123 Main St",
                "show 742 Evergreen Terrace",
            ]
        ]
    finally:
        _clear_overrides()

    assert [response.status_code for response in responses] == [422] * len(responses)
    assert not sql.calls
    assert not lakebase.runs


def test_run_workflow_reconciles_broad_to_actionable_and_writes_audit() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"states": ["il", "IL", "tx"]},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["broad_total"] == 117404
    assert body["actionable_total"] == 5394
    assert body["specialist_agent"] == "structured_data_agent"
    assert body["trace_id"].startswith("agent-trace-")
    assert len(body["tool_result_hash"]) == 64
    assert body["governance_chips"]
    assert body["tool_steps"][0]["tool_name"] == "fn_build_cohort"
    assert body["tool_steps"][0]["result_hash"] == body["tool_result_hash"]
    assert body["criteria"]["states"] == ["IL", "TX"]
    assert body["criteria"]["lead_queue_filters"]["segment_codes"] == ["itm"]
    assert body["criteria"]["lead_queue_filters"]["segment_mode"] == "any"
    assert body["criteria"]["lead_queue_filters"]["portfolio_criteria"] == {
        "marketing_eligibility": "Eligible only",
        "states": ["IL", "TX"],
    }
    assert body["route"] == "/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL%2CTX"
    assert body["policy_checks"][2]["label"] == "Broad vs actionable reconciliation"
    assert "117,404" in body["policy_checks"][2]["detail"]
    assert "5,394" in body["policy_checks"][2]["detail"]

    statement, params = sql.calls[0]
    assert statement.count("COUNT(DISTINCT b.clip)") == 2
    assert params == {"state_0": "IL", "state_1": "TX"}

    assert len(lakebase.audit_events) == 1
    audit_params = lakebase.audit_events[0]
    metadata = json.loads(audit_params["metadata"])
    assert audit_params["event_type"] == "GROWTH_AGENT_RUN"
    assert metadata["workflow_id"] == "daily_refi_brief"
    assert metadata["broad_total"] == 117404
    assert metadata["actionable_total"] == 5394
    assert metadata["trace_id"].startswith("agent-trace-")
    assert metadata["tool_result_hash"] == body["tool_result_hash"]
    assert metadata["specialist_agent"] == "structured_data_agent"
    assert metadata["governance_chips"][0]["label"] == "PII-safe output"
    assert metadata["result_filters"]["segment_codes"] == ["itm"]
    assert metadata["result_filters"]["portfolio_criteria"]["marketing_eligibility"] == "Eligible only"
    metadata_text = json.dumps(metadata).lower()
    assert "borrower_id" not in metadata_text
    assert "subject_clip" not in metadata_text
    assert "owner_name" not in metadata_text
    assert "street" not in metadata_text
    assert str(lakebase.runs[0]["audit_event_id"]) == str(audit_params["audit_id"])


def test_workflow_metric_sql_uses_live_predicates_and_actionability_gates() -> None:
    expectations = {
        "daily_refi_brief": [
            "b.in_the_money = TRUE",
            "array_contains(b.segment_codes, 'itm')",
        ],
        "listing_watch": [
            "b.listed_for_sale = TRUE",
            "array_contains(b.segment_codes, 'listed')",
        ],
        "borrower_dossier_review": [
            "d.opportunity_score >= 75",
        ],
        "competitor_recapture_monitor": [
            "b.is_competitor_lien = TRUE",
            "b.is_competitor_lien = TRUE",
        ],
        "high_equity_heloc_watch": [
            "b.has_permit = TRUE OR b.has_heloc_propensity_trigger = TRUE",
            "b.equity_pct >= b.heloc_equity_min_applied",
            "COALESCE(b.second_pos_amount, 0) = 0",
            "array_contains(b.segment_codes, 'permit')",
            "array_contains(b.segment_codes, 'equity')",
        ],
        "branch_capacity_review": [
            "FROM mip.gold.borrower_360 b",
            "LEFT JOIN mip.gold.borrower_lifecycle_state",
            "COALESCE(ls.approval_status, 'pending') = 'approved'",
            "ls.approved_at <= current_timestamp() - INTERVAL 7 DAYS",
            "ls.outreach_at IS NULL",
        ],
        "source_freshness_sentinel": [
            "FROM mip.gold.source_readiness",
            "COUNT_IF(status = 'live')",
            "status IS NULL OR status <> 'live'",
        ],
    }
    for workflow_id, snippets in expectations.items():
        sql = _FakeSqlClient()
        lakebase = _FakeLakebaseClient()
        client = _client(sql, lakebase)
        try:
            response = client.post(
                f"/api/growth-agent/workflows/{workflow_id}/run",
                json={"states": ["IL"]},
                headers={"X-Forwarded-Email": "operator@example.com"},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 200, response.text
        statement, params = sql.calls[0]
        assert "b.equity_pct >= 35" not in statement
        for snippet in snippets:
            assert snippet in statement
        if workflow_id == "borrower_dossier_review":
            assert "d.marketing_eligible = TRUE" in statement
            assert "d.consent_status = 'opt_in'" in statement
            assert "d.suppression_reason IS NULL" in statement
            assert "UPPER(d.state) IN (:state_0)" in statement
            assert params == {"state_0": "IL"}
        elif workflow_id != "source_freshness_sentinel":
            assert "b.marketing_eligible = TRUE" in statement
            assert "b.consent_status = 'opt_in'" in statement
            assert "b.suppression_reason IS NULL" in statement
            assert "UPPER(b.state) IN (:state_0)" in statement
            assert params == {"state_0": "IL"}
        else:
            assert params == {}


def test_borrower_dossier_workflow_reads_dossier_and_evidence_assets() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/workflows/borrower_dossier_review/run",
            json={"states": ["IL"]},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    statement, params = sql.calls[0]
    assert "FROM mip.gold.borrower_dossier d" in statement
    assert "LEFT JOIN mip.gold.evidence_events ev" in statement
    assert "COUNT(DISTINCT CASE WHEN ev.clip IS NOT NULL THEN d.clip END)" in statement
    assert "UPPER(d.state) IN (:state_0)" in statement
    assert params == {"state_0": "IL"}
    assert response.json()["tool_steps"][0]["tool_name"] == "fn_borrower_dossier_evidence"


def test_impossible_reconciliation_requires_review_instead_of_false_pass() -> None:
    sql = _ImpossibleReconciliationSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    check = response.json()["policy_checks"][2]
    assert check["label"] == "Broad vs actionable reconciliation"
    assert check["status"] == "review_required"
    assert "20 eligible leads exceeds 10 broad opportunities" in check["detail"]
    metadata = json.loads(lakebase.audit_events[0]["metadata"])
    assert metadata["policy_checks"][2]["status"] == "review_required"


def test_save_monitor_persists_reviewed_filters_without_borrower_ids() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/workflows/high_equity_heloc_watch/run",
            json={
                "states": ["CA"],
                "save_monitor": True,
                "cadence": "weekly",
                "monitor_name": "West HELOC Watch",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["monitor"]["name"] == "West HELOC Watch"
    assert body["monitor"]["cadence"] == "weekly"
    assert body["criteria"]["lead_queue_filters"]["segment_codes"] == ["permit", "equity"]
    assert body["criteria"]["lead_queue_filters"]["segment_mode"] == "any"
    assert "borrower_id" not in json.dumps(body["monitor"]["criteria"]).lower()
    assert body["policy_checks"][-1]["label"] == "Monitor saved to Lakebase"
    assert lakebase.monitors[0]["actionable_total"] == 5394


def test_source_freshness_monitor_uses_global_data_ops_handoff() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/workflows/source_freshness_sentinel/run",
            json={
                "states": ["IL"],
                "save_monitor": True,
                "cadence": "daily",
                "monitor_name": "Source/Freshness Sentinel",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route"] == "/admin-config?panel=data-operations"
    assert body["criteria"]["states"] == []
    assert "states" not in body["criteria"]["lead_queue_filters"]
    assert body["monitor"]["route"] == "/admin-config?panel=data-operations"
    assert body["monitor"]["criteria"]["states"] == []
    assert "states" not in body["monitor"]["criteria"]["lead_queue_filters"]
    assert sql.calls[0][1] == {}
    assert body["actionable_label"] == "Live source feeds"


def test_state_scoped_generated_monitor_name_is_public_safe() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={
                "states": ["IL", "TX"],
                "save_monitor": True,
                "cadence": "daily",
                "monitor_name": "Daily Refi Opportunity Brief - IL, TX",
            },
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json()["monitor"]["name"] == "Daily Refi Opportunity Brief - IL, TX"


def test_competitor_workflow_keeps_relationship_filter_in_portfolio_criteria() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(
            "/api/growth-agent/workflows/competitor_recapture_monitor/run",
            json={},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    filters = response.json()["criteria"]["lead_queue_filters"]
    assert filters["portfolio_criteria"] == {
        "marketing_eligibility": "Eligible only",
        "lender_relationship": "Competitor customer",
    }
    assert response.json()["route"] == (
        "/lead-queue?lender_relationship=Competitor+customer&marketing_eligibility=Eligible+only"
    )


def test_raw_psycopg_transaction_failure_returns_safe_lakebase_503() -> None:
    sql = _FakeSqlClient()
    client = _client(sql, _FailingLakebaseClient())
    try:
        response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"states": ["IL"]},
            headers={"X-Forwarded-Email": "operator@example.com"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 503
    assert response.json()["detail"] == "lakebase is temporarily unavailable"


def test_invalid_states_and_monitor_labels_fail_closed() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        invalid_state = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"states": ["illinois"]},
        )
        invalid_monitor = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"save_monitor": True, "monitor_name": "alice@example.com"},
        )
        street_monitor = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"save_monitor": True, "monitor_name": "123 Main St"},
        )
        name_monitor = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"save_monitor": True, "monitor_name": "John Smith"},
        )
        clip_monitor = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"save_monitor": True, "monitor_name": "clip_ref_abcdef123456"},
        )
    finally:
        _clear_overrides()

    assert invalid_state.status_code == 422
    assert invalid_monitor.status_code == 422
    assert street_monitor.status_code == 422
    assert name_monitor.status_code == 422
    assert clip_monitor.status_code == 422
    assert not lakebase.runs


def test_growth_agent_request_id_replays_existing_run_without_duplicate_audit() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    request_id = str(uuid4())
    payload = {"states": ["IL", "TX"], "request_id": request_id}
    headers = {"X-Forwarded-Email": "operator@example.com"}
    try:
        first = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json=payload,
            headers=headers,
        )
        replay = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json=payload,
            headers=headers,
        )
    finally:
        _clear_overrides()

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert replay.json()["audit_event_id"] == first.json()["audit_event_id"]
    assert len(lakebase.runs) == 1
    assert len(lakebase.audit_events) == 1
    assert lakebase.runs[0]["request_id"] == lakebase.audit_events[0]["request_id"]
    assert lakebase.runs[0]["request_id"] == request_id
    assert len(sql.calls) == 1


def test_same_growth_agent_criteria_with_new_request_id_writes_fresh_audit() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    headers = {"X-Forwarded-Email": "operator@example.com"}
    try:
        first = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"states": ["IL", "TX"], "request_id": str(uuid4())},
            headers=headers,
        )
        second = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"states": ["IL", "TX"], "request_id": str(uuid4())},
            headers=headers,
        )
    finally:
        _clear_overrides()

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["run_id"] != first.json()["run_id"]
    assert second.json()["audit_event_id"] != first.json()["audit_event_id"]
    assert len(lakebase.runs) == 2
    assert len(lakebase.audit_events) == 2
    assert {row["request_id"] for row in lakebase.runs} == {
        row["request_id"] for row in lakebase.audit_events
    }


def test_growth_agent_request_id_reuse_with_different_criteria_returns_409() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    request_id = str(uuid4())
    headers = {"X-Forwarded-Email": "operator@example.com"}
    try:
        first = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"states": ["IL"], "request_id": request_id},
            headers=headers,
        )
        mismatch = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            json={"states": ["TX"], "request_id": request_id},
            headers=headers,
        )
    finally:
        _clear_overrides()

    assert first.status_code == 200, first.text
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["detail"] == "request_id already belongs to a different growth-agent run"
    assert len(lakebase.runs) == 1
    assert len(lakebase.audit_events) == 1


def test_growth_agent_post_requires_json_body_contract() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        workflow_text_response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            content='{"states":["IL"]}',
            headers={"Content-Type": "text/plain"},
        )
        workflow_form_response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            data={"states": "IL"},
        )
        workflow_missing_type_response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            content='{"states":["IL"]}',
        )
        agent_text_response = client.post(
            "/api/growth-agent/agent/run",
            content='{"prompt":"Find refi borrowers"}',
            headers={"Content-Type": "text/plain"},
        )
        custom_text_response = client.post(
            "/api/growth-agent/custom/run",
            content='{"segment_codes":["itm"],"segment_mode":"any"}',
            headers={"Content-Type": "text/plain"},
        )
    finally:
        _clear_overrides()

    responses = [
        workflow_text_response,
        workflow_form_response,
        workflow_missing_type_response,
        agent_text_response,
        custom_text_response,
    ]
    assert [response.status_code for response in responses] == [415, 415, 415, 415, 415]
    assert [response.json()["detail"] for response in responses] == ["Unsupported content type"] * 5
    assert not lakebase.runs


def test_growth_agent_openapi_documents_unsupported_content_type() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    for path in (
        "/api/growth-agent/workflows/{workflow_id}/run",
        "/api/growth-agent/custom/run",
        "/api/growth-agent/agent/run",
    ):
        assert paths[path]["post"]["responses"]["415"]["description"] == "Unsupported content type"
