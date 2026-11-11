"""Each governed phase predicts at most 330 minutes and is admitted against the job deadline (M1-PR6).

The prediction is a reviewed sum; the admission is the quiesce window's own
rule (``quiesce_window_budget.admission``) under the phase schema, so a phase
body can never pass as a window budget and a window budget never as a phase.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.databricks import phase_budget as budget
from tools.databricks import quiesce_window_budget
from tools.databricks.quiesce_window_plan import REASONS, budget_body

NOW = 1_800_000_000
ALL_REASONS = tuple(REASONS)


def _window_plan(tmp_path: Path, reasons: tuple[str, ...] = ALL_REASONS, *, tamper: bool = False) -> Path:
    window_budget = budget_body(reasons, blue_restorable=True)
    if tamper:
        window_budget = {**window_budget, "predicted_window_seconds": 1}
    plan = {"code": "window", "reasons": list(reasons), "canonical": {"reasons": list(reasons), "blue_restorable": True, "budget": window_budget}}
    path = tmp_path / "window-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


@pytest.mark.parametrize("agentic", (True, False))
@pytest.mark.parametrize("window_code", ("not_applicable", "none", "unknown"))
@pytest.mark.parametrize("skip_silver", (True, False))
def test_every_phase_predicts_at_most_330_minutes(agentic: bool, window_code: str, skip_silver: bool) -> None:
    for phase in budget.PHASES:
        body = budget.predict(phase, agentic=agentic, window_code=window_code, skip_silver=skip_silver)
        assert body["predicted_seconds"] <= 330 * 60, (phase, body["components"])
        assert body["required_seconds"] == body["predicted_seconds"] + 15 * 60
        assert body["predicted_seconds"] == sum(body["components"].values())


@pytest.mark.parametrize("agentic", (True, False))
def test_a_release_with_the_largest_window_still_fits(agentic: bool) -> None:
    window = budget_body(ALL_REASONS, blue_restorable=True)
    body = budget.predict("release", agentic=agentic, window_code="window", window_budget=window)
    assert body["components"]["window"] == window["predicted_window_seconds"] + window["predicted_restore_seconds"]
    assert "role_bootstrap" not in body["components"], "a window needs signed blue: never a first install"
    assert body["predicted_seconds"] <= 330 * 60


def test_a_first_install_release_bootstraps_one_role_per_tier_role() -> None:
    agentic = budget.predict("release", agentic=True, window_code="not_applicable")
    core = budget.predict("release", agentic=False, window_code="not_applicable")
    assert agentic["components"]["role_bootstrap"] == 2 * core["components"]["role_bootstrap"]
    assert "agent_evaluation" not in core["components"]
    assert budget.predict("release", agentic=True, window_code="none")["components"].get("role_bootstrap") is None


@pytest.mark.parametrize("agentic", (True, False))
def test_a_release_over_signed_blue_budgets_step_0as_start_and_re_pin(agentic: bool) -> None:
    """Review of #334: a stopped blue is started and re-pinned in step 0a (tier a, spike S7)."""

    from tools.databricks.quiesce_window_plan import STEP_BUDGET_S as WINDOW

    window = budget_body(("WR3",), blue_restorable=True)
    for code, given in (("none", None), ("window", window)):
        body = budget.predict("release", agentic=agentic, window_code=code, window_budget=given)
        assert body["components"]["step0a_start"] == WINDOW["stop"] + WINDOW["start"] + WINDOW["repin"], code
    for code in ("not_applicable", "unknown"):
        body = budget.predict("release", agentic=agentic, window_code=code)
        assert "step0a_start" not in body["components"], "no signed blue, no step-0a start"


def test_the_data_phase_reconciles_after_each_job_and_smokes_once() -> None:
    body = budget.predict("data", skip_silver=False)
    assert list(body["components"]) == [
        "mip_refresh_silver",
        "mip_refresh_silver:grant_reconcile",
        "mip_scheduled_refresh",
        "mip_scheduled_refresh:grant_reconcile",
        "data_smoke",
    ]
    assert "mip_refresh_silver" not in budget.predict("data", skip_silver=True)["components"]


@pytest.mark.parametrize(
    ("deadline", "state"),
    (
        ("", "deadline_missing"),
        ("soon", "deadline_unparseable"),
        (str(NOW - 1), "deadline_past"),
        (str(NOW + 7 * 3600), "deadline_too_far"),
        (str(NOW + 60 * 60), "insufficient"),
    ),
)
def test_a_phase_the_deadline_cannot_hold_refuses(deadline: str, state: str) -> None:
    body = budget.predict("data")
    assert budget.decide(body, deadline, now=NOW).state == state
    with pytest.raises(budget.PhaseBudgetRefused, match=state):
        budget.require_admitted(body, deadline, now=NOW)


def test_a_phase_is_admitted_when_the_deadline_covers_its_prediction_and_margin() -> None:
    body = budget.predict("release", agentic=True, window_code="not_applicable")
    exact = str(NOW + body["required_seconds"])
    assert budget.require_admitted(body, exact, now=NOW).state == "admitted"
    assert budget.decide(body, str(NOW + body["required_seconds"] - 1), now=NOW).state == "insufficient"


def test_a_prediction_over_the_phase_limit_refuses_before_any_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(budget.STEP_BUDGET_S, "mip_scheduled_refresh", 6 * 3600)
    body = budget.predict("data")
    with pytest.raises(budget.PhaseBudgetRefused, match="over the 330-minute phase limit"):
        budget.require_admitted(body, str(NOW + 5 * 3600), now=NOW)
    assert "it would refuse" in budget.describe(body, "", now=NOW)


def test_a_phase_body_never_passes_as_a_window_budget() -> None:
    phase = budget.predict("data")
    canonical = {"reasons": ["WR3"], "blue_restorable": True, "budget": phase}
    with pytest.raises(RuntimeError, match="not the reviewed budget of its reasons"):
        quiesce_window_budget.require_admitted(canonical, str(NOW + 5 * 3600), now=NOW)
    # The window's own admission accepts both reviewed schemas and nothing else.
    with pytest.raises(ValueError, match="not a reviewed budget body"):
        quiesce_window_budget.admission({**phase, "schema": "mip.other"}, str(NOW + 5 * 3600), now=NOW)


def test_the_cli_admits_or_refuses_and_reads_the_window_plans_budget(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget.time, "time", lambda: float(NOW))
    plan = _window_plan(tmp_path)
    out = tmp_path / "budget.json"
    argv = ["admit", "--phase", "release", "--window-plan", str(plan), "--deadline-epoch", str(NOW + 330 * 60), "--out", str(out)]
    assert budget.main(argv) == 0
    assert "window" in json.loads(out.read_text(encoding="utf-8"))["components"]
    assert "phase budget: release predicts" in capsys.readouterr().out
    assert budget.main(["admit", "--phase", "data", "--deadline-epoch", ""]) == 2
    assert "deadline_missing" in capsys.readouterr().err
    # describe never refuses, deadline or not.
    assert budget.main(["describe", "--phase", "data"]) == 0
    tampered = _window_plan(tmp_path, tamper=True)
    assert budget.main(["admit", "--phase", "release", "--window-plan", str(tampered), "--deadline-epoch", str(NOW + 330 * 60)]) == 2
    assert "not its reasons' reviewed budget" in capsys.readouterr().err
