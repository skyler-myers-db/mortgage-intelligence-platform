"""The data phase's runner never runs an unbound job, never wedges and never overlaps (M1-PR6).

The same SDK-built fakes as tests/unit/test_job_phase_runner.py
(tests/fixtures/data_phase_estate.py): each run reports the job id it was
started for, as ``runs/get`` does. The cases:

* **the bound job id**: a job recreated under a new id with byte-identical
  settings is another definition; a ledger entry naming the old id is stale in
  every status, and a run the ledger names must be a run of the bound job,
  whether re-attached or answered to a token resend;
* **the rerun ack**: an open generation with nothing stale (a smoke that fails
  after every job succeeded, a run no longer readable) is retired only by the
  exact digest of its rerun plan, which no supersede ack satisfies;
* **one job at a time**: no run-now, close or retirement while another entry
  of the generation still runs, planned or not, and no resume while an entry
  outside the phase's plan still runs (a planned one is re-attached);
* **fail closed reads**: five unreadable polls refuse, and a submitted entry
  whose job still has an active run is never superseded;
* **quiet**: the read-only check a release runs under its lease;
* **a deleted bound job**: the Jobs API answers ``runs/list`` of a deleted job
  with ``InvalidParameterValue`` (the fake answers as it does), so its entry is
  ``absent`` only when ``jobs/get`` proves the job gone and no run of its id is
  active: quiet passes the release that rebinds it, the exact supersede ack
  (which names the absence) retires the generation, and a ``--skip-silver``
  phase resumes, reruns and closes past it. Any other read error still refuses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from databricks.sdk.errors import InvalidParameterValue, NotFound, PermissionDenied

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tests.fixtures.data_phase_estate import APP_NAME, CLIENT, DEPLOYER, JOB_IDS, DataPhaseEstate
from tests.unit.test_app_deployment_rollback import SIGNING_KEY
from tools.databricks import job_phase_definition as definitions
from tools.databricks import job_phase_ledger as ledger
from tools.databricks import job_phase_runner as runner

SHA = "c" * 40
LEASE = "lease-PLACEHOLDER"
NOW = 1_800_000_000
DEADLINE = NOW + 5 * 3600
SILVER, REFRESH = "mip_refresh_silver", "mip_scheduled_refresh"
NEW_SILVER_ID = 201


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", derive_gateway_proof_verify_key(SIGNING_KEY))
    monkeypatch.setattr(runner, "_held", lambda ctx: None)


def _ctx(estate: DataPhaseEstate, at: float = NOW) -> runner.Context:
    ticks = [float(at)]
    return runner.Context(
        estate, APP_NAME, LEASE, SHA, sleep=lambda s: ticks.__setitem__(0, ticks[0] + s), now=lambda: ticks[0]
    )


def _plan(estate: DataPhaseEstate, jobs: tuple[str, ...] = (SILVER, REFRESH)) -> dict[str, Any]:
    return definitions.build_plan(
        estate, deployer=DEPLOYER, client_key=CLIENT, app_name=APP_NAME, jobs=jobs, skip_silver=len(jobs) == 1
    )


def _planned(plan: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in plan["jobs"] if item["key"] == key)


def _run(estate: DataPhaseEstate, plan: dict[str, Any], key: str, at: float = NOW) -> tuple[int, str]:
    return runner.run_job(_ctx(estate, at), plan, _planned(plan, key), deadline=DEADLINE)


def _still_running_at_the_deadline(estate: DataPhaseEstate, plan: dict[str, Any], key: str) -> int:
    estate.jobs.next_script = [("RUNNING", "")]
    assert _run(estate, plan, key, at=DEADLINE - 11 * 60)[0] == runner.EXIT_STILL_RUNNING
    estate.jobs.next_script = [("TERMINATED", "SUCCESS")]
    return int(_ledger(estate)["jobs"][key]["run_id"])


def _ledger(estate: DataPhaseEstate) -> dict[str, Any]:
    record = ledger.read(estate, app_name=APP_NAME)
    assert record is not None
    return record


def _entry(planned: dict[str, Any], tok: str, **fields: Any) -> dict[str, Any]:
    return {
        "job_id": planned["job_id"], "definition_sha256": planned["definition_sha256"], "attempt": 1,
        "token": tok, "run_id": "", "status": "submitted", "result": "", **fields,
    }


def _token(plan: dict[str, Any], record: dict[str, Any], key: str) -> str:
    planned = _planned(plan, key)
    return runner.token(
        client_key=CLIENT, job_key=key, definition_sha256=planned["definition_sha256"],
        generation=record["generation"], attempt=1,
    )


def _ack(refusal: pytest.ExceptionInfo[runner.Refused], env: str) -> str:
    return str(refusal.value).split(f"{env}=")[1].split()[0]


# --- the bound job id ------------------------------------------------------------


def test_the_definition_digest_names_the_bound_job_id() -> None:
    estate = DataPhaseEstate()
    before = _planned(_plan(estate), SILVER)
    estate.rebind(SILVER, NEW_SILVER_ID)
    after = _planned(_plan(estate), SILVER)
    assert estate.jobs.settings[SILVER] and after["job_id"] == str(NEW_SILVER_ID)
    assert after["definition_sha256"] != before["definition_sha256"], "same settings, another job"


@pytest.mark.parametrize("status", ("submitted", "running", "succeeded"))
def test_an_entry_for_a_job_recreated_under_a_new_id_is_stale_and_never_resent(status: str) -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    record = runner.begin(_ctx(estate), plan, ack="")
    tok = _token(plan, record, SILVER)
    fields: dict[str, Any] = {"status": status}
    if status != "submitted":
        started = int(estate.jobs.run_now(job_id=JOB_IDS[SILVER], idempotency_token=tok).run_id)
        estate.jobs.scripts[started] = [("TERMINATED", "SUCCESS")]
        fields["run_id"] = str(started)
    runner._record(_ctx(estate), record, SILVER, _entry(_planned(plan, SILVER), tok, **fields))
    calls = len(estate.jobs.run_now_calls)

    estate.rebind(SILVER, NEW_SILVER_ID)
    replanned = _plan(estate)
    with pytest.raises(runner.Refused, match="stale_definition") as refused:
        runner.begin(_ctx(estate), replanned, ack="")
    with pytest.raises(runner.Refused, match="another definition or job id"):
        _run(estate, replanned, SILVER)
    assert len(estate.jobs.run_now_calls) == calls, "the unbound job is neither resent, re-attached nor skipped"

    runner.begin(_ctx(estate), replanned, ack=_ack(refused, runner.ACK_SUPERSEDE_ENV))
    assert _run(estate, replanned, SILVER) == (0, "succeeded (attempt 1)")
    assert estate.jobs.run_now_calls[-1][0] == NEW_SILVER_ID


@pytest.mark.parametrize("path", ("re-attach", "resend"))
def test_a_run_the_ledger_names_must_be_a_run_of_the_bound_job(path: str) -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    record = runner.begin(_ctx(estate), plan, ack="")
    tok = _token(plan, record, SILVER)
    # A run of another job answers: the ledger's run id, or the token's resend.
    other = estate.jobs.run_now(job_id=JOB_IDS["mip_kpi_snapshot"], idempotency_token=tok)
    estate.jobs.scripts[int(other.run_id)] = [("RUNNING", "")]
    fields = {"status": "running", "run_id": str(other.run_id)} if path == "re-attach" else {}
    runner._record(_ctx(estate), record, SILVER, _entry(_planned(plan, SILVER), tok, **fields))
    with pytest.raises(runner.Refused, match="not a run of its bound job"):
        _run(estate, plan, SILVER)
    entry = _ledger(estate)["jobs"][SILVER]
    assert entry["status"] != "succeeded", "nothing is recorded as the bound job's success"


# --- the rerun ack -----------------------------------------------------------------


def test_a_generation_whose_smoke_failed_after_every_job_reruns_only_by_its_exact_rerun_ack() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    old = runner.begin(_ctx(estate), plan, ack="")
    assert _run(estate, plan, SILVER)[0] == _run(estate, plan, REFRESH)[0] == 0
    # The data smoke fails, so close is never reached: a rerun skips both jobs.
    resumed = runner.begin(_ctx(estate), plan, ack="")
    assert resumed["generation"] == old["generation"]
    assert [_run(estate, plan, key)[1] for key in (SILVER, REFRESH)] == ["skipped (succeeded in this generation)"] * 2
    digest = runner.rerun_digest(runner.rerun_body(APP_NAME, resumed))
    with pytest.raises(runner.Refused, match="ledger_rerun") as wrong:
        runner.begin(_ctx(estate), plan, ack="", rerun_ack="0" * 64)
    assert digest in str(wrong.value), "the refusal names the current rerun plan"
    # The supersede ack is another purpose: given the rerun digest it retires nothing.
    assert runner.begin(_ctx(estate), plan, ack=digest)["generation"] == old["generation"]
    assert runner.supersede_digest(runner.rerun_body(APP_NAME, resumed)) != digest

    fresh = runner.begin(_ctx(estate), plan, ack="", rerun_ack=digest)
    assert fresh["generation"] != old["generation"] and fresh["jobs"] == {}
    assert _run(estate, plan, SILVER)[0] == _run(estate, plan, REFRESH)[0] == 0
    assert len(estate.jobs.run_now_calls) == 4


def test_a_rerun_ack_never_satisfies_a_stale_generation() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    record = runner.begin(_ctx(estate), plan, ack="")
    estate.jobs.next_script = [("TERMINATED", "FAILED")]
    _run(estate, plan, SILVER)
    rerun = runner.rerun_digest(runner.rerun_body(APP_NAME, _ledger(estate)))
    estate.jobs.settings[SILVER]["max_concurrent_runs"] = 3
    with pytest.raises(runner.Refused, match="stale_definition"):
        runner.begin(_ctx(estate), _plan(estate), ack="", rerun_ack=rerun)
    assert _ledger(estate)["generation"] == record["generation"]


def test_a_running_entry_whose_run_is_gone_refuses_until_the_rerun_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    runner.begin(_ctx(estate), plan, ack="")
    gone = _still_running_at_the_deadline(estate, plan, SILVER)
    real = estate.jobs.get_run

    def get_run(run_id: int) -> Any:
        if int(run_id) == gone:
            raise NotFound("run purged by retention")
        return real(run_id)

    monkeypatch.setattr(estate.jobs, "get_run", get_run)
    with pytest.raises(runner.Refused, match=runner.ACK_RERUN_ENV):
        _run(estate, plan, SILVER)
    digest = runner.rerun_digest(runner.rerun_body(APP_NAME, _ledger(estate)))
    # The run cannot be read, so the job's own active runs decide: one is active.
    estate.jobs.active[JOB_IDS[SILVER]] = [gone]
    with pytest.raises(runner.Refused, match="ledger_rerun: jobs.mip_refresh_silver of the open generation is still active"):
        runner.begin(_ctx(estate), plan, ack="", rerun_ack=digest)
    estate.jobs.active.clear()
    assert runner.begin(_ctx(estate), plan, ack="", rerun_ack=digest)["jobs"] == {}
    assert len(estate.jobs.run_now_calls) == 1


def test_the_cli_prints_the_rerun_ack_on_every_resume(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    estate = DataPhaseEstate()
    plan_file = tmp_path / "plan.json"
    plan = _plan(estate)
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    argv = ["begin", "--plan", str(plan_file), "--lease-id", LEASE, "--source-git-sha", SHA]
    env_file = tmp_path / ".env.local"
    env_file.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    assert runner.main(argv, client=estate, environ={}, env_file=env_file) == 0
    assert runner.ACK_RERUN_ENV not in capsys.readouterr().out, "a fresh generation has nothing to rerun"
    _run(estate, plan, SILVER)
    assert runner.main(argv, client=estate, environ={}, env_file=env_file) == 0
    digest = runner.rerun_digest(runner.rerun_body(APP_NAME, _ledger(estate)))
    assert f"export {runner.ACK_RERUN_ENV}={digest} for one run" in capsys.readouterr().out
    assert runner.main([*argv, "--ack-rerun", digest], client=estate, environ={}, env_file=env_file) == 0
    assert _ledger(estate)["jobs"] == {}


# --- one job at a time ---------------------------------------------------------------


def test_a_skip_silver_phase_never_resumes_or_closes_under_a_running_silver() -> None:
    estate = DataPhaseEstate()
    full = _plan(estate)
    runner.begin(_ctx(estate), full, ack="")
    silver = _still_running_at_the_deadline(estate, full, SILVER)
    skip = _plan(estate, (REFRESH,))
    with pytest.raises(runner.Refused, match="still running outside this phase's plan"):
        runner.begin(_ctx(estate), skip, ack="")
    # Nor does close: a refresh entry recorded as succeeded beside the running silver.
    record = _ledger(estate)
    planned = _planned(skip, REFRESH)
    done = estate.jobs.run_now(job_id=JOB_IDS[REFRESH], idempotency_token=_token(skip, record, REFRESH))
    entry = _entry(planned, _token(skip, record, REFRESH), status="succeeded", run_id=str(done.run_id))
    runner._record(_ctx(estate), record, REFRESH, entry)
    with pytest.raises(runner.Refused, match="mip_refresh_silver of this generation is still running"):
        runner.close(_ctx(estate), skip)
    assert runner.outcome(estate.jobs.get_run(silver)) == "running"
    assert [job for job, _tok in estate.jobs.run_now_calls].count(JOB_IDS[SILVER]) == 1


def test_no_run_now_while_another_entry_of_the_generation_still_runs() -> None:
    estate = DataPhaseEstate()
    skip = _plan(estate, (REFRESH,))
    runner.begin(_ctx(estate), skip, ack="")
    _still_running_at_the_deadline(estate, skip, REFRESH)
    full = _plan(estate)
    runner.begin(_ctx(estate), full, ack="")
    calls = len(estate.jobs.run_now_calls)
    with pytest.raises(runner.Refused, match="mip_scheduled_refresh of this generation is still running"):
        _run(estate, full, SILVER)
    assert len(estate.jobs.run_now_calls) == calls and SILVER not in _ledger(estate)["jobs"]


def test_a_supersede_never_discards_a_run_that_is_not_stale_but_still_active() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    runner.begin(_ctx(estate), plan, ack="")
    _still_running_at_the_deadline(estate, plan, SILVER)
    # Only the refresh's closure changes: silver is not stale, yet still running.
    estate.jobs.settings["mip_kpi_snapshot"]["max_concurrent_runs"] = 2
    record = _ledger(estate)
    entry = _entry(_planned(plan, REFRESH), _token(plan, record, REFRESH), status="failed", run_id="1")
    runner._record(_ctx(estate), record, REFRESH, entry)
    replanned = _plan(estate)
    with pytest.raises(runner.Refused) as refused:
        runner.begin(_ctx(estate), replanned, ack="")
    with pytest.raises(runner.Refused, match="mip_refresh_silver of the open generation is still active"):
        runner.begin(_ctx(estate), replanned, ack=_ack(refused, runner.ACK_SUPERSEDE_ENV))
    assert _ledger(estate)["state"] == "open"


# --- fail-closed reads ---------------------------------------------------------------


def test_five_unreadable_polls_refuse_and_keep_the_entry_running(monkeypatch: pytest.MonkeyPatch) -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    runner.begin(_ctx(estate), plan, ack="")
    reads: list[int] = []
    real = estate.jobs.get_run

    def get_run(run_id: int) -> Any:
        reads.append(int(run_id))
        if len(reads) > 1:
            raise RuntimeError("network")
        return real(run_id)

    estate.jobs.next_script = [("RUNNING", "")]
    monkeypatch.setattr(estate.jobs, "get_run", get_run)
    with pytest.raises(runner.Refused, match="could not be read"):
        _run(estate, plan, SILVER)
    assert len(reads) == 1 + runner.MAX_READ_FAILURES
    assert _ledger(estate)["jobs"][SILVER]["status"] == "running", "never read as succeeded"


def test_a_stale_submitted_entry_whose_job_still_runs_is_never_superseded() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    record = runner.begin(_ctx(estate), plan, ack="")
    runner._record(_ctx(estate), record, SILVER, _entry(_planned(plan, SILVER), _token(plan, record, SILVER)))
    estate.jobs.active[JOB_IDS[SILVER]] = [4242]
    estate.jobs.settings[SILVER]["max_concurrent_runs"] = 3
    replanned = _plan(estate)
    with pytest.raises(runner.Refused) as refused:
        runner.begin(_ctx(estate), replanned, ack="")
    with pytest.raises(runner.Refused, match="still active"):
        runner.begin(_ctx(estate), replanned, ack=_ack(refused, runner.ACK_SUPERSEDE_ENV))
    assert _ledger(estate)["generation"] == record["generation"]


# --- quiet: the release's read-only check ---------------------------------------------


def test_quiet_refuses_only_while_an_open_generation_still_runs(capsys: pytest.CaptureFixture[str]) -> None:
    estate = DataPhaseEstate()
    argv = ["quiet", "--app-name", APP_NAME]
    assert runner.main(argv, client=estate) == 0, "no ledger"
    plan = _plan(estate)
    runner.begin(_ctx(estate), plan, ack="")
    silver = _still_running_at_the_deadline(estate, plan, SILVER)
    before = dict(estate.workspace.data)
    assert runner.main(argv, client=estate) == runner.EXIT_REFUSED
    assert "jobs.mip_refresh_silver of the open data-phase generation is still running" in capsys.readouterr().err
    assert estate.workspace.data == before and len(estate.jobs.run_now_calls) == 1, "read-only"
    estate.jobs.scripts[silver] = [("TERMINATED", "SUCCESS")]
    assert _run(estate, plan, SILVER)[0] == 0
    assert runner.main(argv, client=estate) == 0, "an open generation with nothing running"


def test_the_plan_writes_nothing_to_the_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    estate = DataPhaseEstate()
    uploads: list[str] = []
    monkeypatch.setattr(estate.workspace, "upload", lambda path, *a, **k: uploads.append(path))
    monkeypatch.setattr(estate.workspace, "delete", lambda path: uploads.append(path))
    monkeypatch.setattr(runner.time, "time", lambda: float(NOW))
    env_file = tmp_path / ".env.local"
    env_file.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    argv = ["plan", "--app-name", APP_NAME, "--deadline-epoch", str(DEADLINE), "--out", str(tmp_path / "plan.json")]
    assert runner.main(argv, client=estate, environ={}, env_file=env_file) == 0
    assert uploads == [] and estate.jobs.run_now_calls == [] and estate.ledger_bytes() is None


# --- a deleted bound job ---------------------------------------------------------------


def _deleted_silver(estate: DataPhaseEstate, plan: dict[str, Any]) -> int:
    """Silver is deleted out of band after the plan: its run-now fails and its entry stays submitted."""

    runner.begin(_ctx(estate), plan, ack="")
    dead = estate.delete(SILVER)
    with pytest.raises(InvalidParameterValue, match=f"Job {dead} does not exist"):
        _run(estate, plan, SILVER)
    assert _ledger(estate)["jobs"][SILVER]["status"] == "submitted"
    return dead


def test_quiet_passes_a_deleted_job_with_no_active_run_and_names_the_remedy(capsys: pytest.CaptureFixture[str]) -> None:
    estate = DataPhaseEstate()
    dead = _deleted_silver(estate, _plan(estate))
    capsys.readouterr()
    before, calls = dict(estate.workspace.data), len(estate.jobs.run_now_calls)
    # A release and a run with no --phase (refuse_while_a_data_job_runs) run exactly this.
    assert runner.main(["quiet", "--app-name", APP_NAME], client=estate) == 0
    out = capsys.readouterr().out
    assert "jobs.mip_refresh_silver of the open data-phase generation names a deleted job with no active run" in out
    assert runner.ACK_SUPERSEDE_ENV in out and "no data-phase job is running" in out
    assert estate.workspace.data == before and len(estate.jobs.run_now_calls) == calls, "read-only"
    # A run of the deleted job id still active anywhere in the workspace keeps it running.
    estate.jobs.active[dead] = [4242]
    assert runner.main(["quiet", "--app-name", APP_NAME], client=estate) == runner.EXIT_REFUSED
    assert "jobs.mip_refresh_silver of the open data-phase generation is still running" in capsys.readouterr().err


def test_a_deleted_job_rebound_by_a_release_is_retired_only_by_the_supersede_ack_naming_its_absence() -> None:
    estate = DataPhaseEstate()
    plan = _plan(estate)
    dead = _deleted_silver(estate, plan)
    entry = _ledger(estate)["jobs"][SILVER]
    estate.rebind(SILVER, NEW_SILVER_ID)
    replanned = _plan(estate)
    assert runner.main(["quiet", "--app-name", APP_NAME], client=estate) == 0, "the rebinding release is never refused"
    with pytest.raises(runner.Refused, match="stale_definition") as refused:
        runner.begin(_ctx(estate), replanned, ack="")
    ack = _ack(refused, runner.ACK_SUPERSEDE_ENV)
    named = {
        "status": "submitted", "run_id": "", "ledger_definition_sha256": entry["definition_sha256"],
        "live_definition_sha256": _planned(replanned, SILVER)["definition_sha256"], "ledger_job_id": str(dead),
        "ledger_job_present": False, "live_job_id": str(NEW_SILVER_ID),
    }
    body = {"purpose": runner.SUPERSEDE_PURPOSE, "app_name": APP_NAME, "generation": _ledger(estate)["generation"]}
    assert ack == runner.supersede_digest({**body, "stale": {SILVER: named}}), "the ack names the deleted job"
    with pytest.raises(runner.Refused, match="stale_definition"):
        runner.begin(_ctx(estate), replanned, ack="", rerun_ack=runner.rerun_digest(runner.rerun_body(APP_NAME, _ledger(estate))))
    fresh = runner.begin(_ctx(estate), replanned, ack=ack)
    assert fresh["jobs"] == {} and fresh["generation"] != body["generation"]
    assert _run(estate, replanned, SILVER) == (0, "succeeded (attempt 1)")
    assert estate.jobs.run_now_calls[-1][0] == NEW_SILVER_ID


@pytest.mark.parametrize("path", ("resume_and_close", "rerun_ack"))
def test_a_skip_silver_phase_resumes_reruns_and_closes_past_a_deleted_silver(path: str) -> None:
    estate = DataPhaseEstate()
    _deleted_silver(estate, _plan(estate))
    generation = _ledger(estate)["generation"]
    skip = _plan(estate, (REFRESH,))
    if path == "rerun_ack":
        digest = runner.rerun_digest(runner.rerun_body(APP_NAME, _ledger(estate)))
        assert runner.begin(_ctx(estate), skip, ack="", rerun_ack=digest)["jobs"] == {}
        return
    assert runner.begin(_ctx(estate), skip, ack="")["generation"] == generation, "resumed past the absent silver"
    assert _run(estate, skip, REFRESH) == (0, "succeeded (attempt 1)")
    assert runner.close(_ctx(estate), skip)["state"] == "closed"


@pytest.mark.parametrize("fault", ("refused_for_a_present_job", "transient", "deleted_but_get_unreadable"))
def test_an_unproven_absence_still_refuses(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    estate = DataPhaseEstate()
    plan, skip = _plan(estate), _plan(estate, (REFRESH,))
    record = runner.begin(_ctx(estate), plan, ack="")
    runner._record(_ctx(estate), record, SILVER, _entry(_planned(plan, SILVER), _token(plan, record, SILVER)))
    error = RuntimeError("network") if fault == "transient" else InvalidParameterValue("Job 101 does not exist.")

    real = estate.jobs.list_runs

    def list_runs(*, active_only: bool, job_id: int | None = None) -> Any:
        if job_id is None:
            return real(active_only=active_only)  # the workspace-wide listing still answers
        raise error

    def get(job_id: int) -> Any:
        raise PermissionDenied("the job cannot be read")

    monkeypatch.setattr(estate.jobs, "list_runs", list_runs)
    if fault == "deleted_but_get_unreadable":
        estate.delete(SILVER)
        monkeypatch.setattr(estate.jobs, "get", get)
    assert runner.main(["quiet", "--app-name", APP_NAME], client=estate) == runner.EXIT_REFUSED
    with pytest.raises(Exception) as refused:
        runner.begin(_ctx(estate), skip, ack="")
    assert not isinstance(refused.value, runner.Refused), "the read error itself: never judged idle"
    assert _ledger(estate)["generation"] == record["generation"]
