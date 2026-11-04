"""The window library's M0-PR9 shell: the job deadline and the dead-man's lifecycle.

The harness is M0-PR8's (``tests/unit/test_quiesce_window_shell.py``): the
real window and EXIT-trap libraries with a fake ``$PYTHON`` that records each
tool call. ``deploy.sh`` reads the job's deadline (``--deadline`` or
``MIP_JOB_DEADLINE_EPOCH``) and hands it to every window tool that decides on
it; the dry run prints the admission without a Databricks call.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tests.unit.test_quiesce_window_shell import _has, _run

REPO = Path(__file__).resolve().parents[2]
DEADLINE = "1800000000"


def _parse(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """deploy.sh's argument parse alone (it ends before any helper or step)."""

    text = deploy_entrypoint_text()
    parse = text[text.index("DRY_RUN=0\n") : text.index("# Pretty-print helpers")]
    ambient = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("GITHUB_", "MIP_JOB_DEADLINE_EPOCH"))
    }
    return subprocess.run(
        ["bash", "-c", parse + 'printf "deadline=%s\\n" "${MIP_JOB_DEADLINE_EPOCH:-}"\n', "deploy.sh", *args],
        cwd=REPO,
        env={**ambient, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("args", "env", "deadline"),
    (
        (("--deadline", DEADLINE), {}, DEADLINE),
        ((f"--deadline={DEADLINE}",), {}, DEADLINE),
        ((), {"MIP_JOB_DEADLINE_EPOCH": DEADLINE}, DEADLINE),
        (("--deadline", "1900000000"), {"MIP_JOB_DEADLINE_EPOCH": DEADLINE}, "1900000000"),
        ((), {}, ""),
    ),
    ids=("flag", "flag-equals", "workflow-env", "flag-wins", "none"),
)
def test_deploy_reads_the_job_deadline_from_the_flag_or_the_environment(
    args: tuple[str, ...], env: dict[str, str], deadline: str
) -> None:
    result = _parse("-t", "client", *args, env=env)
    assert result.returncode == 0, result.stderr
    assert f"deadline={deadline}\n" in result.stdout


def test_a_deadline_flag_without_a_value_refuses_before_any_step() -> None:
    result = _parse("-t", "client", "--deadline")
    assert result.returncode == 2 and "missing value for --deadline" in result.stderr


def test_the_dry_run_prints_the_admission(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        f'DRY_RUN=1; MIP_JOB_DEADLINE_EPOCH={DEADLINE}\nplan_quiesce_window pre-lease "$TMPDIR/r.json"\n',
    )
    assert result.returncode == 0, result.stderr
    assert calls == [f"-m tools.databricks.quiesce_window_budget describe --deadline-epoch {DEADLINE}"]


@pytest.mark.parametrize("deadline", (DEADLINE, ""))
def test_the_plan_and_the_open_are_handed_the_deadline(tmp_path: Path, deadline: str) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        f'MIP_JOB_DEADLINE_EPOCH="{deadline}"\nQUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS=WR3\n'
        'plan_quiesce_window pre-apply "$TMPDIR/report.json"\nensure_quiesce_window_open_for WR3\n',
        PLAN_CODE="window",
        PLAN_REASONS_JSON='["WR3"]',
    )
    assert result.returncode == 0, result.stderr
    plan = next(call for call in calls if "quiesce_window plan" in call)
    opened = next(call for call in calls if "quiesce_window open" in call)
    for call in (plan, opened):
        assert f"--deadline-epoch {deadline} --" in call, "empty: the tools refuse a window"
    assert _has(calls, "quiesce_window open") == 1


# --- the dead-man's cancel: every path that closes the journal, and only those ------

SEQUENCE = """QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS="$REASONS"
ensure_quiesce_window_open_for WR3
close_quiesce_window
echo "final=$QUIESCE_WINDOW_STATE run=${QUIESCE_WINDOW_DEADMAN_RUN_ID:-none}"
"""


def _cancels(calls: list[str]) -> list[str]:
    return [call for call in calls if "quiesce_window cancel-deadman" in call]


def test_the_open_carries_the_release_and_the_close_cancels_the_armed_run(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, SEQUENCE, REASONS="WR3", DEADMAN_RUN_ID="7001")
    assert result.returncode == 0, result.stderr
    opened = next(call for call in calls if "quiesce_window open" in call)
    for flag in (
        "--release-id aaaaaaaaaaaa-bbbbbbbbbbbb",
        "--stage-dir .databricks/app-release/source",
        "--manifest .databricks/app-release/manifest.json",
        "--expected-principal deployer",
        "--expected-target client",
        "--expected-root-leaf client-key",
        "--expected-app-id app-id",
    ):
        assert flag in opened, flag
    assert _cancels(calls) == ["-m tools.databricks.quiesce_window cancel-deadman --app-name mip-app --run-id 7001"]
    close = next(i for i, call in enumerate(calls) if "quiesce_window close" in call)
    assert calls.index(_cancels(calls)[0]) > close, "cancelled only after the journal closed"
    assert "final=blue_serving run=none" in result.stdout
    start = next(call for call in calls if "quiesce_window start" in call)
    assert "--deadline-epoch" in start, "the run never starts inside the dead-man's guard"


def test_a_run_with_no_window_arms_and_cancels_nothing(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, SEQUENCE, REASONS="")
    assert result.returncode == 0, result.stderr
    assert not _has(calls, "quiesce_window open") and _cancels(calls) == []


def test_a_cancel_that_fails_never_fails_the_close(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, SEQUENCE, REASONS="WR3", CANCEL_RC="1")
    assert result.returncode == 0, result.stderr
    assert len(_cancels(calls)) == 1 and "could not be cancelled" in result.stderr
    assert "final=blue_serving run=4242" in result.stdout, "kept, so a later close could retry"


@pytest.mark.parametrize(
    ("journal", "start_id"),
    (("window_open", ""), ("window_starting", ""), ("window_started", ""), ("window_proven", "deployment-start-1")),
)
def test_every_exit_trap_path_that_closes_the_window_cancels_the_dead_man(
    tmp_path: Path, journal: str, start_id: str
) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=window_open; QUIESCE_WINDOW_DEADMAN_RUN_ID=7002\nstop_app_after_failed_deploy\n",
        JOURNAL=journal,
        START_ID=start_id,
    )
    assert result.returncode == 0, result.stderr
    assert _has(calls, "quiesce_window close") == 1
    assert _cancels(calls) == ["-m tools.databricks.quiesce_window cancel-deadman --app-name mip-app --run-id 7002"]


@pytest.mark.parametrize(
    "env",
    (
        {"JOURNAL": "window_open", "RESTORABLE": "0"},
        {"JOURNAL": "window_refused"},
        {"JOURNAL": "window_open", "FAIL_AWAIT": "1"},
        {"JOURNAL": "window_open", "FAIL_START": "1"},
    ),
    ids=("contract", "refused", "unproven", "start-refused-near-the-deadline"),
)
def test_an_exit_trap_path_that_leaves_the_window_open_keeps_the_dead_man_armed(
    tmp_path: Path, env: dict[str, str]
) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=window_open; QUIESCE_WINDOW_DEADMAN_RUN_ID=7003\nstop_app_after_failed_deploy\n",
        **env,
    )
    assert result.returncode == 1
    assert _cancels(calls) == [], "the dead-man is the App's only restart now"


def test_an_open_that_armed_and_then_failed_before_its_journal_cancels_that_run(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS=WR3\n"
        "ensure_quiesce_window_open_for WR3 || true\nstop_app_after_failed_deploy\n",
        FAIL_OPEN_AFTER_ARM="1",
        JOURNAL="absent",
        DEADMAN_RUN_ID="7004",
    )
    assert result.returncode == 0, result.stderr
    assert _cancels(calls) == ["-m tools.databricks.quiesce_window cancel-deadman --app-name mip-app --run-id 7004"]
    assert not _has(calls, "quiesce_window start")


def test_a_contract_window_cancels_only_once_green_is_captured(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        SEQUENCE
        + "end_quiesce_window_at_activation\n"
        + 'echo "cancels-at-activation=$(grep -c cancel-deadman "$CALLS" || true)"\n'
        + "close_contract_window_after_capture\n",
        REASONS="WR3",
        RESTORABLE="0",
        DEADMAN_RUN_ID="7005",
    )
    assert result.returncode == 0, result.stderr
    assert "cancels-at-activation=0" in result.stdout
    assert _cancels(calls) == ["-m tools.databricks.quiesce_window cancel-deadman --app-name mip-app --run-id 7005"]


def test_a_failure_inside_an_activating_contract_window_keeps_the_dead_man(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=contract_activating; QUIESCE_WINDOW_DEADMAN_RUN_ID=7006\n"
        "APP_UPGRADE_STATE=green_activating_quiesced\nstop_app_after_failed_deploy\n",
    )
    assert result.returncode == 1 and _cancels(calls) == []


# --- the next run: repin --from-deadman before step 0a's proof ----------------------


@pytest.mark.parametrize("journal", ("window_open", "window_starting"))
def test_the_next_run_re_pins_a_dead_man_restart_under_its_own_lease(tmp_path: Path, journal: str) -> None:
    result, calls = _run(
        tmp_path,
        "recover_deadman_quiesce_window\necho recovered\n",
        JOURNAL=journal,
        DEADMAN_STATE="deadman_restarted",
    )
    assert result.returncode == 0, result.stderr
    repin = [call for call in calls if "app_deployment_rollback repin" in call]
    assert len(repin) == 1 and "repin --from-deadman" in repin[0]
    for flag in ("--deployment-lease-id lease", "--deployment-source-git-sha sha", "--token-env MIP_BEARER_TOKEN"):
        assert flag in repin[0], flag
    assert "--to " not in repin[0] and "--expected-rollback-deployment-id" not in repin[0]
    assert "--app-resource-payload" in repin[0], "this run's bundle payload, if the live bindings are it"
    assert not any(_has(calls, stop) for stop in ("quiesce_window start", "stop_app_fail_closed", "apps stop"))


@pytest.mark.parametrize(
    ("journal", "marker"),
    (
        ("absent", "absent"),
        ("closed", "deadman_restarted"),
        ("window_open", "absent"),
        ("window_open", "deadman_left_stopped"),
        ("window_open", "deadman_start_failed"),
        ("window_open", "mismatch"),
        ("window_started", "deadman_restarted"),
        ("window_proven", "deadman_restarted"),
        ("window_refused", "deadman_restarted"),
    ),
)
def test_anything_but_an_open_window_the_dead_man_restarted_is_left_alone(
    tmp_path: Path, journal: str, marker: str
) -> None:
    result, calls = _run(
        tmp_path, "recover_deadman_quiesce_window\necho recovered\n", JOURNAL=journal, DEADMAN_STATE=marker
    )
    assert result.returncode == 0 and "recovered" in result.stdout
    assert not _has(calls, "app_deployment_rollback repin")


def test_a_restart_the_re_pin_cannot_prove_exits_before_any_step(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "recover_deadman_quiesce_window\necho recovered\n",
        JOURNAL="window_open",
        DEADMAN_STATE="deadman_restarted",
        REPIN_RC="1",
    )
    assert result.returncode == 2 and "recovered" not in result.stdout
    assert "not provably the window's blue" in result.stderr


def test_step_0a_recovers_before_it_proves_signed_blue() -> None:
    text = deploy_entrypoint_text()
    recover = text.index("\n      recover_deadman_quiesce_window\n")
    assert text.index("mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET", recover - 400) < recover
    assert recover < text.index('prove_or_restore_signed_blue "$APP_ROLLBACK_BINDING_ENV"', recover)
    assert text.index('step "acquire signed workspace lease for the exact App deployment"') < recover
    # Review: before the fail-closed arm and the treatment quiesce, or a
    # refused recovery stops the App the dead-man restarted, and every later
    # run refuses it for good ("the App compute is not ACTIVE").
    assert recover < text.index("APP_FAIL_CLOSED_ARMED=1", recover)
    branch = text.rindex("\n    else\n", 0, recover)
    assert "APP_FAIL_CLOSED_ARMED=1" not in text[branch:recover]
    assert "converge_campaign_treatment_access" not in text[branch:recover]


def _step_0a_signed_blue_branch() -> str:
    """Step 0a's signed-blue branch, from its ``else`` to the signed-blue proof, verbatim."""

    text = (REPO / "scripts" / "lib" / "deploy_step_signed_blue_proof.sh").read_text(encoding="utf-8")
    recover = text.index("recover_deadman_quiesce_window\n")
    start = text.rindex("\n    else\n", 0, recover) + len("\n    else\n")
    return text[start : text.index('step "prove or reconcile the signed last-good App', recover)]


@pytest.mark.parametrize("repin_rc", ("1", "2"))
def test_a_refused_recovery_leaves_the_app_as_the_dead_man_left_it(tmp_path: Path, repin_rc: str) -> None:
    # The real step-0a branch under the real EXIT trap: the App state of a run
    # whose recovery refuses (a transient health failure, say) is untouched.
    result, calls = _run(
        tmp_path,
        "APP_FAIL_CLOSED_ARMED=0; APP_UPGRADE_STATE=unverified_existing; LAKEBASE_RUNTIME_ACCESS_PROVEN=0\n"
        "TREATMENT_RUNTIME_QUIESCED=0\n"
        "trap stop_app_after_failed_deploy EXIT\n" + _step_0a_signed_blue_branch() + "echo past-step-0a\n",
        JOURNAL="window_open",
        DEADMAN_STATE="deadman_restarted",
        REPIN_RC=repin_rc,
    )
    assert result.returncode == 2 and "past-step-0a" not in result.stdout
    assert _has(calls, "repin --from-deadman") == 1
    assert not any(_has(calls, stop) for stop in ("stop_app_fail_closed", "apps stop", "legacy-stop", "legacy-restore"))
    assert not _has(calls, "converge_campaign_treatment_access"), "nothing ran after the refusal"


# --- the run's own deadline: the window watchdog -----------------------------------


def _watchdog_env(deadline_in: int) -> dict[str, str]:
    import time

    from tools.databricks import quiesce_window_watchdog as watchdog
    from tools.databricks.quiesce_window_budget import DEADMAN_GUARD_S

    deadline = int(time.time()) + DEADMAN_GUARD_S + watchdog.WATCHDOG_LEAD_S + deadline_in
    return {"REAL_WATCHDOG": "1", "PYTHONPATH": str(REPO), "WATCHDOG_DEADLINE": str(deadline)}


WATCHED_WINDOW = (
    "trap quiesce_window_deadline_reached USR1\n"
    "trap 'rc=$?; echo \"exit-trap rc=$rc\"; disarm_quiesce_window_watchdog; "
    "if [[ $rc -ne 0 ]]; then stop_app_after_failed_deploy; fi' EXIT\n"
    "QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS=WR3; MIP_JOB_DEADLINE_EPOCH=$WATCHDOG_DEADLINE\n"
    "ensure_quiesce_window_open_for WR3\n"
    'echo "watchdog=$QUIESCE_WINDOW_WATCHDOG_PID"\n'
)


def test_a_watchdog_ends_an_overrunning_in_window_mutation_and_the_exit_trap_restores_blue(
    tmp_path: Path,
) -> None:
    # Review: a local run past its --deadline kept mutating while the dead-man
    # started blue under it. Here the in-window mutation overruns (sleep 45):
    # the watchdog ends it a minute before the start guard, and the EXIT trap,
    # still outside the guard, starts and re-pins blue itself.
    import time

    started = time.monotonic()
    result, calls = _run(
        tmp_path,
        WATCHED_WINDOW + "sleep 45 || true\necho unreachable\n",
        **_watchdog_env(2),
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 143, result.stdout + result.stderr
    assert "unreachable" not in result.stdout and elapsed < 30, "the mutation was ended, not waited for"
    assert "exit-trap rc=143" in result.stdout and "start guard of the job deadline" in result.stderr
    assert _has(calls, "quiesce_window_watchdog --check") == 1 and _has(calls, "quiesce_window_watchdog --run-pid") == 1
    for step in ("quiesce_window start", "quiesce_window await", "app_deployment_rollback repin", "quiesce_window close"):
        assert _has(calls, step) == 1, step


def test_the_close_disarms_the_watchdog(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        WATCHED_WINDOW
        + 'pid="$QUIESCE_WINDOW_WATCHDOG_PID"; kill -0 "$pid"\n'
        + "close_quiesce_window\n"
        + 'if kill -0 "$pid" 2>/dev/null; then echo watchdog-alive; else echo watchdog-gone; fi\n'
        + 'echo "after=${QUIESCE_WINDOW_WATCHDOG_PID:-}"\n',
        **_watchdog_env(3 * 3600),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "watchdog-gone" in result.stdout and "after=\n" in result.stdout
    assert _has(calls, "quiesce_window close") == 1


def test_a_contract_window_arms_no_watchdog(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, WATCHED_WINDOW, RESTORABLE="0", **_watchdog_env(3 * 3600))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "watchdog=\n" in result.stdout and not _has(calls, "quiesce_window_watchdog")


def test_deploy_traps_the_watchdog_and_the_exit_trap_disarms_it_first() -> None:
    text = deploy_entrypoint_text()
    assert text.index("deploy_quiesce_window_lifecycle.sh\"\n") < text.index("trap quiesce_window_deadline_reached USR1\n")
    trap = text[text.index("restore_rendered_sql_fail_closed() {") :]
    assert trap.index("local rc=$?") < trap.index("disarm_quiesce_window_watchdog") < trap.index("stop_app_after_failed_deploy")
