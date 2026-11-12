"""A plan that predicts a window can complete: plan #3 prints the window digest, then completes with it (B6).

Run 1's plan #3 predicts a WR2 window. Before this fix it could never finish,
so it never printed the active deployment's digest or published the plan
report that step 12 needs:

* ``quiesce_window`` was release-only in ``operation_acks``, so a plan could
  not carry the window's ack, and the pre-lease window plan refused
  ``window_unacked`` (exit 2) on every plan dispatch;
* the window's admission was judged against the plan job's own 30-minute
  deadline, which admits no window (each needs about an hour), so even an
  acked plan refused ``window_budget_refused``.

Now the plan carries the ack (read-only: a plan holds no lease, and only a
pre-apply plan can open a window) and forecasts the admission for a release
dispatched now. The refusal that names the missing ack prints the exact
sha256 the plan needs. A release still refuses an unacked or unadmitted
window exactly as before.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_quiesce_window_budget import BLUE
from tests.unit.test_quiesce_window_shell import _run
from tests.unit.test_quiesce_window_workflow import _workflow, phase_timeouts
from tools.clients import dispatch_acks as acks
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_budget as budget
from tools.databricks.quiesce_window_plan import ACK_ENV, WindowFacts, window_plan

APP_NAME = "mip-app"
# Run 1's window: the jobs identity's Lakebase role no earlier run created.
WR2 = replace(BLUE, jobs_reader_role_state="drift")


def _plan_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    facts: WindowFacts,
    *,
    ack: str = "",
    position: str = "pre-lease",
    forecast: bool = False,
    deadline_in: int = 30 * 60,
) -> tuple[int, dict[str, Any]]:
    monkeypatch.setattr(window, "collect_facts", lambda *_a, **_k: facts)
    monkeypatch.setenv(ACK_ENV, ack)
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"verdict": "passed"}), encoding="utf-8")
    out = tmp_path / "plan.json"
    out.unlink(missing_ok=True)
    argv = ["plan", "--app-name", APP_NAME, "--position", position, "--plan-gate-report", str(gate)]
    argv += ["--deadline-epoch", str(int(time.time()) + deadline_in), "--out", str(out)]
    if forecast:
        argv += ["--admission-forecast", "release"]
    rc = window.main(argv, workspace_factory=lambda: None)
    return rc, json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}


def test_the_carrier_admits_the_window_ack_in_a_plan_and_a_release_only() -> None:
    digest = "9" * 64
    for phase in ("plan", "release"):
        assert acks.operation_exports(f"quiesce_window={digest}", phase) == {"MIP_ACK_QUIESCE_WINDOW": digest}


def test_the_plan_jobs_own_deadline_admits_no_window_even_acked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The premise: without the forecast, an acked plan inside its 30-minute job still refuses."""

    assert phase_timeouts(_workflow()["jobs"]["deploy"]["timeout-minutes"])["plan"] == 30
    sha = window_plan(WR2, position="pre-lease", acknowledgement="").sha256
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR2, ack=sha)
    assert rc == 2 and plan["refusals"] == ["window_budget_refused"] and plan["ack"] == "matched"
    assert plan["admission"]["state"] == "insufficient"
    assert plan["admission"]["required_seconds"] > 30 * 60


def test_the_unacked_plan_prints_the_exact_digest_and_the_acked_plan_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, first = _plan_cli(tmp_path, monkeypatch, WR2, forecast=True)
    out = capsys.readouterr()
    assert rc == 2 and first["refusals"] == ["window_unacked"], "the budget forecast admits it"
    sha = first["window_sha256"]
    assert f"window sha256 {sha}: acknowledge with {ACK_ENV}" in out.out
    assert "REFUSED window_unacked" in out.err
    # The digest it printed is the one its re-run needs, carried as the plan's own ack.
    exported = acks.operation_exports(f"quiesce_window={sha}", "plan")
    rc, second = _plan_cli(tmp_path, monkeypatch, WR2, ack=exported[ACK_ENV], forecast=True)
    out = capsys.readouterr()
    assert rc == 0 and second["code"] == "window" and second["refusals"] == []
    assert second["window_sha256"] == sha, "the forecast is never in the digest"
    assert second["admission"] == {
        "state": "admitted",
        "required_seconds": first["admission"]["required_seconds"],
        "remaining_seconds": second["admission"]["remaining_seconds"],
        "forecast": "release",
    }
    assert "admission (forecast for a release dispatched now) admitted" in out.out
    assert "this plan opens no window" in out.out


def test_a_forecast_never_admits_what_a_release_job_could_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = window_plan(WR2, position="pre-lease", acknowledgement="").sha256
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR2, ack=sha, forecast=True)
    assert rc == 0
    assert plan["admission"]["remaining_seconds"] <= budget.RELEASE_JOB_SECONDS
    release_minutes = phase_timeouts(_workflow()["jobs"]["deploy"]["timeout-minutes"])["release"]
    assert release_minutes * 60 == budget.RELEASE_JOB_SECONDS, "the release job's own budget"
    # A mismatched ack still refuses in the plan, forecast or not.
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR2, ack="0" * 64, forecast=True)
    assert rc == 2 and plan["refusals"] == ["window_unacked"] and plan["ack"] == "mismatch"


def test_the_forecast_is_a_pre_lease_plans_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = window_plan(WR2, position="pre-apply", acknowledgement="").sha256
    with pytest.raises(SystemExit, match="pre-lease only"):
        _plan_cli(tmp_path, monkeypatch, WR2, ack=sha, position="pre-apply", forecast=True)
    assert not (tmp_path / "plan.json").exists(), "refused before any read"


@pytest.mark.parametrize("position", ("pre-lease", "pre-apply"))
def test_a_release_plan_still_refuses_an_unacked_or_unadmitted_window_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR2, position=position, deadline_in=3 * 3600)
    assert rc == 2 and plan["refusals"] == ["window_unacked"] and "forecast" not in plan["admission"]
    sha = plan["window_sha256"]
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR2, ack=sha, position=position, deadline_in=600)
    assert rc == 2 and plan["refusals"] == ["window_budget_refused"]
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR2, ack=sha, position=position, deadline_in=3 * 3600)
    assert rc == 0 and plan["code"] == "window" and plan["admission"]["state"] == "admitted"


@pytest.mark.parametrize(("phase", "forecast"), (("plan", 1), ("release", 0), ("", 0)))
def test_the_shell_forecasts_in_the_plan_phase_only(tmp_path: Path, phase: str, forecast: int) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(tmp_path, f'DEPLOY_PHASE="{phase}"\nplan_quiesce_window pre-lease "$TMPDIR/report.json"\n')
    assert result.returncode == 0, result.stderr
    plan = next(call for call in calls if "quiesce_window plan" in call)
    assert plan.count("--admission-forecast release") == forecast, plan
    assert "--deadline-epoch" in plan, "the job's own deadline is still handed over"
