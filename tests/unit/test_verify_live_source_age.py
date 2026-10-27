"""verify_live's data-age threshold fails closed (post-W5 round 2).

The weekly ``mip_scheduled_refresh`` lands only through a GO governed deploy,
so verify_live may widen the ``checked_at`` threshold from 3 days to the
cadence plus 1 (8) only when a read-only jobs list proves the job exists with
an UNPAUSED schedule on the bundle's weekly cron. Absent, PAUSED, another cron
and an unreadable list all keep 3. Every jobs list here is a fake: no test
reaches a workspace.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tools import verify_live
from tools import verify_live_source_readiness as readiness
from tools.verify_live import ProbeResult

_WEEKLY = {"quartz_cron_expression": "0 0 6 ? * FRI *", "timezone_id": "America/Chicago"}


def _job(name: str, schedule: dict[str, Any] | None, job_id: int = 101) -> dict[str, Any]:
    settings: dict[str, Any] = {"name": name}
    if schedule is not None:
        settings["schedule"] = schedule
    return {"job_id": job_id, "settings": settings}


def _jobs(*jobs: dict[str, Any]) -> Any:
    return lambda: list(jobs)


def _other_jobs() -> list[dict[str, Any]]:
    # The four children exist on dev today, all PAUSED or unscheduled.
    return [
        _job("[dev someone] mip_fred_rates_ingest", {**_WEEKLY, "pause_status": "PAUSED"}, 1),
        _job("[dev someone] mip_refresh_scores", None, 2),
        _job("[dev someone] mip_kpi_snapshot", {"pause_status": "PAUSED", "quartz_cron_expression": "0 30 5 ? * * *"}, 3),
    ]


def test_an_absent_scheduled_refresh_job_keeps_three_days() -> None:
    threshold = readiness.scheduled_refresh_threshold(_jobs(*_other_jobs()))

    assert threshold.days == 3
    assert threshold.basis == "fail-closed: no mip_scheduled_refresh job in the workspace"


def test_a_paused_scheduled_refresh_job_keeps_three_days() -> None:
    paused = _job("[dev someone] mip_scheduled_refresh", {**_WEEKLY, "pause_status": "PAUSED"})
    threshold = readiness.scheduled_refresh_threshold(_jobs(*_other_jobs(), paused))

    assert threshold.days == 3
    assert threshold.basis == "fail-closed: mip_scheduled_refresh is not UNPAUSED on the weekly cron (PAUSED)"


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["databricks"], stderr="Error: default auth: cannot configure"),
        subprocess.TimeoutExpired(["databricks"], 120),
        FileNotFoundError("databricks"),
        ValueError("Expecting value: line 1 column 1 (char 0)"),
    ],
    ids=["cli-error", "timeout", "no-cli", "not-json"],
)
def test_a_jobs_list_read_error_keeps_three_days(error: Exception) -> None:
    def unreadable() -> Any:
        raise error

    threshold = readiness.scheduled_refresh_threshold(unreadable)

    assert threshold.days == 3
    assert threshold.basis == f"fail-closed: the jobs list is unreadable ({type(error).__name__})"


def test_a_jobs_list_of_another_shape_keeps_three_days() -> None:
    assert readiness.scheduled_refresh_threshold(lambda: {"jobs": []}).days == 3


def test_an_unpaused_job_on_the_weekly_cron_widens_to_eight_days() -> None:
    live = _job("[dev someone] mip_scheduled_refresh", {**_WEEKLY, "pause_status": "UNPAUSED"}, 4242)
    threshold = readiness.scheduled_refresh_threshold(_jobs(*_other_jobs(), live))

    assert threshold.days == 8 == readiness.SCHEDULED_REFRESH_CADENCE_DAYS + 1
    # Only the job id is reported: the dev prefix carries a workspace handle.
    assert threshold.basis == "mip_scheduled_refresh job 4242 is UNPAUSED on the weekly cron (cadence + 1 day)"
    bare = _job("mip_scheduled_refresh", {**_WEEKLY, "pause_status": "UNPAUSED"})
    assert readiness.scheduled_refresh_threshold(_jobs(bare)).days == 8


@pytest.mark.parametrize(
    "job",
    [
        _job("[dev someone] mip_scheduled_refresh", {**_WEEKLY, "quartz_cron_expression": "0 0 6 1 * ? *", "pause_status": "UNPAUSED"}),
        _job("[dev someone] mip_scheduled_refresh", {**_WEEKLY, "timezone_id": "UTC", "pause_status": "UNPAUSED"}),
        _job("[dev someone] mip_scheduled_refresh", None),
        _job("[dev someone] mip_scheduled_refresh_copy", {**_WEEKLY, "pause_status": "UNPAUSED"}),
        _job("[dev someone] mip_fred_rates_ingest", {**_WEEKLY, "pause_status": "UNPAUSED"}),
    ],
    ids=["monthly-cron", "other-time-zone", "unscheduled", "suffixed-name", "a-child-job"],
)
def test_only_the_orchestrator_unpaused_on_the_weekly_cron_proves_the_schedule(job: dict[str, Any]) -> None:
    assert readiness.scheduled_refresh_threshold(_jobs(job)).days == 3


@pytest.mark.parametrize(
    "other_pause_status",
    ["UNPAUSED", "PAUSED"],
    ids=["two-live-orchestrators", "a-live-and-a-paused-orchestrator"],
)
def test_more_than_one_orchestrator_in_the_workspace_keeps_three_days(other_pause_status: str) -> None:
    # The jobs list is workspace-wide: another deployer's dev install (or a
    # prod install) proves nothing about the install under test.
    mine = _job("[dev someone] mip_scheduled_refresh", {**_WEEKLY, "pause_status": "UNPAUSED"}, 11)
    other = _job("mip_scheduled_refresh", {**_WEEKLY, "pause_status": other_pause_status}, 12)
    threshold = readiness.scheduled_refresh_threshold(_jobs(*_other_jobs(), mine, other))

    assert threshold.days == 3
    assert threshold.basis == (
        "fail-closed: 2 mip_scheduled_refresh jobs in the workspace (ids 11, 12); which one is this install's is unproven"
    )


def test_the_jobs_list_is_one_read_only_cli_call_with_the_operators_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    live = json.dumps([_job("mip_scheduled_refresh", {**_WEEKLY, "pause_status": "UNPAUSED"}, 7)])

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Record only: an assertion raised in here would be swallowed by the
        # resolver's fail-closed handler and read as a plain 3.
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=live, stderr="")

    monkeypatch.setattr(readiness.subprocess, "run", fake_run)

    # 8 proves the fake's answer was parsed, not swallowed as an error.
    assert readiness.resolve_max_source_age_days("ops").days == 8
    assert [argv for argv, _ in calls] == [["databricks", "jobs", "list", "-p", "ops", "-o", "json"]]
    ((_, kwargs),) = calls
    assert kwargs["check"] is True and kwargs["timeout"] > 0
    # Outside the repo root, where the bundle context could refuse another workspace's profile.
    assert kwargs["cwd"] == tempfile.gettempdir()


def _main_with_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, list_jobs: Any, *extra: str
) -> tuple[int, str, list[str]]:
    seen: dict[str, int] = {}
    profiles: list[str] = []

    def fake_collect(results: list[ProbeResult], *, max_source_age_days: int = 0) -> list[str]:
        seen["days"] = max_source_age_days
        return []

    def fake_cli_jobs_list(profile: str) -> Any:
        profiles.append(profile)
        return list_jobs()

    monkeypatch.setattr(verify_live, "get_oauth_token", lambda profile: "test-token")
    monkeypatch.setattr(verify_live, "run_probes", lambda base, token: [])
    monkeypatch.setattr(verify_live, "collect_red_flags", fake_collect)
    monkeypatch.setattr(readiness, "_cli_jobs_list", fake_cli_jobs_list)
    out = tmp_path / "report.md"
    monkeypatch.setattr("sys.argv", ["verify_live.py", "--out", str(out), "--profile", "ops", *extra])

    assert verify_live.main() == 0
    return seen["days"], out.read_text(encoding="utf-8"), profiles


def test_the_cli_default_is_three_days_without_a_live_schedule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    days, report, profiles = _main_with_jobs(monkeypatch, tmp_path, _jobs(*_other_jobs()))

    assert days == 3 and profiles == ["ops"]
    assert (
        "Source freshness: `checked_at` within 3 d (`--max-source-age-days`); "
        "fail-closed: no mip_scheduled_refresh job in the workspace"
    ) in report
    assert "[verify_live] source freshness threshold 3 d: fail-closed" in capsys.readouterr().err

    monkeypatch.setattr("sys.argv", ["verify_live.py", "--help"])
    with pytest.raises(SystemExit):
        verify_live.main()
    help_text = " ".join(capsys.readouterr().out.split())
    assert "(default 3, fail-closed; 8, the weekly cadence + 1 day, only when a read-only" in help_text
    assert "shows mip_scheduled_refresh UNPAUSED on its weekly cron)" in help_text


def test_the_cli_default_is_eight_days_when_the_schedule_is_proven_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    live = _job("[dev someone] mip_scheduled_refresh", {**_WEEKLY, "pause_status": "UNPAUSED"}, 77)
    days, report, _profiles = _main_with_jobs(monkeypatch, tmp_path, _jobs(*_other_jobs(), live))

    assert days == 8
    assert "within 8 d (`--max-source-age-days`); mip_scheduled_refresh job 77 is UNPAUSED" in report
    assert "someone" not in report


def test_the_cli_default_is_three_days_when_the_jobs_list_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unreadable() -> Any:
        raise subprocess.CalledProcessError(1, ["databricks"])

    days, report, _profiles = _main_with_jobs(monkeypatch, tmp_path, unreadable)

    assert days == 3
    assert "fail-closed: the jobs list is unreadable (CalledProcessError)" in report


def test_the_operator_docs_describe_the_fail_closed_default() -> None:
    root = Path(__file__).resolve().parents[2]
    testing = " ".join((root / "docs" / "testing.md").read_text(encoding="utf-8").split())
    runbook = " ".join((root / "docs" / "runbook.md").read_text(encoding="utf-8").split())

    assert "The default fails closed at 3 days. It widens to 8 days" in testing
    assert "An absent job, a `PAUSED` one, another cron or a jobs list that cannot be read keeps 3." in testing
    assert "shows exactly one `mip_scheduled_refresh` job" in testing
    assert "So does more than one such job: the list is workspace-wide" in testing
    assert "or that the workspace holds more than one `mip_scheduled_refresh`" in runbook
    assert "its default threshold fails closed at 3 days and widens to 8 (the 7-day cadence plus 1) only when" in runbook
    assert "falls back to its fail-closed 3-day threshold" in runbook
    for doc in (testing, runbook):
        assert "The default is 8 days" not in doc and "Its default threshold is 8 days" not in doc
