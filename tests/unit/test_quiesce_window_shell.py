"""The window library in the shell: EXIT-trap matrix, TOCTOU and positive controls (M0-PR8).

The harness sources the real window library and the real EXIT-trap
compensation (``deploy_app_release_lifecycle.sh``) with a fake ``$PYTHON``
that records every tool call and answers the window tools' env files. The
matrix drives ``stop_app_after_failed_deploy`` through every window state;
the controls drive the step files' open/close sequence. Ordering is checked
against the deploy text itself.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib"
FAKE_PYTHON = r"""#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then exec "$REAL_PYTHON" "$@"; fi
printf '%s\n' "$*" >> "$CALLS"
out=""; prev=""
for arg in "$@"; do
  if [[ "$prev" == "--out-env" || "$prev" == "--out" ]]; then out="$arg"; fi
  prev="$arg"
done
case "$*" in
  *"quiesce_window open"*)
    [[ -z "${FAIL_OPEN:-}" ]] || exit 2
    printf 'MIP_QUIESCE_WINDOW_SHA256=w\nMIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID=deployment-blue\nMIP_QUIESCE_WINDOW_TREE_SHA256=t\nMIP_QUIESCE_WINDOW_BLUE_RESTORABLE=%s\n' "${RESTORABLE:-1}" > "$out" ;;
  *"quiesce_window start"*)
    printf 'MIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID=deployment-start-1\n' > "$out" ;;
  *"quiesce_window await"*)
    [[ -z "${FAIL_AWAIT:-}" ]] || exit 1
    printf 'MIP_QUIESCE_WINDOW_PROVEN_DEPLOYMENT_ID=deployment-start-1\n' > "$out" ;;
  *"quiesce_window status"*)
    printf 'MIP_QUIESCE_WINDOW_JOURNAL_STATE=%s\nMIP_QUIESCE_WINDOW_BLUE_RESTORABLE=%s\nMIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID=%s\nMIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID=deployment-blue\nMIP_QUIESCE_WINDOW_TREE_SHA256=t\n' "${JOURNAL:-window_open}" "${RESTORABLE:-1}" "${START_ID:-}" > "$out" ;;
  *"quiesce_window assert-serving"*)
    [[ -z "${NOT_SERVING:-}" ]] || exit 1 ;;
  *"quiesce_window applicable"*)
    printf 'MIP_QUIESCE_WINDOW_APP_PRESENT=0\n' > "$out" ;;
  *"quiesce_window plan"*)
    [[ -z "${PLAN_REFUSES:-}" ]] || exit 2
    printf '{"code": "%s", "reasons": %s}\n' "${PLAN_CODE:-none}" "${PLAN_REASONS_JSON:-[]}" > "$out" ;;
  *"app_deployment_rollback repin"*)
    printf 'MIP_APP_ROLLBACK_DEPLOYMENT_ID=deployment-start-1\n' > "$out" ;;
esac
exit 0
"""
PRELUDE = """set -euo pipefail
RED=""; YLW=""; DIM=""; RST=""; BOLD=""; GRN=""
DRY_RUN=0; STEP=0
step() { echo "step: $*"; }
run() { "$@"; }
run_with_proof_signing_authority() { "$@"; }
run_with_account_identity() { "$@"; }
run_with_lakebase_bootstrap_authority() { "$@"; }
mint_m2m_token() { :; }
converge_app_treatment_access() { echo "treatment $1" >> "$CALLS"; }
converge_runtime_app_release_access() { echo "release-runtime" >> "$CALLS"; }
reconcile_reviewed_function_execute_grants() { echo "function-reconcile" >> "$CALLS"; }
_GRANTS_APP_NAME=mip-app; APP_NAME=mip-app; APP_FAIL_CLOSED_NAME=mip-app
_EXISTING_APP_ID=app-id; _EXISTING_APP_SP_CLIENT_ID=app-client; _EXISTING_APP_SP_SCIM_ID=app-scim
APP_SP_CLIENT_ID=app-client; APP_SP_SCIM_ID=app-scim
_GRANTS_WAREHOUSE_ID=warehouse; _GRANTS_CATALOG=mip
DEPLOYMENT_SYNC_CATALOG=sync; DEPLOYMENT_SYNC_SCHEMA=s; DEPLOYMENT_SYNC_TABLES=t
APP_ROLLBACK_SECRET_SCOPE=scope; MIP_LAKEBASE_INSTANCE=instance; LAKEBASE_DATABASE=db
MIP_APP_DEPLOYMENT_LEASE_ID=lease; SOURCE_GIT_SHA=sha; MIP_APP_URL=https://app.example
APP_FAIL_CLOSED_ARMED=1; FIRST_INSTALL_APP_CREATED=0; APP_EXPECTED_IDENTITY_ARGS=()
TREATMENT_RUNTIME_QUIESCED=1; REVIEWED_FUNCTION_GRANTS_PROVEN=1; LAKEBASE_RUNTIME_ACCESS_PROVEN=1
QUIESCE_WINDOW_STATE=""; QUIESCE_WINDOW_CODE=""; QUIESCE_WINDOW_REASONS=""
QUIESCE_WINDOW_PLAN_JSON="$TMPDIR/plan.json"; QUIESCE_WINDOW_BLUE_RESTORABLE=1
QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID=deployment-blue; QUIESCE_WINDOW_TREE_SHA256=t
APP_ROLLBACK_BINDING_ENV="$TMPDIR/binding.env"; APP_UPGRADE_STATE=blue_quiesced
"""


def _run(tmp_path: Path, body: str, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake = tmp_path / "fake-python"
    fake.write_text(FAKE_PYTHON, encoding="utf-8")
    fake.chmod(0o755)
    calls = tmp_path / "calls.log"
    calls.write_text("", encoding="utf-8")
    script = tmp_path / "harness.sh"
    script.write_text(
        PRELUDE
        + f"PYTHON={shlex.quote(str(fake))}\n"
        + f". {shlex.quote(str(LIB / 'deploy_quiesce_window_lifecycle.sh'))}\n"
        + f". {shlex.quote(str(LIB / 'deploy_app_release_lifecycle.sh'))}\n"
        + 'restore_signed_blue_while_quiesced() { echo "legacy-restore" >> "$CALLS"; }\n'
        + 'stop_and_quiesce_unproven_app() { echo "legacy-stop" >> "$CALLS"; }\n'
        + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        env={
            **{k: v for k, v in os.environ.items() if k in {"PATH", "HOME"}},
            "TMPDIR": str(tmp_path),
            "CALLS": str(calls),
            "REAL_PYTHON": sys.executable,
            **env,
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    return result, [line for line in calls.read_text(encoding="utf-8").splitlines() if line]


def _has(calls: list[str], needle: str) -> int:
    return sum(needle in call for call in calls)


STOPS = ("stop_app_fail_closed", "apps stop", "legacy-stop", "legacy-restore", "rollback restore")


# --- the EXIT-trap matrix ---------------------------------------------------------


@pytest.mark.parametrize("upgrade", ("blue_quiesced", "blue_quiescing"))
def test_blue_serving_never_stops_and_converges_runtime_access(tmp_path: Path, upgrade: str) -> None:
    result, calls = _run(
        tmp_path,
        f"QUIESCE_WINDOW_STATE=blue_serving; APP_UPGRADE_STATE={upgrade}\nstop_app_after_failed_deploy\n",
    )
    assert result.returncode == 0, result.stderr
    assert _has(calls, "assert-serving") == 1 and "release-runtime" in calls and "treatment runtime" in calls
    assert not any(_has(calls, stop) for stop in STOPS)
    assert not _has(calls, "quiesce_window start") and "function-reconcile" not in calls


def test_blue_serving_that_is_not_serving_the_record_keeps_treatment_quiesced(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=blue_serving\nstop_app_after_failed_deploy\n",
        NOT_SERVING="1",
    )
    assert result.returncode == 1
    assert "treatment runtime" not in calls and not any(_has(calls, stop) for stop in STOPS)


def test_blue_degraded_converges_the_mutated_dependencies_with_the_app_up(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=blue_degraded; REVIEWED_FUNCTION_GRANTS_PROVEN=0\n"
        "stop_app_after_failed_deploy\n",
    )
    assert result.returncode == 0, result.stderr
    assert "function-reconcile" in calls
    assert _has(calls, "converge_app_lakebase_sync_access --mode runtime") == 1
    assert "treatment runtime" in calls and not any(_has(calls, stop) for stop in STOPS)


def test_an_open_restorable_window_starts_proves_and_re_pins_blue(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, "QUIESCE_WINDOW_STATE=window_open\nstop_app_after_failed_deploy\n", JOURNAL="window_open"
    )
    assert result.returncode == 0, result.stderr
    order = [
        next(i for i, call in enumerate(calls) if needle in call)
        for needle in (
            "quiesce_window status",
            "quiesce_window start",
            "quiesce_window await",
            "app_deployment_rollback repin",
            "quiesce_window close",
            "assert-serving",
        )
    ]
    assert order == sorted(order)
    assert "--to deployment-start-1" in next(c for c in calls if "rollback repin" in c)
    assert "--expected-rollback-deployment-id deployment-blue" in next(c for c in calls if "repin" in c)
    assert "treatment runtime" in calls and not any(_has(calls, stop) for stop in STOPS)


def test_a_started_window_resumes_at_the_proof_without_a_second_start(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, "QUIESCE_WINDOW_STATE=window_open\nstop_app_after_failed_deploy\n", JOURNAL="window_started"
    )
    assert result.returncode == 0, result.stderr
    assert not _has(calls, "quiesce_window start") and _has(calls, "rollback repin") == 1


def test_a_proven_window_resumes_at_the_re_pin(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=window_open\nstop_app_after_failed_deploy\n",
        JOURNAL="window_proven",
        START_ID="deployment-start-1",
    )
    assert result.returncode == 0, result.stderr
    assert not _has(calls, "quiesce_window start") and not _has(calls, "quiesce_window await")
    assert "--to deployment-start-1" in next(c for c in calls if "rollback repin" in c)


@pytest.mark.parametrize(
    ("env", "why"),
    (
        ({"JOURNAL": "window_open", "RESTORABLE": "0"}, "contract migration stays stopped"),
        ({"JOURNAL": "window_refused"}, "a refused proof is the dead-man's"),
        ({"JOURNAL": "window_open", "FAIL_AWAIT": "1"}, "an unproven start is never re-pinned"),
    ),
)
def test_an_unprovable_window_stays_open_for_the_dead_man(
    tmp_path: Path, env: dict[str, str], why: str
) -> None:
    result, calls = _run(tmp_path, "QUIESCE_WINDOW_STATE=window_open\nstop_app_after_failed_deploy\n", **env)
    assert result.returncode == 1, why
    assert not _has(calls, "rollback repin") and not _has(calls, "quiesce_window close"), why
    assert "treatment runtime" not in calls, why
    assert not any(_has(calls, stop) for stop in STOPS[1:]), why


def test_an_open_that_refused_before_its_journal_compensates_as_blue_serving(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS=WR3\n"
        "ensure_quiesce_window_open_for WR3 || true\n"
        'echo "state=$QUIESCE_WINDOW_STATE"\n'
        "stop_app_after_failed_deploy\n",
        FAIL_OPEN="1",
        JOURNAL="absent",
    )
    assert result.returncode == 0, result.stderr
    assert "state=window_open" in result.stdout, "the state switches before the open runs"
    assert not _has(calls, "quiesce_window start") and "treatment runtime" in calls


@pytest.mark.parametrize("window", ("", "blue_serving"))
def test_states_outside_the_window_keep_the_legacy_compensation(tmp_path: Path, window: str) -> None:
    upgrade = "blue_quiesced" if not window else "green_activating_quiesced"
    result, calls = _run(
        tmp_path,
        f"QUIESCE_WINDOW_STATE={window}; APP_UPGRADE_STATE={upgrade}; APP_SIGNED_BLUE_AVAILABLE=1\n"
        "stop_app_after_failed_deploy\n",
    )
    assert result.returncode == 0, result.stderr
    assert "legacy-restore" in calls and not _has(calls, "assert-serving")


# --- TOCTOU --------------------------------------------------------------------------


def test_a_refused_pre_apply_plan_exits_2_before_any_open(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'plan_quiesce_window pre-apply "$TMPDIR/report.json"\nensure_quiesce_window_open_for WR1\n',
        PLAN_REFUSES="1",
    )
    assert result.returncode == 2
    assert "Nothing was changed" in result.stderr
    assert not _has(calls, "quiesce_window open")


def test_the_plan_sets_the_reasons_the_window_opens_for(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'plan_quiesce_window pre-apply "$TMPDIR/report.json"\n'
        'echo "code=$QUIESCE_WINDOW_CODE reasons=$QUIESCE_WINDOW_REASONS"\n',
        PLAN_CODE="window",
        PLAN_REASONS_JSON='["WR1", "WR3"]',
    )
    assert result.returncode == 0, result.stderr
    assert "code=window reasons=WR1,WR3" in result.stdout
    plan = next(call for call in calls if "quiesce_window plan" in call)
    assert "--position pre-apply" in plan and "--plan-gate-report" in plan


# --- positive controls --------------------------------------------------------------

SEQUENCE = """QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS="$REASONS"
ensure_quiesce_window_open_for WR4
ensure_quiesce_window_open_for WR1
ensure_quiesce_window_open_for WR2
ensure_quiesce_window_open_for WR3
close_quiesce_window
echo "final=$QUIESCE_WINDOW_STATE"
"""


@pytest.mark.parametrize(
    ("reasons", "opens", "starts", "repins", "final"),
    (
        ("", 0, 0, 0, "blue_serving"),
        ("WR3", 1, 1, 1, "blue_serving"),
        ("WR1,WR2,WR3,WR4", 1, 1, 1, "blue_serving"),
    ),
    ids=("no-window", "wr3", "every-reason-one-window"),
)
def test_the_release_sequence_stops_once_per_window_and_re_pins_once(
    tmp_path: Path, reasons: str, opens: int, starts: int, repins: int, final: str
) -> None:
    result, calls = _run(tmp_path, SEQUENCE, REASONS=reasons)
    assert result.returncode == 0, result.stderr
    assert (
        _has(calls, "quiesce_window open"),
        _has(calls, "quiesce_window start"),
        _has(calls, "rollback repin"),
    ) == (opens, starts, repins)
    assert f"final={final}" in result.stdout


def test_a_contract_window_stays_open_until_green_activation_closes_it(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, SEQUENCE + "end_quiesce_window_at_activation\necho \"after=${QUIESCE_WINDOW_STATE:-none}\"\n",
        REASONS="WR3",
        RESTORABLE="0",
    )
    assert result.returncode == 0, result.stderr
    assert "final=window_open" in result.stdout and "after=none" in result.stdout
    assert not _has(calls, "quiesce_window start") and not _has(calls, "rollback repin")
    assert _has(calls, "quiesce_window close") == 1
    assert "--superseded-by-activation" in next(call for call in calls if "quiesce_window close" in call)


def test_the_step_files_open_each_reason_before_its_mutation_and_close_after_the_migration() -> None:
    text = deploy_entrypoint_text()
    steps = text.index('step "preflight — check .env.local, databricks CLI, venv"')

    def at(needle: str, start: int = steps) -> int:
        return text.index(needle, start)

    wr4 = at("ensure_quiesce_window_open_for WR4")
    apply = at('step "deploy non-App bundle resources without activating an App candidate"')
    wr1 = at("ensure_quiesce_window_open_for WR1")
    update = at('run databricks apps update "$_GRANTS_APP_NAME"')
    wr2 = at("ensure_quiesce_window_open_for WR2")
    role = at('step "converge App Lakebase OAuth role to exact LOGIN-only profile"')
    wr3 = at("ensure_quiesce_window_open_for WR3")
    migrate = at("databricks bundle run mip_lakebase_migrate")
    proven = at("LAKEBASE_RUNTIME_ACCESS_PROVEN=1", migrate)
    close = at("\nclose_quiesce_window\n", migrate)
    degrade = at("mark_blue_degraded", close)
    activation = at("end_quiesce_window_at_activation")
    green = at('deploy_app_snapshot "activate App snapshot', activation)

    assert wr4 < apply < wr1 < update < wr2 < role < wr3 < migrate < proven < close < degrade
    assert activation < green
    assert "--stop-app-for-mutation" not in text[steps:], "the converger never stops the App"
