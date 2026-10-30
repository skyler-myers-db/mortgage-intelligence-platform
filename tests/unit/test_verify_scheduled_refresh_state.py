"""The read-only mip_scheduled_refresh postflight (deploy step 9c).

``tools/databricks/verify_scheduled_refresh_state.py`` reads the applied job
back through the Jobs API: a bundle validate cannot prove what was stored. It
must fail on a wrong pause status, a failure email that is not exactly the
client profile's recipients (nobody while its refresh is off; M1-PR3), an
orchestrator task graph that is not
FRED -> gold -> lifecycle -> KPI plus the outcome gate after gold, and a live
``run_job_task`` closure that is not exactly those four jobs or that reaches a
reviewed-function publisher.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import verify_scheduled_refresh_state as postflight

ROOT = Path(__file__).resolve().parents[2]
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
PROFILE_FLAG = "--recipients-from-profile"


@pytest.fixture(autouse=True)
def _profile_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--recipients-from-profile`` reads the client profile; these tests pin its recipients."""

    monkeypatch.setattr(postflight, "_profile_recipients", lambda environ, env_file: (DEPLOYER,))


GATE_PATH = "/Workspace/Users/x/.bundle/p/dev/files/sql/_rendered/transformations/assert_scheduled_refresh_outcome.sql"


def _task(
    key: str,
    *,
    run_job: str | None = None,
    sql_path: str | None = None,
    depends_on: tuple[str, ...] = (),
    run_if: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        task_key=key,
        run_job_task=None if run_job is None else SimpleNamespace(job_id=int(run_job)),
        sql_task=None if sql_path is None else SimpleNamespace(file=SimpleNamespace(path=sql_path)),
        depends_on=[SimpleNamespace(task_key=dep) for dep in depends_on] or None,
        run_if=None if run_if is None else SimpleNamespace(value=run_if),
    )


def _orchestrator_tasks() -> list[SimpleNamespace]:
    """The live shape of databricks.yml's mip_scheduled_refresh tasks (the Jobs API reports ALL_SUCCESS)."""

    return [
        _task("refresh_market_rates", run_job=IDS["mip_fred_rates_ingest"], run_if="ALL_SUCCESS"),
        _task("refresh_gold_scores", run_job=IDS["mip_refresh_scores"], depends_on=("refresh_market_rates",)),
        _task("sync_lifecycle_state", run_job=IDS["mip_sync_lifecycle_state"], depends_on=("refresh_gold_scores",)),
        _task("snapshot_headline_kpis", run_job=IDS["mip_kpi_snapshot"], depends_on=("sync_lifecycle_state",)),
        _task("assert_refresh_outcome", sql_path=GATE_PATH, depends_on=("refresh_gold_scores",)),
    ]


def _jobs(*, pause: str = "UNPAUSED", recipients: list[str] | None = None) -> dict[str, Any]:
    orchestrator = SimpleNamespace(
        schedule=SimpleNamespace(pause_status=SimpleNamespace(value=pause)),
        email_notifications=SimpleNamespace(on_failure=[DEPLOYER] if recipients is None else recipients),
        tasks=_orchestrator_tasks(),
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


def test_a_matching_refresh_passes_without_printing_the_recipient(capsys: pytest.CaptureFixture[str]) -> None:
    client = SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))

    code = postflight.main(["--expect", "UNPAUSED", PROFILE_FLAG], stdin=io.StringIO(json.dumps(SUMMARY)), client=client)

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert DEPLOYER not in captured.out + captured.err


def test_a_paused_ci_install_without_email_passes() -> None:
    assert postflight.verify(_get(_jobs(pause="PAUSED", recipients=[])), SUMMARY, expect_pause="PAUSED", expected_recipients=()) == []


@pytest.mark.parametrize(
    ("jobs", "expect", "recipients", "needle"),
    [
        (_jobs(pause="PAUSED"), "UNPAUSED", (DEPLOYER,), "pause_status is PAUSED, expected UNPAUSED"),
        (_jobs(pause="UNPAUSED", recipients=[]), "UNPAUSED", (DEPLOYER,), "exactly the client profile's 1 failure"),
        (_jobs(pause="PAUSED"), "PAUSED", (), "no failure recipient while the refresh is off"),
        (
            _jobs(recipients=["someone-else@example.invalid"]),
            "UNPAUSED",
            (DEPLOYER,),
            "has 1 that differ",
        ),
    ],
)
def test_a_wrong_pause_or_email_fails(
    jobs: dict[str, Any], expect: str, recipients: tuple[str, ...], needle: str
) -> None:
    problems = postflight.verify(_get(jobs), SUMMARY, expect_pause=expect, expected_recipients=recipients)

    assert any(needle in problem for problem in problems), problems
    assert not any(DEPLOYER in problem or "someone-else" in problem for problem in problems)


def test_a_stale_gold_job_that_publishes_a_reviewed_function_fails() -> None:
    jobs = _jobs()
    jobs[IDS["mip_refresh_scores"]].tasks.append(
        _task("publish_fn_build_cohort", sql_path="/x/sql/_rendered/uc_functions/fn_build_cohort.sql")
    )

    problems = postflight.verify(_get(jobs), SUMMARY, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))

    assert problems == [f"job {IDS['mip_refresh_scores']} task publish_fn_build_cohort publishes a reviewed Growth Agent function"]


def test_a_closure_that_reaches_the_publisher_or_drops_a_child_fails() -> None:
    reaches = _jobs()
    reaches[IDS["mip_kpi_snapshot"]].tasks.append(_task("bootstrap", run_job=IDS["mip_init_catalog_schemas"]))
    reaches[IDS["mip_init_catalog_schemas"]] = SimpleNamespace(tasks=[_task("init_catalog_schemas")])
    problems = postflight.verify(_get(reaches), SUMMARY, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))
    assert "mip_scheduled_refresh reaches mip_init_catalog_schemas" in problems

    dropped = _jobs()
    dropped[IDS["mip_scheduled_refresh"]].tasks = dropped[IDS["mip_scheduled_refresh"]].tasks[:3]
    problems = postflight.verify(_get(dropped), SUMMARY, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))
    assert any("expected the bundle's chain" in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("task_key", "sql_path"),
    [
        ("init_cohort_builder", "/x/sql/_rendered/uc_functions/fn_build_cohort.sql"),
        ("publish_fn_segment_counts", "/x/sql/_rendered/transformations/gold_segment_population.sql"),
        ("publish_fn_lead_queue_url", None),
    ],
    ids=["path-only", "key-only", "key-only-no-sql"],
)
def test_either_half_of_the_publisher_check_fails_on_its_own(task_key: str, sql_path: str | None) -> None:
    jobs = _jobs()
    jobs[IDS["mip_kpi_snapshot"]].tasks.append(_task(task_key, sql_path=sql_path))

    problems = postflight.verify(_get(jobs), SUMMARY, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))

    assert problems == [f"job {IDS['mip_kpi_snapshot']} task {task_key} publishes a reviewed Growth Agent function"]


def test_a_recipient_beyond_the_profiles_fails() -> None:
    jobs = _jobs(recipients=[DEPLOYER, "second@example.invalid"])

    problems = postflight.verify(_get(jobs), SUMMARY, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))

    assert problems == [
        "mip_scheduled_refresh must email exactly the client profile's 1 failure recipient(s) "
        "(MIP_SCHEDULED_REFRESH_RECIPIENTS), has 2 that differ"
    ]


def test_recipients_compare_as_a_set_without_regard_to_case() -> None:
    jobs = _jobs(recipients=["B@Example.Invalid", "a@example.invalid"])

    assert postflight.verify(
        _get(jobs), SUMMARY, expect_pause="UNPAUSED", expected_recipients=("a@example.invalid", "b@example.invalid")
    ) == []


def test_an_application_id_recipient_never_matches_the_profiles_and_is_never_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The profile refuses an application id among its recipients, so a deployer SP id always differs."""

    application_id = "00000000-0000-0000-0000-000000000000"
    jobs = _jobs(recipients=[application_id])

    problems = postflight.verify(_get(jobs), SUMMARY, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))
    assert len(problems) == 1 and "has 1 that differ" in problems[0], problems

    client = SimpleNamespace(jobs=SimpleNamespace(get=_get(jobs)))
    code = postflight.main(["--expect", "UNPAUSED", PROFILE_FLAG], stdin=io.StringIO(json.dumps(SUMMARY)), client=client)
    captured = capsys.readouterr()
    assert code == 2
    assert application_id not in captured.out + captured.err, "never print a recipient"


@pytest.mark.parametrize(("refresh", "expected"), [("on", ("refresh-alerts@summit.example",)), ("off", ())])
def test_the_profile_decides_the_expected_recipients(
    refresh: str, expected: tuple[str, ...], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.fixtures.client_profiles import demo_profile, env_file_text

    monkeypatch.undo()
    env_file = tmp_path / ".env.local"
    env_file.write_text(env_file_text(demo_profile(MIP_SCHEDULED_REFRESH=refresh)), encoding="utf-8")

    assert postflight._profile_recipients({}, env_file) == expected


def test_a_profile_that_cannot_be_resolved_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.undo()
    client = SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))
    env_file = tmp_path / ".env.local"
    env_file.write_text("MIP_CLIENT='fictional-demo'\n", encoding="utf-8")

    code = postflight.main(
        ["--expect", "UNPAUSED", PROFILE_FLAG],
        stdin=io.StringIO(json.dumps(SUMMARY)),
        client=client,
        environ={},
        env_file=env_file,
    )

    assert code == 2
    assert "cannot verify the deployed job" in capsys.readouterr().err


def test_a_closure_that_reaches_an_extra_non_publisher_job_fails() -> None:
    silver_id = "106"
    summary = {"resources": {"jobs": {**SUMMARY["resources"]["jobs"], "mip_refresh_silver": {"id": silver_id}}}}
    jobs = _jobs()
    jobs[IDS["mip_refresh_scores"]].tasks.append(_task("refresh_silver_first", run_job=silver_id))
    jobs[silver_id] = SimpleNamespace(tasks=[_task("silver_pipeline")])

    problems = postflight.verify(_get(jobs), summary, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))

    expected = sorted([IDS["mip_scheduled_refresh"], *(IDS[key] for key in postflight.CHAIN_JOB_KEYS)])
    reached = sorted([*expected, silver_id])
    assert problems == [f"mip_scheduled_refresh reaches jobs {reached}, expected the bundle's chain {expected}"]


def _with_task(change: Any) -> dict[str, Any]:
    jobs = _jobs()
    jobs[IDS["mip_scheduled_refresh"]].tasks = change(_orchestrator_tasks())
    return jobs


def _replace(key: str, **fields: Any) -> Any:
    def change(tasks: list[SimpleNamespace]) -> list[SimpleNamespace]:
        return [_task(key, **fields) if task.task_key == key else task for task in tasks]

    return change


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        (
            _replace("assert_refresh_outcome", sql_path=GATE_PATH, depends_on=("snapshot_headline_kpis",)),
            "task assert_refresh_outcome depends on ['snapshot_headline_kpis'], expected ['refresh_gold_scores']",
        ),
        (
            _replace("refresh_gold_scores", run_job=IDS["mip_refresh_scores"]),
            "task refresh_gold_scores depends on [], expected ['refresh_market_rates']",
        ),
        (
            _replace(
                "snapshot_headline_kpis",
                run_job=IDS["mip_kpi_snapshot"],
                depends_on=("sync_lifecycle_state", "refresh_market_rates"),
            ),
            "task snapshot_headline_kpis depends on ['refresh_market_rates', 'sync_lifecycle_state']",
        ),
        (
            _replace("sync_lifecycle_state", run_job=IDS["mip_sync_lifecycle_state"], depends_on=("refresh_gold_scores",), run_if="ALL_DONE"),
            "task sync_lifecycle_state runs if ALL_DONE, expected ALL_SUCCESS",
        ),
        (
            _replace("refresh_market_rates", run_job=IDS["mip_refresh_scores"]),
            "task refresh_market_rates does not run mip_fred_rates_ingest",
        ),
        (
            _replace("assert_refresh_outcome", sql_path="/x/sql/_rendered/transformations/gold_borrower_360.sql", depends_on=("refresh_gold_scores",)),
            "task assert_refresh_outcome is not the SELECT-only outcome gate",
        ),
        (
            _replace("assert_refresh_outcome", run_job=IDS["mip_kpi_snapshot"], depends_on=("refresh_gold_scores",)),
            "task assert_refresh_outcome is not the SELECT-only outcome gate",
        ),
        (lambda tasks: tasks[:4], "has tasks ['refresh_gold_scores', 'refresh_market_rates', 'snapshot_headline_kpis', 'sync_lifecycle_state']"),
        (lambda tasks: [*tasks, _task("notify", depends_on=("assert_refresh_outcome",))], "has tasks ["),
        (lambda tasks: [*tasks, tasks[-1]], "has tasks ["),
    ],
    ids=[
        "gate-after-kpi",
        "gold-without-fred",
        "kpi-extra-dependency",
        "run-if-all-done",
        "fred-task-runs-gold",
        "gate-wrong-file",
        "gate-as-run-job",
        "gate-missing",
        "extra-task",
        "duplicate-task-key",
    ],
)
def test_an_orchestrator_task_graph_that_is_not_the_bundle_chain_fails(change: Any, needle: str) -> None:
    problems = postflight.verify(_get(_with_task(change)), SUMMARY, expect_pause="UNPAUSED", expected_recipients=(DEPLOYER,))

    assert any(needle in problem for problem in problems), problems


def test_the_expected_task_graph_is_the_one_databricks_yml_declares() -> None:
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    declared = {}
    for task in bundle["resources"]["jobs"]["mip_scheduled_refresh"]["tasks"]:
        job_ref = (task.get("run_job_task") or {}).get("job_id")
        job = None if job_ref is None else job_ref.removeprefix("${resources.jobs.").removesuffix(".id}")
        declared[task["task_key"]] = (job, tuple(dep["task_key"] for dep in task.get("depends_on", [])))
        if job is None:
            assert task["sql_task"]["file"]["path"] == postflight.GATE_FILE

    assert declared == postflight.ORCHESTRATOR_TASKS
    assert postflight.CHAIN_JOB_KEYS == (
        "mip_fred_rates_ingest",
        "mip_refresh_scores",
        "mip_sync_lifecycle_state",
        "mip_kpi_snapshot",
    )


def test_the_success_line_names_what_was_checked(capsys: pytest.CaptureFixture[str]) -> None:
    client = SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))

    assert postflight.main(["--expect", "UNPAUSED", PROFILE_FLAG], stdin=io.StringIO(json.dumps(SUMMARY)), client=client) == 0

    out = capsys.readouterr().out
    assert "tasks FRED -> gold -> lifecycle -> KPI plus the outcome gate after gold" in out
    assert "closure = {FRED, gold, lifecycle, KPI} verified" in out


def test_the_cli_builds_the_deployer_client_never_a_bare_workspace_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.databricks import workspace_auth

    built: list[str] = []

    def fake_deployment_client() -> Any:
        built.append("deployer")
        return SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))

    monkeypatch.setattr(workspace_auth, "deployment_workspace_client", fake_deployment_client)

    code = postflight.main(["--expect", "UNPAUSED", PROFILE_FLAG], stdin=io.StringIO(json.dumps(SUMMARY)))

    assert code == 0 and built == ["deployer"]


@pytest.mark.parametrize(
    ("stdin", "client"),
    [
        (json.dumps({"resources": {"jobs": {}}}), SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))),
        ("not json", SimpleNamespace(jobs=SimpleNamespace(get=_get(_jobs())))),
        (json.dumps(SUMMARY), SimpleNamespace(jobs=SimpleNamespace(get=lambda job_id: (_ for _ in ()).throw(PermissionError("denied"))))),
    ],
)
def test_any_read_failure_fails_closed(stdin: str, client: Any, capsys: pytest.CaptureFixture[str]) -> None:
    code = postflight.main(["--expect", "UNPAUSED", PROFILE_FLAG], stdin=io.StringIO(stdin), client=client)

    assert code == 2
    assert "cannot verify the deployed job" in capsys.readouterr().err


def test_deploy_runs_the_postflight_after_the_kpi_snapshot_and_before_the_genie_rebind() -> None:
    text = deploy_entrypoint_text()
    kpi = text.index("run_job_with_retry databricks bundle run mip_kpi_snapshot -t \"$TARGET\"")
    call = text.index("-m tools.databricks.verify_scheduled_refresh_state")
    rebind = text.index('step "rebind Genie space')
    block = text[kpi:rebind]

    assert kpi < call < rebind
    assert "--live-install" not in block and "CLIENT_INSTALL_CLASS" not in block
    assert (
        'run "$PYTHON" -m tools.databricks.verify_scheduled_refresh_state \\\n'
        '  --expect "${BUNDLE_VAR_scheduled_refresh_pause_status:-}" \\\n'
        '  --recipients-from-profile <<< "$BUNDLE_SUMMARY_JSON"'
    ) in block, "through run (skipped in a dry-run), with the post-apply summary as a here-string"
