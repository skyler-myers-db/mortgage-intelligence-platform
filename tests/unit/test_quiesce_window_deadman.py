"""The window arms its dead-man before the stop and cancels it when the journal closes (M0-PR9).

The open publishes this run's staged release (create-only, byte-verified),
re-reads the dead-man's published bytes, re-admits the budget and submits one
one-time run whose source is exactly ``app-src/<release id>/jobs/window_deadman.py``.
The idempotency token is stable per window and differs across windows; the
run's timeout is the time left to the deadline plus the start budget; its
environment matches the bundle's serverless jobs with the SDK pinned to
uv.lock's. An arm that fails journals and stops nothing; a journal write that
fails cancels the armed run; the cancel refuses a run that is not this App's
dead-man and deletes nothing.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    LEASE_ID,
    OTHER_SOURCE,
    green_release,
    window_hooks,
)
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    GIT_SHA,
    _env,
    _estate,
    _open,
    _plan,
    _stop,
)
from tools.databricks import app_release_contract as contract
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_deadman as deadman
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_release_source import PublishPlan, PublishReport
from tools.databricks.quiesce_window_budget import WindowAdmissionError

REPO = Path(__file__).resolve().parents[2]


def _parameters(run: Any) -> dict[str, str]:
    values = list(run.tasks[0].spark_python_task.parameters)
    return dict(zip(values[::2], values[1::2], strict=True))


def _open_with(estate: Any, **hooks: Any) -> dict[str, Any]:
    plan = _plan(estate)
    return window.open_window(
        estate.workspace,
        app_name=APP_NAME,
        plan=plan.full(),
        acknowledgement=plan.sha256,
        lease_id=LEASE_ID,
        source_git_sha=GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(estate),
        **{**window_hooks(estate), **hooks},
    )


# --- the source --------------------------------------------------------------------


def test_the_dead_mans_source_is_the_published_release_folder_never_a_mutable_root() -> None:
    estate = _estate()
    release = green_release()
    opened = _open_with(estate, **window_hooks(estate, release=release))
    run = estate.jobs.only_run()
    python_file = run.tasks[0].spark_python_task.python_file

    assert python_file == f"{release.release_path}/jobs/window_deadman.py"
    assert contract.release_id_in(python_file.removesuffix("/jobs/window_deadman.py")) == release.manifest.release_id
    assert "/files/" not in python_file and "/app-src/" in python_file
    published = estate.files.data[contract.api_path(python_file)]
    assert published == (REPO / "jobs" / "window_deadman.py").read_bytes(), "the reviewed commit's bytes"
    for entry in release.manifest.entries:
        assert estate.files.data[contract.api_path(f"{release.release_path}/{entry.path}")], entry.path
    assert opened["state"] == "window_open" and estate.apps.stops == 1


def test_a_published_folder_that_is_not_the_release_refuses_the_arm() -> None:
    release = green_release()
    files_root = contract.files_root(principal="deployer-app-id-PLACEHOLDER", target="client", root_leaf="client-key")
    for path in (files_root, OTHER_SOURCE, release.release_path.replace(release.manifest.release_id, "c" * 12 + "-" + "d" * 12)):
        with pytest.raises((RuntimeError, contract.ReleaseContractError)):
            deadman.deadman_source(PublishReport(path, 0, 0), release)
    assert deadman.deadman_source(PublishReport(release.release_path, 1, 0), release).endswith(
        "/jobs/window_deadman.py"
    )


def test_a_release_without_the_dead_man_refuses_with_no_submit_and_no_stop() -> None:
    estate = _estate()
    release = green_release()
    stripped = PublishPlan(
        contract.Manifest(
            release.manifest.commit,
            release.manifest.release_id,
            tuple(entry for entry in release.manifest.entries if entry.path != deadman.DEADMAN_RELPATH),
        ),
        release.stage_dir,
        release.release_path,
        1,
    )
    with pytest.raises(RuntimeError, match="does not carry the dead-man"):
        _open_with(estate, **window_hooks(estate, release=stripped))
    assert (estate.jobs.submits, estate.apps.stops) == (0, 0)
    assert journal.read(estate.workspace, app_name=APP_NAME) is None


def test_published_bytes_that_differ_from_the_manifest_refuse_the_submit() -> None:
    estate = _estate()
    release = green_release()
    real_upload = estate.files.upload

    def tamper(path: str, content: Any, **kwargs: Any) -> None:
        real_upload(path, content, **kwargs)
        if path.endswith("/jobs/window_deadman.py"):
            estate.files.data[path] = b"print('not the reviewed dead-man')\n"

    estate.files.upload = tamper  # type: ignore[method-assign]
    with pytest.raises(contract.ReleaseContractError, match="jobs/window_deadman.py"):
        _open_with(estate, **window_hooks(estate, release=release))
    assert (estate.jobs.submits, estate.apps.stops) == (0, 0)


def test_bytes_changed_after_the_publish_refuse_the_submit() -> None:
    # The publish verified every file; the dead-man's own bytes are read again
    # right before the submit, so a change in between is still refused.
    estate = _estate()
    release = green_release()
    plan = _plan(estate)

    def publish_then_tamper(workspace: Any, plan_: PublishPlan) -> PublishReport:
        from tools.databricks.app_release_source import upload_release

        report = upload_release(workspace, plan_)
        target = contract.api_path(f"{report.release_path}/{deadman.DEADMAN_RELPATH}")
        estate.files.data[target] = estate.files.data[target].replace(b"apps.start", b"apps.stop")
        return report

    with pytest.raises(RuntimeError, match="not the manifest's bytes"):
        deadman.arm_deadman(
            estate.workspace,
            app_name=APP_NAME,
            app_id="app-object-id",
            canonical=plan.canonical,
            window_sha256=plan.sha256,
            lease_id=LEASE_ID,
            release=release,
            deadline=int(time.time()) + 3600,
            admit=lambda: None,
            publish=publish_then_tamper,
        )
    assert estate.jobs.submits == 0


# --- the submit --------------------------------------------------------------------


def test_the_submit_is_one_bounded_serverless_run_as_the_deployer() -> None:
    estate = _estate()
    before = time.time()
    _open_with(estate, **window_hooks(estate, budget_s=2 * 3600))
    run = estate.jobs.only_run()
    request = run.request
    parameters = _parameters(run)
    deadline = int(parameters["--deadline"])

    assert "run_as" not in request and "access_control_list" not in request, "the submitter owns it"
    assert request["run_name"] == deadman.run_name(APP_NAME, request["idempotency_token"])
    assert before + 2 * 3600 - 5 <= deadline <= time.time() + 2 * 3600
    remaining = deadline - before
    assert remaining + deadman.DEADMAN_START_BUDGET_S - 5 <= request["timeout_seconds"]
    assert request["timeout_seconds"] <= remaining + deadman.DEADMAN_START_BUDGET_S + 5
    task = request["tasks"][0]
    assert task.max_retries == 0 and task.timeout_seconds == request["timeout_seconds"]
    environment = request["environments"][0]
    assert task.environment_key == environment.environment_key
    assert environment.spec.environment_version == "5"
    lock = (REPO / "uv.lock").read_text(encoding="utf-8")
    pin = re.search(r"^databricks-sdk==(\S+)", lock, re.M)
    assert pin and environment.spec.dependencies == [f"databricks-sdk=={pin.group(1)}"]
    assert parameters["--blue-restorable"] == "true" and parameters["--app-id"] == "app-object-id"
    assert parameters["--journal"] == journal.journal_path(APP_NAME)
    assert parameters["--marker"] == deadman.marker_path(APP_NAME, request["idempotency_token"])
    assert parameters["--start-timeout-seconds"] == str(deadman.DEADMAN_START_WAIT_S)


def test_the_sdk_pin_is_the_published_releases_uv_lock_never_the_installed_version() -> None:
    # Review: the pin was importlib.metadata.version, the deployer's own venv.
    from importlib.metadata import version

    lock = (REPO / "uv.lock").read_text(encoding="utf-8")
    pinned = re.sub(r"^databricks-sdk==\S+$", "databricks-sdk==9.9.9", lock, count=1, flags=re.M)
    assert pinned != lock and version("databricks-sdk") != "9.9.9"
    estate = _estate()
    _open_with(estate, **window_hooks(estate, release=green_release(extra={"uv.lock": pinned.encode()})))
    environment = estate.jobs.only_run().request["environments"][0]
    assert environment.spec.dependencies == ["databricks-sdk==9.9.9"]


@pytest.mark.parametrize(
    "lock",
    (None, b"requests==2.32.5\n", b"databricks-sdk==0.1.0\ndatabricks-sdk==0.2.0\n"),
    ids=("absent", "unpinned", "twice"),
)
def test_a_release_whose_uv_lock_does_not_pin_the_sdk_once_refuses_before_any_publish(lock: bytes | None) -> None:
    estate = _estate()
    release = green_release(extra={} if lock is None else {"uv.lock": lock})
    if lock is None:
        (release.stage_dir / "uv.lock").unlink()
        release = PublishPlan(
            contract.Manifest(
                release.manifest.commit,
                release.manifest.release_id,
                tuple(entry for entry in release.manifest.entries if entry.path != "uv.lock"),
            ),
            release.stage_dir,
            release.release_path,
            1,
        )
    files = dict(estate.files.data)
    with pytest.raises(RuntimeError, match="uv.lock"):
        _open_with(estate, **window_hooks(estate, release=release))
    assert (estate.jobs.submits, estate.apps.stops) == (0, 0) and estate.files.data == files, "nothing published"


def test_the_bundles_serverless_jobs_use_the_same_environment_version() -> None:
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")
    assert f'environment_version: "{deadman.ENVIRONMENT_VERSION}"' in bundle


def test_the_idempotency_token_is_stable_per_window_and_differs_across_windows() -> None:
    from tests.fixtures.quiesce_window_estate import load_deadman

    job = load_deadman((REPO / "jobs" / "window_deadman.py").read_bytes(), "window_deadman_token_check")
    first, again = deadman.window_token("a" * 64, LEASE_ID), deadman.window_token("a" * 64, LEASE_ID)
    assert first == again == job["window_token"]("a" * 64, LEASE_ID), "the job derives the same token"
    assert len({first, deadman.window_token("b" * 64, LEASE_ID), deadman.window_token("a" * 64, "lease-2")}) == 3
    assert len(first) == 64

    estate = _estate()
    hooks = window_hooks(estate)
    plan = _plan(estate)
    run_id = hooks["arm"](plan.canonical)
    assert hooks["arm"](plan.canonical) == run_id, "a retried submit returns the same run"
    assert estate.jobs.submits == 2 and len(estate.jobs.runs) == 1


def test_the_budget_is_admitted_again_after_the_publish_and_before_the_submit() -> None:
    estate = _estate()
    release = green_release()
    calls: list[str] = []

    def slow_publish(workspace: Any, plan: PublishPlan) -> PublishReport:
        from tools.databricks.app_release_source import upload_release

        calls.append("publish")
        return upload_release(workspace, plan)

    def refuse() -> None:
        calls.append("admit")
        raise WindowAdmissionError(SimpleNamespace(state="insufficient", remaining_seconds=1, required_seconds=2))  # type: ignore[arg-type]

    plan = _plan(estate)
    with pytest.raises(WindowAdmissionError):
        deadman.arm_deadman(
            estate.workspace,
            app_name=APP_NAME,
            app_id="app-object-id",
            canonical=plan.canonical,
            window_sha256=plan.sha256,
            lease_id=LEASE_ID,
            release=release,
            deadline=int(time.time()) + 3600,
            admit=refuse,
            publish=slow_publish,
        )
    assert calls == ["publish", "admit"] and estate.jobs.submits == 0


# --- the open ----------------------------------------------------------------------


def test_the_open_arms_before_the_journal_and_stops_after_both() -> None:
    estate = _estate()
    order: list[str] = []
    real_submit, real_upload, real_stop = estate.jobs.submit, estate.files.upload, estate.apps.stop

    def submit(**request: Any) -> Any:
        order.append("submit")
        return real_submit(**request)

    def upload(path: str, content: Any, **kwargs: Any) -> None:
        if path == journal.journal_path(APP_NAME):
            order.append("journal")
        return real_upload(path, content, **kwargs)

    def stop(name: str) -> Any:
        order.append("stop")
        return real_stop(name)

    estate.jobs.submit, estate.files.upload, estate.apps.stop = submit, upload, stop  # type: ignore[method-assign]
    _open_with(estate)
    assert order == ["submit", "journal", "stop"]


def test_an_arm_that_fails_journals_nothing_and_stops_nothing() -> None:
    estate = _estate()
    estate.jobs.fail_submit = True
    with pytest.raises(ConnectionError):
        _open_with(estate)
    assert estate.apps.stops == 0 and journal.read(estate.workspace, app_name=APP_NAME) is None


def test_a_journal_write_that_fails_cancels_the_armed_run_and_stops_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estate = _estate()

    def unwritable(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("the workspace is unreachable")

    monkeypatch.setattr(window.journal, "write", unwritable)
    with pytest.raises(ConnectionError):
        _open_with(estate)
    run = estate.jobs.only_run()
    assert estate.jobs.cancels == [run.run_id] and estate.apps.stops == 0


def test_the_arm_waits_until_the_run_is_running_and_wrote_its_armed_marker() -> None:
    estate = _estate()
    order: list[str] = []
    real_get_run, real_upload = estate.jobs.get_run, estate.files.upload

    def get_run(run_id: int) -> Any:
        order.append("get_run")
        return real_get_run(run_id)

    def upload(path: str, content: Any, **kwargs: Any) -> None:
        if ".armed-" in path:
            order.append("armed")
        elif path == journal.journal_path(APP_NAME):
            order.append("journal")
        return real_upload(path, content, **kwargs)

    estate.jobs.get_run, estate.files.upload = get_run, upload  # type: ignore[method-assign]
    _open_with(estate)
    run = estate.jobs.only_run()
    token = run.request["idempotency_token"]
    assert order == ["armed", "get_run", "journal"], "armed by the published run, proven, then journaled"
    armed = json.loads(estate.files.data[deadman.armed_path(APP_NAME, token)])
    assert armed["state"] == "deadman_armed" and armed["token"] == token
    assert _parameters(run)["--armed-marker"] == deadman.armed_path(APP_NAME, token)


def test_a_run_that_fails_to_start_refuses_the_window_with_no_stop() -> None:
    # A serverless environment, the workspace file or a policy can fail the run
    # after the submit returned its id: the window is never left without its restart.
    estate = _estate()
    estate.jobs.start = "fails"
    with pytest.raises(RuntimeError, match="ended before it armed"):
        _open_with(estate)
    assert estate.apps.stops == 0 and journal.read(estate.workspace, app_name=APP_NAME) is None
    assert estate.jobs.only_run().state.life_cycle_state == "INTERNAL_ERROR" and estate.jobs.cancels == []


def test_a_run_that_never_arms_is_cancelled_and_refuses_the_window_with_no_stop() -> None:
    estate = _estate()
    estate.jobs.start = "silent"
    now = [time.time()]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(RuntimeError, match="did not arm within"):
        _open_with(estate, **window_hooks(estate, clock=lambda: now[0], sleep=sleep))
    run = estate.jobs.only_run()
    assert estate.jobs.cancels == [run.run_id], "the unarmed run is cancelled"
    assert estate.apps.stops == 0 and journal.read(estate.workspace, app_name=APP_NAME) is None
    assert now[0] >= deadman.ARM_WAIT_S, "bounded by the arm wait"


def test_an_armed_marker_of_another_window_refuses_the_window() -> None:
    estate = _estate()
    estate.jobs.start = "silent"
    plan = _plan(estate)
    token = deadman.window_token(plan.sha256, LEASE_ID)
    estate.files.data[deadman.armed_path(APP_NAME, token)] = json.dumps(
        {"schema": deadman.ARMED_SCHEMA, "app_name": APP_NAME, "token": token, "window_sha256": "e" * 64}
    ).encode()
    with pytest.raises(RuntimeError, match="not this window's"):
        _open_with(estate)
    assert estate.apps.stops == 0 and estate.jobs.cancels == [estate.jobs.only_run().run_id]


def test_a_deployment_that_lands_during_the_arm_refuses_the_window_with_no_stop() -> None:
    # Time of check, time of use: the arm publishes a whole release and waits
    # for the run, so blue is re-checked after it, right before the journal.
    from tests.fixtures.quiesce_window_estate import deployment

    estate = _estate()
    hooks = window_hooks(estate)
    real_arm = hooks["arm"]

    def arm_while_someone_deploys(canonical: Any) -> str:
        run_id = real_arm(canonical)
        estate.apps.deployments["deployment-out-of-band"] = deployment(
            "deployment-out-of-band",
            source=OTHER_SOURCE,
            artifact="/Workspace/Users/app-sp/src/deployment-out-of-band",
            env_names=["DATABRICKS_WAREHOUSE_ID"],
            created="2026-10-05T00:00:00Z",
        )
        estate.apps.active_id = "deployment-out-of-band"
        return run_id

    with pytest.raises(RuntimeError, match="during the arm"):
        _open_with(estate, **{**hooks, "arm": arm_while_someone_deploys})
    run = estate.jobs.only_run()
    assert estate.apps.stops == 0 and journal.read(estate.workspace, app_name=APP_NAME) is None
    assert estate.jobs.cancels == [run.run_id], "the armed run is cancelled"


def test_a_budget_that_runs_out_during_the_arm_refuses_the_window_with_no_stop() -> None:
    estate = _estate()
    hooks = window_hooks(estate)
    admits: list[str] = []

    def admit(canonical: Any) -> object:
        # The open's own admissions: before the arm, then after it (the arm
        # re-admits on its own before the submit, through the hooks).
        admits.append("admit")
        if len(admits) == 2:
            raise WindowAdmissionError(SimpleNamespace(state="insufficient", remaining_seconds=1, required_seconds=2))  # type: ignore[arg-type]
        return hooks["admit"](canonical)

    with pytest.raises(WindowAdmissionError):
        _open_with(estate, **{**hooks, "admit": admit})
    run = estate.jobs.only_run()
    assert len(admits) == 2 and estate.apps.stops == 0
    assert estate.jobs.cancels == [run.run_id] and journal.read(estate.workspace, app_name=APP_NAME) is None


@pytest.mark.parametrize("states", (("window_open",), ("window_open", "window_starting")))
def test_an_earlier_open_journal_refuses_before_any_submit(states: tuple[str, ...]) -> None:
    # The plan saw no journal; one written since (time of check, time of use)
    # refuses the open before the arm.
    estate = _estate()
    plan = _plan(estate)
    current = None
    for state in states:
        current = journal.write(
            estate.workspace,
            app_name=APP_NAME,
            expected=current,
            changes={"state": state, "window_sha256": "e" * 64, "lease_id": "lease-earlier"},
            assert_lease_held=lambda: None,
        )
    with pytest.raises(RuntimeError, match="still open"):
        window.open_window(
            estate.workspace,
            app_name=APP_NAME,
            plan=plan.full(),
            acknowledgement=plan.sha256,
            lease_id=LEASE_ID,
            source_git_sha=GIT_SHA,
            assert_lease_held=lambda: None,
            stop=_stop(estate),
            **window_hooks(estate),
        )
    assert (estate.jobs.submits, estate.apps.stops) == (0, 0)


# --- the cancel --------------------------------------------------------------------


def test_the_cancel_cancels_only_this_apps_running_dead_man_and_deletes_nothing() -> None:
    estate = _estate()
    _open(estate)
    run = estate.jobs.only_run()
    files = dict(estate.files.data)

    assert deadman.cancel_deadman(estate.workspace, app_name=APP_NAME, run_id=str(run.run_id)) == "cancelled"
    assert deadman.cancel_deadman(estate.workspace, app_name=APP_NAME, run_id=str(run.run_id)) == "already_terminated"
    assert estate.jobs.cancels == [run.run_id] and estate.jobs.runs and estate.files.data == files
    with pytest.raises(RuntimeError, match="not this App's dead-man"):
        deadman.cancel_deadman(estate.workspace, app_name="another-app", run_id=str(run.run_id))
    run.tasks[0].spark_python_task.python_file = "/Workspace/Users/x/.bundle/mortgage-intelligence-platform/client/files/jobs/window_deadman.py"
    with pytest.raises(RuntimeError, match="not this App's dead-man"):
        deadman.cancel_deadman(estate.workspace, app_name=APP_NAME, run_id=str(run.run_id))
    with pytest.raises(RuntimeError, match="not a run id"):
        deadman.cancel_deadman(estate.workspace, app_name=APP_NAME, run_id="4242; rm -rf /")


def test_the_cancel_never_takes_another_apps_dead_man_for_this_apps() -> None:
    # Review: App "mip" accepted App "mip-app"'s run by its name prefix.
    estate = _estate()
    _open(estate)
    run = estate.jobs.only_run()
    assert run.run_name.startswith(f"{deadman._RUN_PREFIX}mip-")
    with pytest.raises(RuntimeError, match="not this App's dead-man"):
        deadman.cancel_deadman(estate.workspace, app_name="mip", run_id=str(run.run_id))
    task = run.tasks[0].spark_python_task
    for flag, value in (("--app", "another-app"), ("--token", "f" * 64)):
        original = list(task.parameters)
        task.parameters[task.parameters.index(flag) + 1] = value
        with pytest.raises(RuntimeError, match="not this App's dead-man"):
            deadman.cancel_deadman(estate.workspace, app_name=APP_NAME, run_id=str(run.run_id))
        task.parameters = original
    assert estate.jobs.cancels == []
    assert deadman.cancel_deadman(estate.workspace, app_name=APP_NAME, run_id=str(run.run_id)) == "cancelled"


def test_the_cancel_mode_reports_and_never_raises_on_a_terminated_run(capsys: pytest.CaptureFixture[str]) -> None:
    estate = _estate()
    _open(estate)
    run = estate.jobs.only_run()
    argv = ["cancel-deadman", "--app-name", APP_NAME, "--run-id", str(run.run_id)]
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    out = capsys.readouterr().out
    assert "cancel-deadman: cancelled" in out and "cancel-deadman: already_terminated" in out


# --- the run never races the dead-man ---------------------------------------------


def test_within_the_guard_of_the_deadline_the_run_leaves_the_start_to_the_dead_man() -> None:
    estate = _estate()
    _open(estate)
    now = time.time()
    with pytest.raises(RuntimeError, match="dead-man owns"):
        window.start_window(
            estate.workspace,
            app_name=APP_NAME,
            assert_lease_held=lambda: None,
            stop=_stop(estate),
            deadline=int(now + window.DEADMAN_GUARD_S - 1),
            clock=lambda: now,
        )
    assert estate.apps.starts == 0
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open", "no intent journaled"
    started = window.start_window(
        estate.workspace,
        app_name=APP_NAME,
        assert_lease_held=lambda: None,
        stop=_stop(estate),
        deadline=int(now + window.DEADMAN_GUARD_S + 60),
        clock=lambda: now,
    )
    assert started == "deployment-start-1"


def test_the_start_guard_covers_the_start_and_the_re_pin() -> None:
    # Review: a 120 s guard let the run begin a start the job's kill cut off
    # half-way (start wait 600 s, re-pin 300 s), left for the owner.
    from tools.databricks.quiesce_window_plan import ADMISSION_MARGIN_S, STEP_BUDGET_S

    budget = STEP_BUDGET_S["start"] + STEP_BUDGET_S["repin"]
    assert budget < window.DEADMAN_GUARD_S < budget + ADMISSION_MARGIN_S, "the admitted restore fits outside it"
    estate = _estate()
    _open(estate)
    now = time.time()
    with pytest.raises(RuntimeError, match="dead-man owns"):
        window.start_window(
            estate.workspace,
            app_name=APP_NAME,
            assert_lease_held=lambda: None,
            stop=_stop(estate),
            deadline=int(now + budget),
            clock=lambda: now,
        )
    assert estate.apps.starts == 0


def test_the_start_mode_requires_the_deadline() -> None:
    with pytest.raises(SystemExit, match="job deadline"):
        window.main(
            ["start", "--app-name", APP_NAME, "--deployment-lease-id", LEASE_ID, "--deployment-source-git-sha", GIT_SHA],
            workspace_factory=lambda: SimpleNamespace(),
        )


# --- the CLI: open arms from the staged release, end to end ------------------------


def test_the_open_mode_publishes_the_staged_release_and_hands_the_shell_the_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from tests.fixtures.quiesce_window_estate import CLIENT_KEY, DEPLOYER, GREEN_COMMIT
    from tools.databricks import app_deployment_lease

    estate = _estate()
    release = green_release(tmp_path / "release", extra={"requirements.txt": b"# deps-digest\n"})
    plan = _plan(estate)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan.full()), encoding="utf-8")
    monkeypatch.setenv(window.ACK_ENV, plan.sha256)
    monkeypatch.setattr(app_deployment_lease, "assert_held", lambda *_a, **_k: None)
    out = tmp_path / "open.env"
    argv = [
        "open",
        "--app-name", APP_NAME,
        "--plan", str(plan_file),
        "--deadline-epoch", str(int(time.time()) + 3 * 3600),
        "--release-id", release.manifest.release_id,
        "--stage-dir", str(release.stage_dir),
        "--manifest", str(tmp_path / "release" / "manifest.json"),
        "--expected-principal", DEPLOYER,
        "--expected-target", "client",
        "--expected-root-leaf", CLIENT_KEY,
        "--deployment-lease-id", LEASE_ID,
        "--deployment-source-git-sha", GREEN_COMMIT,
        "--expected-app-id", "app-object-id",
        "--expected-client-id", "app-client",
        "--expected-scim-id", "app-scim-id",
        "--out-env", str(out),
    ]  # fmt: skip

    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    run = estate.jobs.only_run()
    assert f"MIP_QUIESCE_WINDOW_DEADMAN_RUN_ID={run.run_id}\n" in out.read_text(encoding="utf-8")
    assert run.tasks[0].spark_python_task.python_file == f"{release.release_path}/jobs/window_deadman.py"
    assert estate.apps.stops == 1 and journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open"


def test_the_open_mode_refuses_an_unbudgeted_window_with_no_publish_no_submit_and_no_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    estate = _estate()
    plan = _plan(estate)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan.full()), encoding="utf-8")
    monkeypatch.setenv(window.ACK_ENV, plan.sha256)
    files = dict(estate.files.data)
    argv = [
        "open", "--app-name", APP_NAME, "--plan", str(plan_file), "--deadline-epoch", "",
        "--release-id", "aaaaaaaaaaaa-bbbbbbbbbbbb", "--stage-dir", str(tmp_path), "--manifest", str(plan_file),
        "--expected-principal", "deployer", "--expected-target", "client",
        "--deployment-lease-id", LEASE_ID, "--deployment-source-git-sha", GIT_SHA,
    ]  # fmt: skip
    with pytest.raises(WindowAdmissionError, match="deadline_missing"):
        window.main(argv, workspace_factory=lambda: estate.workspace)
    assert (estate.jobs.submits, estate.apps.stops) == (0, 0) and estate.files.data == files
