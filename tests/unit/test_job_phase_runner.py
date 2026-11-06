"""The data phase's job runner: re-attach, skip, rerun and stale-definition refusal (M1-PR6).

The fakes are built from the SDK's own dataclasses (tests/fixtures/
data_phase_estate.py): ``Job.from_dict``, ``Run.from_dict`` and
``GetPipelineResponse.from_dict``, and a run-now that honours the idempotency
token as the Jobs API does. The ledger is the real signed ledger in fake
workspace files; only the lease's held check is stubbed (it has its own tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tests.fixtures.data_phase_estate import (
    APP_NAME,
    CLIENT,
    DEPLOYER,
    JOB_IDS,
    PIPELINE_ID,
    DataPhaseEstate,
    state_document,
)
from tests.unit.test_app_deployment_rollback import SIGNING_KEY
from tools.databricks import job_phase_definition as definitions
from tools.databricks import job_phase_ledger as ledger
from tools.databricks import job_phase_runner as runner

SHA = "c" * 40
LEASE = "lease-PLACEHOLDER"
NOW = 1_800_000_000
DEADLINE = NOW + 5 * 3600


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", derive_gateway_proof_verify_key(SIGNING_KEY))
    monkeypatch.setattr(runner, "_held", lambda ctx: None)


def _ctx(estate: DataPhaseEstate, clock: list[float] | None = None) -> runner.Context:
    ticks = clock if clock is not None else [float(NOW)]

    def sleep(seconds: float) -> None:
        ticks[0] += seconds

    return runner.Context(estate, APP_NAME, LEASE, SHA, sleep=sleep, now=lambda: ticks[0])


def _plan(estate: DataPhaseEstate, jobs: tuple[str, ...] = ("mip_refresh_silver", "mip_scheduled_refresh")) -> dict[str, Any]:
    return definitions.build_plan(
        estate, deployer=DEPLOYER, client_key=CLIENT, app_name=APP_NAME, jobs=jobs, skip_silver=len(jobs) == 1
    )


def _planned(plan: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in plan["jobs"] if item["key"] == key)


def _run(estate: DataPhaseEstate, plan: dict[str, Any], key: str, **kwargs: Any) -> tuple[int, str]:
    return runner.run_job(_ctx(estate, **kwargs), plan, _planned(plan, key), deadline=DEADLINE)


def _entry(estate: DataPhaseEstate, key: str) -> dict[str, Any]:
    record = ledger.read(estate, app_name=APP_NAME)
    assert record is not None
    return record["jobs"][key]


# --- the definition ------------------------------------------------------------


def test_the_plan_digests_each_jobs_bound_closure_and_classifies_gold() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)

    silver, refresh = plan["jobs"]
    assert (silver["key"], silver["job_id"]) == ("mip_refresh_silver", str(JOB_IDS["mip_refresh_silver"]))
    assert silver["pipelines"] == ["mip_feature_pipeline"] and silver["touches_gold"]
    assert refresh["closure"] == sorted(
        ["mip_scheduled_refresh", "mip_fred_rates_ingest", "mip_refresh_scores", "mip_sync_lifecycle_state", "mip_kpi_snapshot"]
    )
    assert refresh["touches_gold"], "the gold refresh and the UC functions are in its closure"
    assert plan["budget"]["predicted_seconds"] <= 330 * 60
    # The digest is the live closure's: an edit to a child changes the parent's.
    estate.jobs.settings["mip_refresh_scores"]["max_concurrent_runs"] = 2
    assert _planned(_plan(estate), "mip_scheduled_refresh")["definition_sha256"] != refresh["definition_sha256"]
    assert _planned(_plan(estate), "mip_refresh_silver")["definition_sha256"] == silver["definition_sha256"]


@pytest.mark.parametrize(
    ("task", "touches"),
    (
        ({"sql_task": {"file": {"path": "/W/files/sql/_rendered/ref/state_footprint_seed.sql"}}}, False),
        ({"sql_task": {"file": {"path": "/W/files/sql/_rendered/ddl/silver_market_rates_weekly.sql"}}}, False),
        ({"sql_task": {"file": {"path": "/W/files/sql/_rendered/ddl/003_gold_tables.sql"}}}, True),
        ({"sql_task": {"file": {"path": "/W/files/sql/_rendered/uc_functions/fn_lead_score.sql"}}}, True),
        ({"sql_task": {"query": {"query_id": "q"}}}, True),
        ({"spark_python_task": {"python_file": "/W/files/jobs/fred_rates_ingest.py"}}, False),
        ({"spark_python_task": {"python_file": "/W/files/jobs/converge_governed_owner.py"}}, True),
        ({"pipeline_task": {"pipeline_id": "p"}}, True),
        ({"run_job_task": {"job_id": 1}}, True),
        ({"notebook_task": {"notebook_path": "/x"}}, True),
    ),
)
def test_a_task_is_gold_free_only_when_provably_so(task: dict[str, Any], touches: bool) -> None:
    assert definitions.task_touches_gold(task) is touches


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda e: e.jobs.settings["mip_refresh_silver"].pop("deployment"), "not deployed by this deployer's bundle"),
        (
            lambda e: e.jobs.settings["mip_kpi_snapshot"]["deployment"].update(metadata_file_path="/Workspace/other/state/metadata.json"),
            "not deployed by this deployer's bundle",
        ),
        (lambda e: e.jobs.settings["mip_refresh_silver"].update(name="renamed"), "named otherwise"),
        (lambda e: e.pipelines.spec.pop("deployment"), "not deployed by this deployer's bundle"),
    ),
)
def test_a_job_the_deployers_bundle_did_not_deploy_refuses(mutate: Any, message: str) -> None:
    estate = DataPhaseEstate()
    mutate(estate)
    with pytest.raises(definitions.Refused, match=message):
        _plan(estate)


def test_an_unbound_job_or_child_refuses() -> None:
    unbound = dict(JOB_IDS)
    unbound.pop("mip_refresh_scores")
    with pytest.raises(definitions.Refused, match="starts a job this deployer's bundle state does not bind"):
        _plan(DataPhaseEstate(document=state_document(jobs=unbound)))
    with pytest.raises(definitions.Refused, match="starts a pipeline"):
        _plan(DataPhaseEstate(document=state_document(pipelines={})))
    with pytest.raises(definitions.Refused, match="binds no jobs.mip_refresh_silver; run the release first"):
        _plan(DataPhaseEstate(document=state_document(jobs={})))


# --- run, skip, re-attach, rerun ------------------------------------------------


def _begun(estate: DataPhaseEstate, plan: dict[str, Any]) -> dict[str, Any]:
    return runner.begin(_ctx(estate), plan, ack="")


def test_a_first_run_journals_its_token_before_the_run_now_and_a_rerun_skips_it() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    generation = _begun(estate, plan)["generation"]

    assert _run(estate, plan, "mip_refresh_silver") == (0, "succeeded (attempt 1)")
    entry = _entry(estate, "mip_refresh_silver")
    expected = runner.token(
        client_key=CLIENT,
        job_key="mip_refresh_silver",
        definition_sha256=_planned(plan, "mip_refresh_silver")["definition_sha256"],
        generation=generation,
        attempt=1,
    )
    assert estate.jobs.run_now_calls == [(JOB_IDS["mip_refresh_silver"], expected)]
    assert len(expected) <= 64 and entry["token"] == expected and entry["status"] == "succeeded"
    # A rerun of the phase skips the succeeded run: no second run-now.
    _begun(estate, plan)
    assert _run(estate, plan, "mip_refresh_silver")[0] == 0
    assert len(estate.jobs.run_now_calls) == 1


def test_a_killed_phase_re_attaches_to_its_running_run() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    _begun(estate, plan)
    estate.jobs.next_script = [("RUNNING", "")]
    clock = [float(DEADLINE - 11 * 60)]
    # The runner reaches its deadline with the run still going: it stops waiting, never cancels.
    assert _run(estate, plan, "mip_scheduled_refresh", clock=clock)[0] == runner.EXIT_STILL_RUNNING
    entry = _entry(estate, "mip_scheduled_refresh")
    assert entry["status"] == "running"
    run_id = int(entry["run_id"])
    estate.jobs.scripts[run_id] = [("RUNNING", ""), ("TERMINATING", ""), ("TERMINATED", "SUCCESS")]
    # The next data phase re-attaches to that run id: no run-now at all.
    _begun(estate, plan)
    assert _run(estate, plan, "mip_scheduled_refresh") == (0, "succeeded (re-attached)")
    assert len(estate.jobs.run_now_calls) == 1
    assert _entry(estate, "mip_scheduled_refresh")["status"] == "succeeded"


def test_a_token_journaled_without_its_run_id_is_resent_and_starts_nothing_new() -> None:
    # The kill lands between the run-now and the ledger write of its run id.
    estate = DataPhaseEstate()
    plan = _plan(estate)
    record = _begun(estate, plan)
    planned = _planned(plan, "mip_refresh_silver")
    tok = runner.token(
        client_key=CLIENT, job_key=planned["key"], definition_sha256=planned["definition_sha256"],
        generation=record["generation"], attempt=1,
    )
    estate.jobs.run_now(job_id=JOB_IDS["mip_refresh_silver"], idempotency_token=tok)  # the run the dead phase started
    entry = {
        "job_id": planned["job_id"], "definition_sha256": planned["definition_sha256"], "attempt": 1,
        "token": tok, "run_id": "", "status": "submitted", "result": "",
    }
    runner._record(_ctx(estate), record, planned["key"], entry)

    assert _run(estate, plan, "mip_refresh_silver") == (0, "succeeded (attempt 1)")
    assert {token for _job, token in estate.jobs.run_now_calls} == {tok}
    assert len(estate.jobs.tokens) == 1, "the resend returned the run the token had started"


@pytest.mark.parametrize(
    "terminal", (("TERMINATED", "FAILED"), ("TERMINATED", "SUCCESS_WITH_FAILURES"), ("SKIPPED", ""), ("INTERNAL_ERROR", ""))
)
def test_only_a_terminal_failure_is_rerun_as_the_next_attempt(terminal: tuple[str, str]) -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    _begun(estate, plan)
    estate.jobs.next_script = [terminal]
    rc, label = _run(estate, plan, "mip_refresh_silver")
    assert rc == runner.EXIT_FAILED and "failed (attempt 1" in label
    first = _entry(estate, "mip_refresh_silver")
    assert first["status"] == "failed"

    estate.jobs.next_script = [("TERMINATED", "SUCCESS")]
    _begun(estate, plan)
    assert _run(estate, plan, "mip_refresh_silver") == (0, "succeeded (attempt 2)")
    second = _entry(estate, "mip_refresh_silver")
    assert second["attempt"] == 2 and second["token"] != first["token"]
    assert len(estate.jobs.run_now_calls) == 2


def test_a_run_that_ended_while_no_runner_watched_is_recorded_not_rerun() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    _begun(estate, plan)
    estate.jobs.next_script = [("RUNNING", "")]
    _run(estate, plan, "mip_refresh_silver", clock=[float(DEADLINE - 11 * 60)])
    run_id = int(_entry(estate, "mip_refresh_silver")["run_id"])
    estate.jobs.scripts[run_id] = [("TERMINATED", "SUCCESS")]
    assert _run(estate, plan, "mip_refresh_silver")[0] == 0
    assert len(estate.jobs.run_now_calls) == 1


# --- stale definitions --------------------------------------------------------


def test_a_stale_definition_refuses_until_its_exact_reset_ack() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    old = _begun(estate, plan)
    estate.jobs.next_script = [("TERMINATED", "FAILED")]
    _run(estate, plan, "mip_refresh_silver")
    # A release redeployed the job between the failed attempt and this data phase.
    estate.jobs.settings["mip_refresh_silver"]["max_concurrent_runs"] = 3
    replanned = _plan(estate)
    with pytest.raises(runner.Refused, match="stale_definition") as refused:
        runner.begin(_ctx(estate), replanned, ack="")
    digest = str(refused.value).split(f"{runner.ACK_SUPERSEDE_ENV}=")[1].split()[0]
    with pytest.raises(runner.Refused, match="stale_definition"):
        runner.begin(_ctx(estate), replanned, ack="0" * 64)
    # The run refuses too, without a run-now, while the stale generation is open.
    with pytest.raises(runner.Refused, match="stale_definition"):
        _run(estate, replanned, "mip_refresh_silver")
    assert len(estate.jobs.run_now_calls) == 1

    fresh = runner.begin(_ctx(estate), replanned, ack=digest)
    assert fresh["generation"] != old["generation"] and fresh["jobs"] == {}
    estate.jobs.next_script = [("TERMINATED", "SUCCESS")]
    assert _run(estate, replanned, "mip_refresh_silver") == (0, "succeeded (attempt 1)")


def test_a_stale_run_still_active_is_never_superseded() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    _begun(estate, plan)
    estate.jobs.next_script = [("RUNNING", "")]
    _run(estate, plan, "mip_refresh_silver", clock=[float(DEADLINE - 11 * 60)])
    estate.jobs.settings["mip_refresh_silver"]["max_concurrent_runs"] = 3
    replanned = _plan(estate)
    with pytest.raises(runner.Refused) as refused:
        runner.begin(_ctx(estate), replanned, ack="")
    digest = str(refused.value).split(f"{runner.ACK_SUPERSEDE_ENV}=")[1].split()[0]
    with pytest.raises(runner.Refused, match="still active"):
        runner.begin(_ctx(estate), replanned, ack=digest)
    assert ledger.read(estate, app_name=APP_NAME)["state"] == "open"


def test_the_runs_definition_must_still_be_the_plans(tmp_path: Path) -> None:
    estate = DataPhaseEstate()
    env_file = _env_file(tmp_path)
    plan_file = tmp_path / "data-plan.json"
    plan_file.write_text(json.dumps(_plan(estate)), encoding="utf-8")
    assert runner.main(["begin", "--plan", str(plan_file), *COMMON], client=estate, environ={}, env_file=env_file) == 0
    # A redeploy between the plan and the run: the run refuses before any run-now.
    estate.jobs.settings["mip_kpi_snapshot"]["max_concurrent_runs"] = 2
    argv = [
        "run", "--plan", str(plan_file), *COMMON, "--job-key", "mip_scheduled_refresh",
        "--deadline-epoch", str(DEADLINE), "--out-env", str(tmp_path / "out.env"),
    ]
    assert runner.main(argv, client=estate, environ={}, env_file=env_file) == runner.EXIT_REFUSED
    assert estate.jobs.run_now_calls == []


# --- close and the next generation --------------------------------------------


def test_close_needs_every_planned_job_succeeded_then_the_next_phase_runs_again() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    _begun(estate, plan)
    _run(estate, plan, "mip_refresh_silver")
    with pytest.raises(runner.Refused, match="mip_scheduled_refresh has not succeeded"):
        runner.close(_ctx(estate), plan)
    _run(estate, plan, "mip_scheduled_refresh")
    assert runner.close(_ctx(estate), plan)["state"] == "closed"
    fresh = _begun(estate, plan)
    assert fresh["state"] == "open" and fresh["jobs"] == {}
    _run(estate, plan, "mip_refresh_silver")
    assert len(estate.jobs.run_now_calls) == 3, "a completed phase is no reason to skip the next one"


def test_a_tampered_ledger_refuses() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    _begun(estate, plan)
    _run(estate, plan, "mip_refresh_silver")
    value = json.loads(estate.ledger_bytes() or b"{}")
    value["jobs"]["mip_refresh_silver"]["status"] = "failed"
    estate.put_ledger(value)
    with pytest.raises(RuntimeError, match="signature is invalid"):
        _run(estate, plan, "mip_refresh_silver")


def test_a_succeeded_entry_is_final_within_its_generation() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    _begun(estate, plan)
    _run(estate, plan, "mip_refresh_silver")
    current = ledger.read(estate, app_name=APP_NAME)
    entry = {**current["jobs"]["mip_refresh_silver"], "status": "failed"}
    with pytest.raises(RuntimeError, match="rewrites the succeeded entry"):
        runner._record(_ctx(estate), current, "mip_refresh_silver", entry)


# --- the CLI --------------------------------------------------------------------


COMMON = ["--lease-id", LEASE, "--source-git-sha", SHA]


def _env_file(tmp_path: Path) -> Path:
    env_file = tmp_path / ".env.local"
    env_file.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    return env_file


def test_the_cli_plans_runs_and_closes_under_the_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    estate = DataPhaseEstate()
    env_file = _env_file(tmp_path)
    plan_file = tmp_path / "data-plan.json"
    monkeypatch.setattr(runner.time, "time", lambda: float(NOW))
    common = COMMON

    assert runner.main(
        ["plan", "--app-name", APP_NAME, "--deadline-epoch", str(DEADLINE), "--out", str(plan_file)],
        client=estate, environ={}, env_file=env_file,
    ) == 0
    assert oct(plan_file.stat().st_mode & 0o777) == "0o600"
    assert runner.main(["begin", "--plan", str(plan_file), *common], client=estate, environ={}, env_file=env_file) == 0
    for key in ("mip_refresh_silver", "mip_scheduled_refresh"):
        out = tmp_path / f"{key}.env"
        argv = ["run", "--plan", str(plan_file), *common, "--job-key", key, "--deadline-epoch", str(DEADLINE), "--out-env", str(out)]
        assert runner.main(argv, client=estate, environ={}, env_file=env_file, sleep=lambda _s: None, now=lambda: float(NOW)) == 0
        assert "JOB_PHASE_TOUCHES_GOLD=1" in out.read_text(encoding="utf-8")
    assert runner.main(["close", "--plan", str(plan_file), *common], client=estate, environ={}, env_file=env_file) == 0
    assert ledger.read(estate, app_name=APP_NAME)["state"] == "closed"


def test_the_cli_plan_refuses_a_phase_the_job_budget_does_not_admit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    estate = DataPhaseEstate()
    env_file = _env_file(tmp_path)
    monkeypatch.setattr(runner.time, "time", lambda: float(NOW))
    plan_file = tmp_path / "data-plan.json"
    for deadline in ("", str(NOW + 60 * 60)):
        argv = ["plan", "--app-name", APP_NAME, "--deadline-epoch", deadline, "--out", str(plan_file)]
        assert runner.main(argv, client=estate, environ={}, env_file=env_file) == runner.EXIT_REFUSED
        assert "phase_budget_refused" in capsys.readouterr().err
    assert not plan_file.exists() and estate.ledger_bytes() is None
    assert PIPELINE_ID  # the estate's pipeline is the one the plan would have digested
