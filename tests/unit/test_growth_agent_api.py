from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.databricks_sql import get_sql_client
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
        if "INSERT INTO mip_app.action_audit" in sql:
            row = {
                "audit_id": uuid4(),
                "event_at": now,
                **params,
            }
            self.audit_events.append(row)
            return row
        if "INSERT INTO mip_app.growth_agent_runs" in sql:
            row = {
                "run_id": uuid4(),
                "created_at": now,
                **params,
            }
            self.runs.append(row)
            return row
        if "INSERT INTO mip_app.growth_agent_monitors" in sql:
            row = {
                "monitor_id": uuid4(),
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
        "listing_watch",
        "competitor_recapture_monitor",
        "high_equity_heloc_watch",
    ]
    assert all(workflow["default_route"].startswith("/lead-queue?") for workflow in body["workflows"])
    assert body["monitors"] == []


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
    assert "COUNT(DISTINCT b.clip)" in statement
    assert "COUNT(DISTINCT lp.clip)" in statement
    assert params == {"state_0": "IL", "state_1": "TX"}

    assert len(lakebase.audit_events) == 1
    audit_params = lakebase.audit_events[0]
    metadata = json.loads(audit_params["metadata"])
    assert audit_params["event_type"] == "GROWTH_AGENT_RUN"
    assert metadata["workflow_id"] == "daily_refi_brief"
    assert metadata["broad_total"] == 117404
    assert metadata["actionable_total"] == 5394
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
            "array_contains(lp.segment_codes, 'itm')",
        ],
        "listing_watch": [
            "b.listed_for_sale = TRUE",
            "array_contains(lp.segment_codes, 'listed')",
        ],
        "competitor_recapture_monitor": [
            "b.is_competitor_lien = TRUE",
            "lp.is_competitor_lien = TRUE",
        ],
        "high_equity_heloc_watch": [
            "b.has_heloc_propensity_trigger = TRUE",
            "b.equity_pct >= 35",
            "array_contains(lp.segment_codes, 'permit')",
            "array_contains(lp.segment_codes, 'equity')",
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
        for snippet in snippets:
            assert snippet in statement
        assert "lp.marketing_eligible = TRUE" in statement
        assert "lp.consent_status = 'opt_in'" in statement
        assert "lp.suppression_reason IS NULL" in statement
        assert "UPPER(b.state) IN (:state_0)" in statement
        assert "UPPER(lp.state) IN (:state_0)" in statement
        assert params == {"state_0": "IL"}


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


def test_growth_agent_post_requires_json_body_contract() -> None:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        text_response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            content='{"states":["IL"]}',
            headers={"Content-Type": "text/plain"},
        )
        form_response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            data={"states": "IL"},
        )
        missing_type_response = client.post(
            "/api/growth-agent/workflows/daily_refi_brief/run",
            content='{"states":["IL"]}',
        )
    finally:
        _clear_overrides()

    assert text_response.status_code == 415
    assert form_response.status_code == 415
    assert missing_type_response.status_code == 415
    assert text_response.json()["detail"] == "Unsupported content type"
    assert form_response.json()["detail"] == "Unsupported content type"
    assert missing_type_response.json()["detail"] == "Unsupported content type"
    assert not lakebase.runs
