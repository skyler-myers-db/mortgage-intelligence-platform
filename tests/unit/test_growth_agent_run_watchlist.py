"""``POST /api/growth-agent/runs/{run_id}/monitors`` saves exactly the executed run (genie-09 part 1).

The old "Save reviewed watchlist" re-posted the objective to ``/agent/run``:
the model could pick a different workflow, and a run the lender never saw was
saved. This route re-plans nothing. It reads the caller's stored run, refuses
a run that did not complete, is unaudited or no longer matches the hash the
card showed, and saves the STORED workflow, criteria and route with one
``GROWTH_AGENT_MONITOR_SAVE`` audit row. The planner, the metrics loader, the
composer and the SQL warehouse are never reached.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

import backend.api.growth_agent as growth_agent_api
import backend.api.growth_agent_compose_routes as compose_routes
import backend.services.growth_agent_composer as composer_module
import backend.services.growth_agent_ledger_sql as ledger_sql
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)

ACTOR = "operator@example.com"
OTHER_ACTOR = "someone.else@example.com"
SAVE_EVENT = "GROWTH_AGENT_MONITOR_SAVE"


class _Harness:
    def __init__(self) -> None:
        self.lakebase = _FakeLakebaseClient()
        self.save_sql = _FakeSqlClient()
        self.run: dict[str, Any] = {}

    def start_run(self, body: dict[str, Any] | None = None, path: str = "/api/growth-agent/workflows/daily_refi_brief/run") -> dict[str, Any]:
        client = _client(_FakeSqlClient(), self.lakebase)
        try:
            response = client.post(path, json=body or {"states": ["IL"]}, headers={"X-Forwarded-Email": ACTOR})
        finally:
            _clear_overrides()
        assert response.status_code == 200, response.text
        self.run = dict(response.json())
        return self.run

    def save(self, body: dict[str, Any] | None = None, *, actor: str = ACTOR, run_id: str | None = None) -> Any:
        client = _client(self.save_sql, self.lakebase)
        payload = body if body is not None else {"tool_result_hash": self.run["tool_result_hash"], "cadence": "weekly"}
        try:
            return client.post(
                f"/api/growth-agent/runs/{run_id or self.run['run_id']}/monitors",
                json=payload,
                headers={"X-Forwarded-Email": actor},
            )
        finally:
            _clear_overrides()

    def save_events(self) -> list[dict[str, Any]]:
        return [row for row in self.lakebase.audit_events if row.get("event_type") == SAVE_EVENT]


def _forbid_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("Save as watchlist must never plan, read metrics or compose")

    monkeypatch.setattr(growth_agent_api, "plan_growth_agent_prompt", boom)
    monkeypatch.setattr(growth_agent_api, "load_growth_agent_metrics", boom)
    monkeypatch.setattr(compose_routes, "compose_growth_agent_plan", boom)
    monkeypatch.setattr(composer_module, "compose_growth_agent_plan", boom)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Harness]:
    instance = _Harness()
    instance.start_run()
    _forbid_planning(monkeypatch)
    yield instance


def test_save_builds_the_watchlist_from_the_stored_run(harness: _Harness) -> None:
    run = harness.run
    response = harness.save()

    assert response.status_code == 200, response.text
    monitor = response.json()
    assert monitor["workflow_id"] == run["workflow"]["id"] == "daily_refi_brief"
    assert monitor["criteria"] == run["criteria"]
    # The stored route (the response's route also carries a per-response,
    # actor-bound handoff proof that is never persisted).
    assert monitor["route"] == harness.lakebase.runs[0]["route"]
    assert run["route"].startswith(monitor["route"])
    assert monitor["last_run_id"] == run["run_id"]
    assert monitor["actionable_total"] == run["actionable_total"]
    assert monitor["cadence"] == "weekly"
    assert monitor["name"] == "Daily Refi Opportunity Brief - IL"
    assert harness.save_sql.calls == []
    [event] = harness.save_events()
    metadata = json.loads(event["metadata"])
    assert metadata["action"] == "growth_agent.monitor_save"
    assert metadata["run_id"] == run["run_id"]
    assert metadata["tool_result_hash"] == run["tool_result_hash"]
    assert event["entity_id"] == monitor["monitor_id"]


def test_a_replay_returns_the_same_watchlist_with_no_second_audit_row(harness: _Harness) -> None:
    first = harness.save()
    second = harness.save()
    assert first.status_code == second.status_code == 200
    assert first.json()["monitor_id"] == second.json()["monitor_id"]
    assert len(harness.save_events()) == 1
    assert len(harness.lakebase.monitors) == 1


def test_an_explicit_name_is_kept(harness: _Harness) -> None:
    response = harness.save({"tool_result_hash": harness.run["tool_result_hash"], "monitor_name": "IL Refi Watch"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "IL Refi Watch"
    assert response.json()["cadence"] == "daily"


def test_another_actors_run_is_not_found(harness: _Harness) -> None:
    response = harness.save(actor=OTHER_ACTOR)
    assert response.status_code == 404
    assert response.json()["detail"] == "growth-agent run not found"
    assert harness.save_events() == [] and harness.lakebase.monitors == []


@pytest.mark.parametrize("name", ["RUN_SELECT_FOR_SAVE_SQL", "MONITOR_SELECT_BY_KEY_SQL", "MONITOR_ID_BY_KEY_SQL"])
def test_every_save_and_series_lookup_reads_only_the_callers_rows(name: str) -> None:
    # The cross-actor 404 and the replay/series key live in the SQL's own
    # predicate; the fake Lakebase honours it only when the text carries it,
    # and tests/integration/test_growth_agent_runs_postgres.py runs it for real.
    assert re.search(r"WHERE\s+actor_email\s*=\s*%\(actor_email\)s", getattr(ledger_sql, name)), name


def test_another_actors_watchlist_with_the_same_key_is_never_replayed(harness: _Harness) -> None:
    # Same (workflow, name) key, same cadence, even pointing at this run: it is
    # someone else's watchlist, so the save writes the caller's own.
    foreign_id = uuid4()
    now = datetime.now(UTC)
    harness.lakebase.monitors.append(
        {
            "monitor_id": foreign_id,
            "actor_email": OTHER_ACTOR,
            "workflow_id": "daily_refi_brief",
            "name": "Daily Refi Opportunity Brief - IL",
            "cadence": "weekly",
            "status": "active",
            "criteria": {"states": ["TX"]},
            "route": "/lead-queue?states=TX",
            "actionable_total": 1,
            "source_assets": [],
            "last_run_id": harness.run["run_id"],
            "seed_run_id": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    response = harness.save()
    assert response.status_code == 200, response.text
    monitor = response.json()
    assert monitor["monitor_id"] != str(foreign_id)
    assert monitor["criteria"] == harness.run["criteria"]
    assert len(harness.save_events()) == 1
    assert [row["actor_email"] for row in harness.lakebase.monitors] == [OTHER_ACTOR, ACTOR]


def test_an_unknown_run_is_not_found(harness: _Harness) -> None:
    assert harness.save(run_id=str(uuid4())).status_code == 404


@pytest.mark.parametrize(
    "tamper",
    [
        lambda run: run.update({"tool_result_hash": "f" * 64}),
        lambda run: run.update({"status": "failed"}),
        lambda run: run.update({"audit_event_id": None}),
    ],
    ids=["hash-changed", "failed-run", "unaudited-run"],
)
def test_a_changed_failed_or_unaudited_run_is_a_409_with_no_write(harness: _Harness, tamper: Any) -> None:
    tamper(harness.lakebase.runs[0])
    audit_before = len(harness.lakebase.audit_events)
    response = harness.save()
    assert response.status_code == 409
    assert response.json()["detail"] == "This run changed or did not complete; run it again before saving."
    assert len(harness.lakebase.audit_events) == audit_before
    assert harness.lakebase.monitors == []


@pytest.mark.parametrize(
    "body",
    [
        {"tool_result_hash": "not-a-hash"},
        {"tool_result_hash": "A" * 64},
        {"tool_result_hash": "a" * 64, "cadence": "hourly"},
        {"tool_result_hash": "a" * 64, "criteria": {"states": ["TX"]}},
        {"tool_result_hash": "a" * 64, "workflow_id": "listing_watch"},
        {},
    ],
    ids=["bad-hash", "upper-hash", "bad-cadence", "extra-criteria", "extra-workflow", "empty"],
)
def test_the_body_cannot_steer_what_is_saved(harness: _Harness, body: dict[str, Any]) -> None:
    response = harness.save(body)
    assert response.status_code == 422
    assert harness.lakebase.monitors == [] and harness.save_events() == []


def test_a_malformed_run_id_is_a_422(harness: _Harness) -> None:
    assert harness.save(run_id="not-a-uuid").status_code == 422


def test_a_custom_segment_run_is_named_by_its_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness()
    harness.start_run(
        {"states": ["IL"], "segment_codes": ["itm", "listed"], "segment_mode": "any"},
        path="/api/growth-agent/custom/run",
    )
    _forbid_planning(monkeypatch)
    response = harness.save()
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Custom Segment Workflow - ANY - ITM+LISTED - IL"
    assert response.json()["workflow_id"] == "custom_segment_watch"
