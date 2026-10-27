"""The read-only mip_scheduled_refresh postflight (deploy step 9c).

``tools/databricks/verify_scheduled_refresh_state.py`` reads the applied job
back through the Jobs API: a bundle validate cannot prove what was stored. It
must fail on a wrong pause status, a failure email on the wrong install, and a
live ``run_job_task`` closure that is not exactly FRED -> gold -> lifecycle ->
KPI or that reaches a reviewed-function publisher.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import verify_scheduled_refresh_state as postflight

IDS = {
    "mip_scheduled_refresh": "100",
    "mip_init_catalog_schemas": "101",
    "mip_fred_rates_ingest": "102",
    "mip_refresh_scores": "103",
    "mip_sync_lifecycle_state": "104",
    "mip_kpi_snapshot": "105",
}
SUMMARY = {"resources": {"jobs": {key: {"id": job_id} for key, job_id in IDS.items()}}}
DEPLOYER = "operator@example.invalid"


def _task(key: str, *, run_job: str | None = None, sql_path: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        task_key=key,
        run_job_task=None if run_job is None else SimpleNamespace(job_id=int(run_job)),
        sql_task=None if sql_path is None else SimpleNamespace(file=SimpleNamespace(path=sql_path)),
    )


def _jobs(*, pause: str = "UNPAUSED", recipients: list[str] | None = None) -> dict[str, Any]:
    orchestrator = SimpleNamespace(
        schedule=SimpleNamespace(pause_status=SimpleNamespace(value=pause)),
        email_notifications=SimpleNamespace(on_failure=[DEPLOYER] if recipients is None else recipients),
        tasks=[
            _task("refresh_market_rates", run_job=IDS["mip_fred_rates_ingest"]),
            _task("refresh_gold_scores", run_job=IDS["mip_refresh_scores"]),
            _task("sync_lifecycle_state", run_job=IDS["mip_sync_lifecycle_state"]),
            _task("snapshot_headline_kpis", run_job=IDS["mip_kpi_snapshot"]),
            _task("assert_refresh_outcome", sql_path="/x/sql/_rendered/transformations/assert_scheduled_refresh_outcome.sql"),
        ],
    )
    jobs: dict[str, Any] = {IDS["mip_scheduled_refresh"]: orchestrator}
    for key in ("mip_fred_rates_ingest", "mip_sync_lifecycle_state", "mip_kpi_snapshot"):
        jobs[IDS[key]] = SimpleNamespace(tasks=[_task("only")])
    jobs[IDS["mip_refresh_scores"]] = SimpleNamespace(
        tasks=[
            _task("init_fn_rate_spread", sql_path="/x/sql/_rendered/uc_functions/fn_rate_spread.sql"),
            _task("ctas_borrower_360", sql_path="/x/sql/_rendered/transformations/gold_borrower_360.sql"),
        ]
    )
    return jobs


def _get(jobs: dict[str, Any]) -> Any:
    return lambda job_id: SimpleNamespace(settings=jobs[str(job_id)])


def test_a_matching_live_install_passes_without_printing_the_recipient(capsys: pytest.CaptureFixture[str]) -> None:
    client = SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))

    code = postflight.main(["--expect", "UNPAUSED", "--live-install"], stdin=io.StringIO(json.dumps(SUMMARY)), client=client)

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert DEPLOYER not in captured.out + captured.err


def test_a_paused_ci_install_without_email_passes() -> None:
    assert postflight.verify(_get(_jobs(pause="PAUSED", recipients=[])), SUMMARY, expect_pause="PAUSED", live_install=False) == []


@pytest.mark.parametrize(
    ("jobs", "expect", "live", "needle"),
    [
        (_jobs(pause="PAUSED"), "UNPAUSED", True, "pause_status is PAUSED, expected UNPAUSED"),
        (_jobs(pause="UNPAUSED", recipients=[]), "UNPAUSED", True, "exactly one failure recipient"),
        (_jobs(pause="PAUSED"), "PAUSED", False, "no failure recipient outside the live install"),
    ],
)
def test_a_wrong_pause_or_email_fails(jobs: dict[str, Any], expect: str, live: bool, needle: str) -> None:
    problems = postflight.verify(_get(jobs), SUMMARY, expect_pause=expect, live_install=live)

    assert any(needle in problem for problem in problems), problems


def test_a_stale_gold_job_that_publishes_a_reviewed_function_fails() -> None:
    jobs = _jobs()
    jobs[IDS["mip_refresh_scores"]].tasks.append(
        _task("publish_fn_build_cohort", sql_path="/x/sql/_rendered/uc_functions/fn_build_cohort.sql")
    )

    problems = postflight.verify(_get(jobs), SUMMARY, expect_pause="UNPAUSED", live_install=True)

    assert problems == [f"job {IDS['mip_refresh_scores']} task publish_fn_build_cohort publishes a reviewed Growth Agent function"]


def test_a_closure_that_reaches_the_publisher_or_drops_a_child_fails() -> None:
    reaches = _jobs()
    reaches[IDS["mip_kpi_snapshot"]].tasks.append(_task("bootstrap", run_job=IDS["mip_init_catalog_schemas"]))
    reaches[IDS["mip_init_catalog_schemas"]] = SimpleNamespace(tasks=[_task("init_catalog_schemas")])
    problems = postflight.verify(_get(reaches), SUMMARY, expect_pause="UNPAUSED", live_install=True)
    assert "mip_scheduled_refresh reaches mip_init_catalog_schemas" in problems

    dropped = _jobs()
    dropped[IDS["mip_scheduled_refresh"]].tasks = dropped[IDS["mip_scheduled_refresh"]].tasks[:3]
    problems = postflight.verify(_get(dropped), SUMMARY, expect_pause="UNPAUSED", live_install=True)
    assert any("expected the bundle's chain" in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("stdin", "client"),
    [
        (json.dumps({"resources": {"jobs": {}}}), SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))),
        ("not json", SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))),
        (json.dumps(SUMMARY), SimpleNamespace(jobs=SimpleNamespace(get=lambda job_id: (_ for _ in ()).throw(PermissionError("denied"))))),
    ],
)
def test_any_read_failure_fails_closed(stdin: str, client: Any, capsys: pytest.CaptureFixture[str]) -> None:
    code = postflight.main(["--expect", "UNPAUSED", "--live-install"], stdin=io.StringIO(stdin), client=client)

    assert code == 2
    assert "cannot verify the deployed job" in capsys.readouterr().err


def test_deploy_runs_the_postflight_after_the_kpi_snapshot_and_before_the_genie_rebind() -> None:
    text = deploy_entrypoint_text()
    kpi = text.index("run_job_with_retry databricks bundle run mip_kpi_snapshot -t \"$TARGET\"")
    call = text.index("-m tools.databricks.verify_scheduled_refresh_state")
    rebind = text.index('step "rebind Genie space')
    block = text[kpi:rebind]

    assert kpi < call < rebind
    assert '_SCHEDULED_REFRESH_ARGS=(--expect "${BUNDLE_VAR_scheduled_refresh_pause_status:-}")' in block
    assert 'if [[ "$TARGET" == "dev" && -z "${BUNDLE_VAR_install_suffix:-}" ]]; then\n  _SCHEDULED_REFRESH_ARGS+=(--live-install)' in block
    assert 'run "$PYTHON" -m tools.databricks.verify_scheduled_refresh_state \\\n  "${_SCHEDULED_REFRESH_ARGS[@]}" <<< "$BUNDLE_SUMMARY_JSON"' in block, (
        "through run (skipped in a dry-run), with the post-apply summary as a here-string"
    )
