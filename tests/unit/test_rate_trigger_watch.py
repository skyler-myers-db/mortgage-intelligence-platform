"""The reviewed rate watch and "Watch this level" (audit 2026-09-21 ``wow-ai-2``, W5d).

``POST /api/growth-agent/rate-watch`` saves a ``rate_trigger_watch`` watchlist
from one Rate Lever step: the client sends the step only, the server reads the
watched par level from today's grid and names the watchlist. Each run (Run
now, run-due, run-due-all) rebuilds the workflow from the stored criteria,
compares the latest weekly 30-year print with the level and counts the Rate
Lever scenario from gold; a review note is drafted only when the level is
reached. These tests drive the real routes, the real audit write path and the
real metadata policy over the shared fakes, with the two gold rate
repositories faked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

import backend.api.growth_agent_watchlist_routes as watchlist_routes
import backend.services.growth_agent_drafts as drafts_module
import backend.services.growth_agent_rate_trigger as rate_trigger
from backend.main import app
from backend.schemas.analytics_rate_window import (
    RateWindowProvenance,
    RateWindowResponse,
    RateWindowThresholds,
    RateWindowWeek,
)
from backend.schemas.geo_rate_sensitivity import (
    RateSensitivityProvenance,
    RateSensitivityResponse,
    RateSensitivityState,
    RateSensitivityThresholds,
)
from backend.schemas.growth_agent import validate_growth_agent_monitor_name
from backend.schemas.growth_agent_watchlist import GrowthAgentSchedulerStatus
from backend.services.audit_metadata_policy import AuditMetadataValueViolation
from backend.services.audit_metadata_public_values import _assert_public_safe_values
from backend.services.databricks_sql_helpers import qualify
from backend.services.growth_agent_ledger_sql import DUE_MONITOR_LIST_ALL_SQL, DUE_MONITOR_LIST_SQL
from backend.services.growth_agent_rate_watch import rate_watch_name
from backend.services.growth_agent_runtime import (
    criteria_for,
    default_copilot_evidence,
    policy_checks,
    tool_steps,
)
from backend.services.growth_agent_workflows import (
    WORKFLOWS,
    RateTrigger,
    build_growth_agent_route,
    rate_trigger_workflow,
)
from backend.services.repositories import (
    get_rate_sensitivity_repository,
    get_rate_window_repository,
)
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)

ACTOR = "lo@summit.example"
HEADERS = {"X-Forwarded-Email": ACTOR}
STEPS = [-100, -75, -50, -25, 0, 25, 50, 75, 100]
PARS = [5.30, 5.55, 5.80, 6.05, 6.30, 6.55, 6.80, 7.05, 7.30]
SAVE_EVENT = "GROWTH_AGENT_MONITOR_SAVE"
RUN_EVENT = "GROWTH_AGENT_RUN"
DRAFT_EVENT = "GROWTH_AGENT_NOTIFICATION_DRAFT"
PAUSED = GrowthAgentSchedulerStatus(state="paused", reason="job_schedule")


def _grid(*, built: bool = True, steps: list[int] | None = None) -> RateSensitivityResponse:
    steps = STEPS if steps is None else steps
    states = [
        RateSensitivityState(
            state="IL", addressable=900, rate_movable=800,
            in_the_money=[400, 350, 300, 250, 200, 150, 100, 50, 25][: len(steps)],
            contactable_in_the_money=[40, 35, 30, 25, 20, 15, 10, 5, 2][: len(steps)],
        ),
        RateSensitivityState(
            state="TX", addressable=500, rate_movable=400,
            in_the_money=[200, 175, 150, 125, 100, 75, 50, 25, 10][: len(steps)],
            contactable_in_the_money=None,
        ),
    ]
    return RateSensitivityResponse(
        built=built,
        steps_bps=steps if built else [],
        scenario_market_rate_pct=PARS[: len(steps)] if built else [],
        base_market_rate_pct=6.30 if built else None,
        thresholds=RateSensitivityThresholds(min_spread_bps=50, min_equity_pct=20),
        states=states if built else [],
        provenance=RateSensitivityProvenance(
            gold_source="mip.gold.rate_sensitivity_rollup", book_source="book", rule_source="rules",
            contactable_source="live", note="A scenario, not a forecast.",
        ),
    )


def _window(print_pct: float | None) -> RateWindowResponse:
    weeks = (
        []
        if print_pct is None
        else [
            RateWindowWeek(week="2026-09-21", market_rate_pct=6.40, itm_count=1),
            RateWindowWeek(week="2026-09-28", market_rate_pct=print_pct, itm_count=1, is_latest=True),
        ]
    )
    return RateWindowResponse(
        series_id="MORTGAGE30US", weeks=weeks, book_lien_count=1, thresholds=RateWindowThresholds(),
        provenance=RateWindowProvenance(
            market_rate_source="fred", book_source="book", gold_source="gold", rule_source="rules", note="n"
        ),
    )


class _Rates:
    """Both gold rate repositories, faked."""

    def __init__(self) -> None:
        self.grid = _grid()
        self.window = _window(5.75)

    def rate_sensitivity(self) -> RateSensitivityResponse:
        return self.grid

    def rate_window(self) -> RateWindowResponse:
        return self.window


@pytest.fixture
def rates(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Rates]:
    repo = _Rates()
    monkeypatch.setattr(rate_trigger, "get_rate_window_repository", lambda: repo)
    monkeypatch.setattr(rate_trigger, "get_rate_sensitivity_repository", lambda: repo)
    monkeypatch.setattr(watchlist_routes, "growth_agent_scheduler_status", lambda: PAUSED)
    app.dependency_overrides[get_rate_sensitivity_repository] = lambda: repo
    app.dependency_overrides[get_rate_window_repository] = lambda: repo
    try:
        yield repo
    finally:
        app.dependency_overrides.pop(get_rate_sensitivity_repository, None)
        app.dependency_overrides.pop(get_rate_window_repository, None)


def _post(lakebase: Any, path: str, body: dict[str, Any], sql: _FakeSqlClient | None = None) -> Any:
    client = _client(sql or _FakeSqlClient(), lakebase)
    try:
        return client.post(path, json=body, headers=HEADERS)
    finally:
        _clear_overrides()


def _save(lakebase: Any, step: int = -50, **extra: Any) -> Any:
    return _post(lakebase, "/api/growth-agent/rate-watch", {"step_bps": step, "cadence": "daily", **extra})


def _events(lakebase: Any, event_type: str) -> list[dict[str, Any]]:
    return [row for row in lakebase.audit_events if row.get("event_type") == event_type]


TRIGGER = RateTrigger(step_bps=-50, level_pct=5.80, direction="at_or_below", base_market_rate_pct=6.30)


def test_the_builder_is_a_reviewed_scenario_workflow_and_never_a_card() -> None:
    workflow = rate_trigger_workflow(TRIGGER)
    assert (workflow.id, workflow.title, workflow.route_path) == ("rate_trigger_watch", "Rate Trigger Watch", "/")
    assert build_growth_agent_route(workflow.route_filters, path=workflow.route_path) == "/?map_mode=rate&rate_step=-50"
    assert workflow.source_assets == (qualify("gold", "rate_window_weekly"), qualify("gold", "rate_sensitivity_rollup"))
    assert (workflow.broad_predicate, workflow.actionable_predicate) == ("FALSE", "FALSE")
    assert workflow.specialist_agent == "structured_data_agent"
    assert "rate_trigger_watch" not in WORKFLOWS


def test_the_workflow_run_route_stays_a_404(rates: _Rates) -> None:
    response = _post(_FakeLakebaseClient(), "/api/growth-agent/workflows/rate_trigger_watch/run", {})
    assert response.status_code == 404


def test_the_generated_name_passes_the_unchanged_monitor_name_validator() -> None:
    for trigger in (TRIGGER, RateTrigger(50, 6.80, "at_or_above", 6.30)):
        name = rate_watch_name(trigger)
        assert "%" not in name and validate_growth_agent_monitor_name(name) == name


@pytest.mark.parametrize(
    ("print_pct", "level", "direction", "expected"),
    [
        (5.80, 5.80, "at_or_below", True),
        (5.79, 5.80, "at_or_below", True),
        (5.81, 5.80, "at_or_below", False),
        (5.8040, 5.80, "at_or_below", True),
        (6.55, 6.55, "at_or_above", True),
        (6.56, 6.55, "at_or_above", True),
        (6.54, 6.55, "at_or_above", False),
        (None, 5.80, "at_or_below", None),
    ],
)
def test_the_print_is_compared_with_the_level_at_two_decimals(
    print_pct: float | None, level: float, direction: Any, expected: bool | None
) -> None:
    assert rate_trigger.crossed(print_pct, level, direction) is expected


def test_save_writes_one_audited_watch_with_the_servers_level(rates: _Rates) -> None:
    lakebase = _FakeLakebaseClient()
    response = _save(lakebase, request_id="5a1e0000-0000-4000-8000-0000000000aa")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["watch_level_pct"], body["watch_direction"], body["replayed"]) == (5.80, "at_or_below", False)
    assert (body["latest_print_pct"], body["latest_print_week"]) == (5.75, "2026-09-28")
    assert body["scheduler"] == {"state": "paused", "reason": "job_schedule"}
    monitor = body["monitor"]
    assert monitor["workflow_id"] == "rate_trigger_watch" and monitor["last_run_id"] is None
    assert monitor["name"] == "Rate watch - 30-year par at or below 5.80 percent"
    assert monitor["route"] == "/?map_mode=rate&rate_step=-50"
    assert monitor["criteria"]["rate_trigger"] == {
        "series_id": "MORTGAGE30US", "step_bps": -50, "level_pct": 5.80,
        "direction": "at_or_below", "base_market_rate_pct": 6.30,
    }
    [event] = _events(lakebase, SAVE_EVENT)
    metadata = json.loads(event["metadata"])
    assert {key: metadata[key] for key in ("workflow_id", "rate_step_bps", "watch_level_pct", "watch_direction")} == {
        "workflow_id": "rate_trigger_watch", "rate_step_bps": -50, "watch_level_pct": 5.80,
        "watch_direction": "at_or_below",
    }
    assert metadata["route"] == "/?map_mode=rate&rate_step=-50"
    assert not lakebase.runs, "saving a watch starts no run"


def test_an_identical_save_replays_without_a_second_row(rates: _Rates) -> None:
    lakebase = _FakeLakebaseClient()
    assert _save(lakebase).status_code == 200
    again = _save(lakebase)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert len(_events(lakebase, SAVE_EVENT)) == 1 and len(lakebase.monitors) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"step_bps": 0},
        {"step_bps": 125},
        {"step_bps": -125},
        {"step_bps": "-50"},
        {"step_bps": -50, "level_pct": 5.8},
        {"step_bps": -50, "rate": 5.8},
        {"step_bps": -50, "name": "My watch"},
        {"step_bps": -50, "request_id": "jane@summit.example"},
    ],
    ids=["zero", "above", "below", "string", "level-in-body", "rate-in-body", "name-in-body", "pii-request-id"],
)
def test_the_body_carries_a_step_only(rates: _Rates, body: dict[str, Any]) -> None:
    lakebase = _FakeLakebaseClient()
    response = _post(lakebase, "/api/growth-agent/rate-watch", body)
    assert response.status_code == 422
    assert not lakebase.audit_events and not lakebase.monitors


@pytest.mark.parametrize(
    ("grid", "step", "detail"),
    [
        (_grid(built=False), -50, "Rate scenarios are not built yet."),
        (_grid(), -60, "The Rate Lever grid changed; move the slider again."),
        (_grid(steps=[-100, -75, -25, 0]), -50, "The Rate Lever grid changed; move the slider again."),
    ],
    ids=["not-built", "off-grid", "grid-changed"],
)
def test_a_grid_that_cannot_answer_is_a_409_and_writes_nothing(
    rates: _Rates, grid: RateSensitivityResponse, step: int, detail: str
) -> None:
    rates.grid = grid
    lakebase = _FakeLakebaseClient()
    response = _save(lakebase, step=step)
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert not lakebase.audit_events and not lakebase.monitors


def _run_now(lakebase: _FakeLakebaseClient, sql: _FakeSqlClient | None = None) -> Any:
    monitor_id = str(lakebase.monitors[0]["monitor_id"])
    response = _post(lakebase, f"/api/growth-agent/monitors/{monitor_id}/run", {}, sql=sql)
    assert response.status_code == 200, response.text
    return response.json()


def test_two_consecutive_runs_keep_the_trigger_byte_identical(rates: _Rates) -> None:
    lakebase = _FakeLakebaseClient()
    saved = _save(lakebase).json()["monitor"]["criteria"]
    canonical = json.dumps(saved, sort_keys=True)
    for _ in range(2):
        run = _run_now(lakebase)
        assert json.dumps(lakebase.monitors[0]["criteria"], sort_keys=True) == canonical
        assert run["workflow"]["id"] == "rate_trigger_watch"
    assert [json.dumps(json.loads(row["criteria"]), sort_keys=True) for row in lakebase.runs] == [canonical] * 2
    assert len(_events(lakebase, RUN_EVENT)) == 2, "both runs went through the real audit write"


def test_a_run_reads_no_borrower_sql_and_counts_the_saved_step(rates: _Rates) -> None:
    lakebase = _FakeLakebaseClient()
    sql = _FakeSqlClient()
    _save(lakebase)
    run = _run_now(lakebase, sql=sql)
    assert sql.calls == [], "the rate branch never uses the run's SQL client (borrower_360)"
    assert (run["broad_total"], run["actionable_total"]) == (300 + 150, 30)
    [check] = [check for check in run["policy_checks"] if check["label"] == "Watched rate level"]
    assert check["status"] == "review_required"


def _copy(metrics: dict[str, Any]) -> list[str]:
    workflow = rate_trigger_workflow(TRIGGER)
    steps = tool_steps(workflow, metrics, tool_result_hash="a" * 64, copilot_evidence=default_copilot_evidence(workflow))
    checks = policy_checks(workflow, metrics, saved_monitor=True)
    return [text for step in steps for text in (step.label, step.detail)] + [
        text for check in checks for text in (check.label, check.detail)
    ]


@pytest.mark.parametrize(
    ("grid", "print_pct", "status"),
    [
        (_grid(), 5.75, "review_required"),
        (_grid(), 5.95, "passed"),
        (_grid(), None, "blocked"),
        (_grid(built=False), 5.75, "review_required"),
        (_grid(steps=[-100, -75, -25, 0]), 5.95, "passed"),
    ],
    ids=["reached", "not-reached", "no-print", "not-built", "off-grid"],
)
def test_every_outcome_is_a_comparison_never_a_forecast(
    rates: _Rates, grid: RateSensitivityResponse, print_pct: float | None, status: str
) -> None:
    rates.grid, rates.window = grid, _window(print_pct)
    metrics = rate_trigger.load_rate_trigger_metrics(TRIGGER)
    if not grid.built or -50 not in grid.steps_bps:
        assert (metrics["broad_total"], metrics["actionable_total"]) == (0, 0)
    [check] = [c for c in policy_checks(rate_trigger_workflow(TRIGGER), metrics, saved_monitor=False) if c.label == "Watched rate level"]
    assert check.status == status
    for text in _copy(metrics):
        negation_stripped = text.replace("not a forecast", "")
        assert not re.search(r"\b(will|forecast\w*|expect\w*|predict\w*)\b", negation_stripped, re.I), text
        assert not re.search(r"\d{4}-\d{2}-\d{2}", text), f"no dates in step or check copy: {text}"


def test_the_scenario_step_states_the_counted_scenario(rates: _Rates) -> None:
    metrics = rate_trigger.load_rate_trigger_metrics(TRIGGER)
    assert "Counts are the -50 bps scenario at today's 6.30% par, not a forecast" in " ".join(_copy(metrics))


def _draft(lakebase: _FakeLakebaseClient) -> Any:
    monitor_id = str(lakebase.monitors[0]["monitor_id"])
    return _post(
        lakebase, f"/api/growth-agent/monitors/{monitor_id}/notification-drafts", {"channels": ["slack", "teams"]}
    )


def test_a_reached_watch_drafts_review_notes(rates: _Rates) -> None:
    lakebase = _FakeLakebaseClient()
    _save(lakebase)
    _run_now(lakebase)
    response = _draft(lakebase)
    assert response.status_code == 200, response.text
    assert [draft["channel"] for draft in response.json()] == ["slack", "teams"]
    assert len(_events(lakebase, DRAFT_EVENT)) == 2


def test_a_quiet_watch_drafts_nothing_and_writes_no_row(rates: _Rates, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(drafts_module, "emit", lambda _log, event, **fields: events.append((event, fields)))
    rates.window = _window(5.95)
    lakebase = _FakeLakebaseClient()
    _save(lakebase)
    _run_now(lakebase)
    audit_before = len(lakebase.audit_events)
    response = _draft(lakebase)
    assert (response.status_code, response.json()) == (200, [])
    assert len(lakebase.audit_events) == audit_before and not lakebase.notification_drafts
    assert events == [("growth_agent_rate_trigger_quiet", {})]


@pytest.mark.parametrize("statement", [DUE_MONITOR_LIST_SQL, DUE_MONITOR_LIST_ALL_SQL], ids=["actor", "all"])
def test_a_quiet_watch_is_due_by_cadence_only(statement: str) -> None:
    missing_drafts = statement[statement.index("OR EXISTS") :]
    assert "AND m.workflow_id <> 'rate_trigger_watch'" in missing_drafts
    assert "AND m.workflow_id <> 'rate_trigger_watch'" not in statement[: statement.index("OR EXISTS")]


@pytest.mark.parametrize(
    ("metadata", "accepted"),
    [
        ({"rate_step_bps": -50, "watch_level_pct": 5.8, "watch_direction": "at_or_below"}, True),
        ({"rate_step_bps": 100, "watch_level_pct": 7, "watch_direction": "at_or_above"}, True),
        ({"rate_step_bps": 0}, False),
        ({"rate_step_bps": 30}, False),
        ({"rate_step_bps": 125}, False),
        ({"rate_step_bps": True}, False),
        ({"rate_step_bps": "-50"}, False),
        ({"watch_level_pct": 5.805}, False),
        ({"watch_level_pct": 0}, False),
        ({"watch_level_pct": 25}, False),
        ({"watch_level_pct": True}, False),
        ({"watch_direction": "below"}, False),
    ],
)
def test_the_rate_watch_audit_values_are_closed(metadata: dict[str, Any], accepted: bool) -> None:
    if accepted:
        _assert_public_safe_values(metadata)
    else:
        with pytest.raises(AuditMetadataValueViolation):
            _assert_public_safe_values(metadata)


def test_criteria_for_rewrites_only_the_trigger() -> None:
    criteria = criteria_for(rate_trigger_workflow(TRIGGER), ["IL"])
    assert criteria == {
        "states": [],
        "rate_trigger": TRIGGER.criteria(),
        "marketing_eligibility": "Eligible only",
        "workflow_id": "rate_trigger_watch",
    }


@pytest.mark.parametrize(
    "trigger",
    [
        {"step_bps": 0},
        {"step_bps": 30},
        {"step_bps": -50, "direction": "at_or_above"},
        {"level_pct": 5.805},
        {"level_pct": 25},
        {"series_id": "DGS10"},
        {"step_bps": True},
    ],
)
def test_a_malformed_stored_trigger_is_the_409(rates: _Rates, trigger: dict[str, Any]) -> None:
    lakebase = _FakeLakebaseClient()
    _save(lakebase)
    stored = lakebase.monitors[0]["criteria"]["rate_trigger"]
    stored.update(trigger)
    monitor_id = str(lakebase.monitors[0]["monitor_id"])
    response = _post(lakebase, f"/api/growth-agent/monitors/{monitor_id}/run", {})
    assert (response.status_code, response.json()["detail"]) == (409, "saved monitor has malformed criteria")
