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
    # M0-PR9: the armed run id reaches the env file before the journal write.
    printf 'MIP_QUIESCE_WINDOW_DEADMAN_RUN_ID=%s\n' "${DEADMAN_RUN_ID:-4242}" > "$out"
    [[ -z "${FAIL_OPEN_AFTER_ARM:-}" ]] || exit 1
    printf 'MIP_QUIESCE_WINDOW_DEADMAN_RUN_ID=%s\nMIP_QUIESCE_WINDOW_SHA256=w\nMIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID=deployment-blue\nMIP_QUIESCE_WINDOW_TREE_SHA256=t\nMIP_QUIESCE_WINDOW_BLUE_RESTORABLE=%s\n' "${DEADMAN_RUN_ID:-4242}" "${RESTORABLE:-1}" > "$out" ;;
  *"quiesce_window cancel-deadman"*)
    exit "${CANCEL_RC:-0}" ;;
  *"quiesce_window start"*)
    [[ -z "${FAIL_START:-}" ]] || exit 1
    printf 'MIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID=deployment-start-1\n' > "$out" ;;
  *"quiesce_window await"*)
    [[ -z "${FAIL_AWAIT:-}" ]] || exit 1
    printf 'MIP_QUIESCE_WINDOW_PROVEN_DEPLOYMENT_ID=deployment-start-1\n' > "$out" ;;
  *"quiesce_window status"*)
    printf 'MIP_QUIESCE_WINDOW_JOURNAL_STATE=%s\nMIP_QUIESCE_WINDOW_BLUE_RESTORABLE=%s\nMIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID=%s\nMIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID=deployment-blue\nMIP_QUIESCE_WINDOW_TREE_SHA256=t\nMIP_QUIESCE_WINDOW_DEADMAN_STATE=%s\n' "${JOURNAL:-window_open}" "${RESTORABLE:-1}" "${START_ID:-}" "${DEADMAN_STATE:-absent}" > "$out" ;;
  *"quiesce_window assert-serving"*)
    [[ -z "${NOT_SERVING:-}" ]] || exit 1 ;;
  *"quiesce_window applicable"*)
    printf 'MIP_QUIESCE_WINDOW_APP_PRESENT=%s\nMIP_QUIESCE_WINDOW_APP_CLIENT_ID=app-client\nMIP_QUIESCE_WINDOW_REBASE=%s\n' "${APP_PRESENT:-0}" "${JOURNAL_REBASE:-0}" > "$out" ;;
  *"converge_lakebase_oauth_role --check"*"--role-contract app"*)
    exit "${APP_ROLE_RC:-0}" ;;
  *"converge_lakebase_oauth_role --check"*"--role-contract verifier"*)
    exit "${VERIFIER_ROLE_RC:-0}" ;;
  *"converge_lakebase_oauth_role --check"*"--role-contract jobs_reader"*)
    exit "${JOBS_READER_ROLE_RC:-0}" ;;
  *"quiesce_window plan"*)
    [[ -z "${PLAN_REFUSES:-}" ]] || exit 2
    printf '{"code": "%s", "reasons": %s}\n' "${PLAN_CODE:-none}" "${PLAN_REASONS_JSON:-[]}" > "$out" ;;
  *"app_deployment_rollback repin"*|*"app_core_rollback repin"*)
    [[ -z "${REPIN_RC:-}" ]] || exit "$REPIN_RC"
    [[ -z "$out" ]] || printf 'MIP_APP_ROLLBACK_DEPLOYMENT_ID=deployment-start-1\n' > "$out" ;;
  *"catalog_bootstrap_skip_rule check"*)
    exit "${SKIP_RULE_RC:-0}" ;;
  *"quiesce_window_watchdog"*)
    # M0-PR9: the real watchdog when a test asks for it, else a no-op.
    [[ -z "${REAL_WATCHDOG:-}" ]] || exec "$REAL_PYTHON" "$@" ;;
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
# The profile's agentic tier (resolve_client_profile, M2-PR3): client #1's by default.
tier_is_agentic() { [[ "${CLIENT_AGENTIC_TIER:-on}" == "on" ]]; }
mint_m2m_token() { :; }
# The entrypoint's runner-mask helpers: nothing here, under GitHub Actions too, so the call
# log is the same in CI (tests/unit/test_deployment_id_runner_masks.py drives the real ones).
mask_runtime_ids() { :; }
mask_deployment_ids() { :; }
# The entrypoint keeps an apps CLI mutation's rendered result off the job log; here the
# command just runs, so the call log is the same (tests/unit/test_cli_result_private.py drives the real one).
keep_cli_result_private() { "$@"; }
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
TARGET=client; OTEL_RESOURCE_BINDING_ARGS=(); DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
MIP_SALESFORCE_BINDINGS=off
CLIENT_JOBS_PRINCIPAL=jobs-client
MIP_LENDER_NAME=lender; MIP_LENDER_NMLS_ID=1; MIP_TENANT_ID=tenant
APP_RELEASE_ID=aaaaaaaaaaaa-bbbbbbbbbbbb; APP_RELEASE_STAGE_ROOT=.databricks/app-release
APP_RELEASE_MANIFEST=.databricks/app-release/manifest.json; CLIENT_DEPLOYER=deployer; CLIENT_KEY=client-key
QUIESCE_WINDOW_DEADMAN_RUN_ID=""; QUIESCE_WINDOW_WATCHDOG_PID=""
run_job_with_retry() { echo "job: $*" >> "$CALLS"; }
# The identity library's reader (its strip rule is pinned in test_lakebase_seed_operator.py).
nonblank_control_value() { printf '%s' "${!1:-}"; }
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


# deploy.sh's agentic value (resolve_client_profile) and a tier not yet resolved (M2-PR7b review).
@pytest.mark.parametrize("tier", ("CLIENT_AGENTIC_TIER=on; ", ""), ids=("on", "unset"))
def test_blue_degraded_converges_the_mutated_dependencies_with_the_app_up(
    tmp_path: Path, tier: str
) -> None:
    result, calls = _run(
        tmp_path,
        f"{tier}QUIESCE_WINDOW_STATE=blue_degraded; REVIEWED_FUNCTION_GRANTS_PROVEN=0\n"
        "stop_app_after_failed_deploy\n",
    )
    assert result.returncode == 0, result.stderr
    assert "function-reconcile" in calls
    assert _has(calls, "converge_app_lakebase_sync_access --mode runtime") == 1
    assert "treatment runtime" in calls and not any(_has(calls, stop) for stop in STOPS)


def test_a_core_blue_degraded_keeps_its_synced_catalog_access_quiesced(tmp_path: Path) -> None:
    """The core tier never converges step 4c's sync quiesce back (M2-PR7b review)."""

    result, calls = _run(
        tmp_path,
        "CLIENT_AGENTIC_TIER=off; QUIESCE_WINDOW_STATE=blue_degraded; REVIEWED_FUNCTION_GRANTS_PROVEN=0\n"
        "stop_app_after_failed_deploy\n",
    )
    assert result.returncode == 0, result.stderr
    assert "function-reconcile" in calls and not _has(calls, "converge_app_lakebase_sync_access")
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


def test_a_starting_window_resumes_through_the_start_tool_once(tmp_path: Path) -> None:
    # window_starting: the start may have run unjournaled. The start tool
    # resumes from the App's pending deployment; the shell never starts twice.
    result, calls = _run(
        tmp_path, "QUIESCE_WINDOW_STATE=window_open\nstop_app_after_failed_deploy\n", JOURNAL="window_starting"
    )
    assert result.returncode == 0, result.stderr
    assert _has(calls, "quiesce_window start") == 1 and _has(calls, "rollback repin") == 1
    start = next(call for call in calls if "quiesce_window start" in call)
    assert "--expected-app-id app-id" in start and "--expected-scim-id app-scim" in start


@pytest.mark.parametrize(("reasons", "passed"), (("WR1,WR3", True), ("WR3", False)))
def test_the_re_pin_gets_the_applied_bindings_only_after_a_wr1_window(
    tmp_path: Path, reasons: str, passed: bool
) -> None:
    # Blue restarts on the bindings a WR1 window applied; the re-pin may record
    # exactly that payload, so the next step 0a has nothing to revert.
    result, calls = _run(
        tmp_path,
        f'QUIESCE_WINDOW_STATE=window_open; QUIESCE_WINDOW_REASONS="{reasons}"\n'
        'APP_RESOURCE_BINDING_PAYLOAD="$TMPDIR/bindings.json"; echo "{}" > "$APP_RESOURCE_BINDING_PAYLOAD"\n'
        "close_quiesce_window\n",
    )
    assert result.returncode == 0, result.stderr
    repin = next(call for call in calls if "rollback repin" in call)
    assert ("--app-resource-payload" in repin) is passed
    if passed:
        assert repin.endswith(f"--app-resource-payload {tmp_path}/bindings.json")


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


# --- the catalog bootstrap skip rule's mapping ----------------------------------------


@pytest.mark.parametrize(("rule_rc", "mapped"), (("0", 0), ("1", 3), ("2", 3), ("3", 3), ("4", 3)))
def test_the_skip_rule_skips_only_on_its_exact_exit(tmp_path: Path, rule_rc: str, mapped: int) -> None:
    # A crashed rule (a traceback exits 1) or any other outcome runs the
    # bootstrap; only the rule's own "equal" exit skips it.
    result, calls = _run(
        tmp_path,
        'rc=0; check_catalog_bootstrap_skip_rule "$TMPDIR/skip.json" || rc=$?\necho "mapped=$rc"\n',
        SKIP_RULE_RC=rule_rc,
    )
    assert result.returncode == 0, result.stderr
    assert f"mapped={mapped}" in result.stdout
    assert _has(calls, "catalog_bootstrap_skip_rule check --warehouse-id warehouse") == 1


def test_the_skip_rule_without_a_warehouse_runs_without_reading(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        '_GRANTS_WAREHOUSE_ID=""; DATABRICKS_WAREHOUSE_ID=""; dotenv_value() { :; }\n'
        'rc=0; check_catalog_bootstrap_skip_rule "$TMPDIR/skip.json" || rc=$?\necho "mapped=$rc"\n',
    )
    assert result.returncode == 0, result.stderr
    assert "mapped=3" in result.stdout and not _has(calls, "catalog_bootstrap_skip_rule")


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


def _migration_block() -> str:
    """The step file's real migration lines: open for WR3, run or skip the job, close."""

    text = (LIB / "deploy_step_lakebase_and_uc_grants.sh").read_text(encoding="utf-8")
    start = text.index("ensure_quiesce_window_open_for WR3\n")
    end = text.index("\nclose_quiesce_window\n", start) + len("\nclose_quiesce_window\n")
    return text[start:end]


@pytest.mark.parametrize("journal_rebase", ("0", "1"), ids=("signed-journal", "retired-journal"))
def test_a_governed_run_with_the_rebase_flag_still_plans_signed_blue(
    tmp_path: Path, journal_rebase: str
) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        MIP_REBASE_UNVERIFIED_APP="1",
        APP_PRESENT="1",
        JOURNAL_REBASE=journal_rebase,
    )
    assert result.returncode == 0, result.stderr
    plan = next(call for call in calls if "quiesce_window plan" in call)
    assert "--rebase-unverified-app 0" in plan, "step 0a governs signed blue: never a rebase"
    assert _has(calls, "--role-contract app") == 1 and _has(calls, "lakebase_migrate.py --mode plan") == 1
    applicable = next(call for call in calls if "quiesce_window applicable" in call)
    assert "--rebase-unverified-app 1" in applicable, "the tool resolves the request itself"


@pytest.mark.parametrize(("journal_rebase", "passed"), (("0", "0"), ("1", "1")))
def test_the_pre_lease_plan_rebases_only_when_the_journal_is_not_signed(
    tmp_path: Path, journal_rebase: str, passed: str
) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'plan_quiesce_window pre-lease "$TMPDIR/report.json"\n',
        MIP_REBASE_UNVERIFIED_APP="1",
        APP_PRESENT="1",
        JOURNAL_REBASE=journal_rebase,
    )
    assert result.returncode == 0, result.stderr
    assert f"--rebase-unverified-app {passed}" in next(c for c in calls if "quiesce_window plan" in c)
    assert _has(calls, "--role-contract app") == (1 if passed == "0" else 0)


@pytest.mark.parametrize("code", ("not_applicable", "refused", "unreviewed"))
def test_a_governed_run_whose_plan_is_neither_none_nor_window_never_runs_the_migration(
    tmp_path: Path, code: str
) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n'
        + _migration_block()
        + 'echo "final=$QUIESCE_WINDOW_STATE"\n',
        APP_PRESENT="1",
        PLAN_CODE=code,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "refusing before any mutation" in result.stderr
    assert not _has(calls, "mip_lakebase_migrate") and "final=" not in result.stdout


@pytest.mark.parametrize(
    ("code", "reasons", "job"),
    (("none", "[]", 0), ("window", '["WR3"]', 1)),
)
def test_the_migration_block_skips_only_a_none_plan_and_runs_inside_a_window(
    tmp_path: Path, code: str, reasons: str, job: int
) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n'
        + _migration_block()
        + 'echo "final=$QUIESCE_WINDOW_STATE"\n',
        APP_PRESENT="1",
        PLAN_CODE=code,
        PLAN_REASONS_JSON=reasons,
    )
    assert result.returncode == 0, result.stderr
    assert _has(calls, "bundle run mip_lakebase_migrate") == job
    assert _has(calls, "quiesce_window open") == job and "final=blue_serving" in result.stdout


@pytest.mark.parametrize(("rc", "check"), (("0", "exact"), ("3", "drift")))
def test_the_plan_reads_the_verifier_role_as_a_wr2_fact(tmp_path: Path, rc: str, check: str) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
        VERIFIER_ROLE_RC=rc,
    )
    assert result.returncode == 0, result.stderr
    verifier = next(call for call in calls if "--role-contract verifier" in call)
    assert "--check" in verifier and "--application-id verifier-client" in verifier
    assert f"--verifier-role-check {check}" in next(c for c in calls if "quiesce_window plan" in c)


def test_an_undecided_verifier_role_check_refuses_the_plan(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
        VERIFIER_ROLE_RC="1",
    )
    assert result.returncode == 2 and "verifier role check could not decide" in result.stderr
    assert not _has(calls, "quiesce_window plan")


@pytest.mark.parametrize(("rc", "check"), (("0", "exact"), ("3", "drift")))
def test_the_plan_reads_the_jobs_reader_role_and_grants_as_wr2_facts(
    tmp_path: Path, rc: str, check: str
) -> None:
    # M2-PR5 x M0-PR8: the migration job is the jobs reader's only grantor.
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
        JOBS_READER_ROLE_RC=rc,
    )
    assert result.returncode == 0, result.stderr
    role = next(call for call in calls if "--role-contract jobs_reader" in call)
    assert "--check" in role and "--application-id jobs-client" in role
    migration = next(call for call in calls if "lakebase_migrate.py --mode plan" in call)
    assert "--jobs-reader-client-id jobs-client" in migration, "the plan reads its grants"
    assert f"--jobs-reader-role-check {check}" in next(c for c in calls if "quiesce_window plan" in c)


def test_the_agentic_migration_plan_requires_the_verifier(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
    )
    assert result.returncode == 0, result.stderr
    migration = next(call for call in calls if "lakebase_migrate.py --mode plan" in call)
    assert "--ai-gateway-verifier-client-id verifier-client --require-ai-gateway-verifier" in migration
    assert "--ai-gateway-verifier-absent" not in migration


def test_the_core_tier_plans_no_verifier_role_and_asserts_its_absence(tmp_path: Path) -> None:
    # M2-PR3 x M0-PR8: the core tier deploys no verifier, so the window plan
    # checks no verifier role (it is recorded absent) and the migration plan
    # binds none. The App and the jobs-reader checks are unchanged.
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nDATABRICKS_VERIFIER_CLIENT_ID=""\n'
        'plan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
        CLIENT_AGENTIC_TIER="off",
    )
    assert result.returncode == 0, result.stderr
    assert not _has(calls, "--role-contract verifier")
    assert _has(calls, "--role-contract app") == 1 and _has(calls, "--role-contract jobs_reader") == 1
    plan = next(call for call in calls if "quiesce_window plan" in call)
    assert "--verifier-role-check absent" in plan
    migration = next(call for call in calls if "lakebase_migrate.py --mode plan" in call)
    assert "--ai-gateway-verifier-absent" in migration
    assert "--require-ai-gateway-verifier" not in migration and "--ai-gateway-verifier-client-id" not in migration


def test_the_migration_plan_renders_the_resolved_seed_operator(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
        MIP_SEED_OPERATOR_EMAIL="demo.operator@summit.example",
    )
    assert result.returncode == 0, result.stderr
    migration = next(call for call in calls if "lakebase_migrate.py --mode plan" in call)
    assert "--seed-operator-email demo.operator@summit.example" in migration


def test_an_undecidable_seed_operator_refuses_before_the_migration_plan(tmp_path: Path) -> None:
    # M1-PR4 review: the reader refuses (2) when it cannot decide blank; a
    # substitution inside the command's arguments would swallow that and plan
    # the synthetic operator.
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        "nonblank_control_value() { return 2; }\n"
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
    )
    assert result.returncode == 2 and "MIP_SEED_OPERATOR_EMAIL could not be resolved" in result.stderr
    assert not _has(calls, "lakebase_migrate.py --mode plan") and not _has(calls, "quiesce_window plan")


def test_an_undecided_jobs_reader_role_check_refuses_the_plan(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving\nplan_quiesce_window pre-apply "$TMPDIR/report.json"\n',
        APP_PRESENT="1",
        JOBS_READER_ROLE_RC="1",
    )
    assert result.returncode == 2 and "jobs-reader role check could not decide" in result.stderr
    assert not _has(calls, "quiesce_window plan")


OUTSIDE_A_WINDOW = (
    ("blue_serving", "0", 0, 1),
    ("blue_degraded", "3", 2, 1),
    ("blue_serving", "3", 2, 1),
    ("blue_serving", "1", 2, 1),
    ("window_open", "3", 0, 0),
    ("", "3", 0, 0),
)


@pytest.mark.parametrize(("state", "rc", "exit_code", "checks"), OUTSIDE_A_WINDOW)
def test_outside_a_window_the_jobs_reader_role_must_already_be_exact(
    tmp_path: Path, state: str, rc: str, exit_code: int, checks: int
) -> None:
    result, calls = _run(
        tmp_path,
        f"QUIESCE_WINDOW_STATE={state}\nrequire_exact_jobs_reader_role_outside_window\n"
        'echo "converge-jobs-reader" >> "$CALLS"\n',
        JOBS_READER_ROLE_RC=rc,
    )
    assert result.returncode == exit_code, result.stderr
    assert _has(calls, "--role-contract jobs_reader") == checks
    assert _has(calls, "--application-id jobs-client") == checks
    assert ("converge-jobs-reader" in calls) is (exit_code == 0), "drift refuses before the converge"


@pytest.mark.parametrize("state", ("", "window_open"))
def test_an_ungoverned_or_dry_run_needs_no_role_client_ids_for_the_re_checks(
    tmp_path: Path, state: str
) -> None:
    # A dry run and a first install carry no verifier or jobs-reader client id
    # at these lines; under set -u the re-checks must return before reading them.
    result, calls = _run(
        tmp_path,
        f"unset DATABRICKS_VERIFIER_CLIENT_ID CLIENT_JOBS_PRINCIPAL\nQUIESCE_WINDOW_STATE={state}\n"
        "require_exact_verifier_role_outside_window\nrequire_exact_jobs_reader_role_outside_window\n"
        'echo "both-converges" >> "$CALLS"\n',
    )
    assert result.returncode == 0, result.stderr
    assert "both-converges" in calls and not _has(calls, "converge_lakebase_oauth_role --check")


@pytest.mark.parametrize(("state", "rc", "exit_code", "checks"), OUTSIDE_A_WINDOW)
def test_outside_a_window_the_verifier_role_must_already_be_exact(
    tmp_path: Path, state: str, rc: str, exit_code: int, checks: int
) -> None:
    result, calls = _run(
        tmp_path,
        f"QUIESCE_WINDOW_STATE={state}\nrequire_exact_verifier_role_outside_window\n"
        'echo "converge-verifier" >> "$CALLS"\n',
        VERIFIER_ROLE_RC=rc,
    )
    assert result.returncode == exit_code, result.stderr
    assert _has(calls, "--role-contract verifier") == checks
    assert ("converge-verifier" in calls) is (exit_code == 0), "drift refuses before the converge"


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


def test_a_contract_window_stays_open_through_activation_until_green_is_captured(
    tmp_path: Path,
) -> None:
    result, calls = _run(
        tmp_path,
        SEQUENCE
        + "end_quiesce_window_at_activation\n"
        + 'echo "activating=${QUIESCE_WINDOW_STATE:-none}"; echo "closes=$(grep -c "quiesce_window close" "$CALLS" || true)"\n'
        + "close_contract_window_after_capture\n"
        + 'echo "after=${QUIESCE_WINDOW_STATE:-none}"\n',
        REASONS="WR3",
        RESTORABLE="0",
    )
    assert result.returncode == 0, result.stderr
    assert "final=window_open" in result.stdout and "activating=contract_activating" in result.stdout
    assert "closes=0" in result.stdout, "the activation itself never closes the journal"
    assert "after=none" in result.stdout
    assert not _has(calls, "quiesce_window start") and not _has(calls, "rollback repin")
    close = next(call for call in calls if "quiesce_window close" in call)
    assert "--superseded-by-activation" in close and "--scope scope" in close
    assert "--lakebase-instance instance" in close


def test_a_restorable_window_still_open_at_activation_refuses_it(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, "QUIESCE_WINDOW_STATE=window_open\nend_quiesce_window_at_activation\necho reached\n"
    )
    assert result.returncode == 1 and "reached" not in result.stdout
    assert not _has(calls, "quiesce_window close")


@pytest.mark.parametrize("upgrade", ("green_activating_quiesced", "green_treatment_pending_capture"))
def test_a_failure_inside_an_activating_contract_window_stops_and_never_restores_blue(
    tmp_path: Path, upgrade: str
) -> None:
    result, calls = _run(
        tmp_path,
        f"QUIESCE_WINDOW_STATE=contract_activating; APP_UPGRADE_STATE={upgrade}; "
        "APP_SIGNED_BLUE_AVAILABLE=1\nstop_app_after_failed_deploy\n",
    )
    assert result.returncode == 1, "the lease is retained"
    assert "legacy-stop" in calls and "legacy-restore" not in calls
    assert not _has(calls, "rollback restore") and not _has(calls, "quiesce_window close")


@pytest.mark.parametrize(
    ("state", "restorable", "refused"),
    (
        ("contract_activating", "0", True),
        ("window_open", "0", True),
        ("window_open", "1", False),
        ("", "1", False),
    ),
)
def test_the_signed_blue_restore_refuses_while_a_contract_window_is_open(
    tmp_path: Path, state: str, restorable: str, refused: bool
) -> None:
    # The real restore, also reached outside the EXIT trap (the final-proof
    # rollback in step 11): blue never lands on a contract-migrated schema.
    release = shlex.quote(str(LIB / "deploy_app_release_lifecycle.sh"))
    result, calls = _run(
        tmp_path,
        f". {release}\nAPP_SIGNED_BLUE_AVAILABLE=1\n"
        f"QUIESCE_WINDOW_STATE={state}; QUIESCE_WINDOW_BLUE_RESTORABLE={restorable}\n"
        "restore_signed_blue_while_quiesced || echo \"restore rc=$?\"\n",
    )
    assert "restore rc=1" in result.stdout
    assert ("refusing to restore signed blue" in result.stderr) is refused
    assert not _has(calls, "rollback restore")


def test_the_step_files_open_each_reason_before_its_mutation_and_close_after_the_migration() -> None:
    text = deploy_entrypoint_text()
    steps = text.index('step "preflight — check .env.local, databricks CLI, venv"')

    def at(needle: str, start: int = steps) -> int:
        return text.index(needle, start)

    wr4 = at("ensure_quiesce_window_open_for WR4")
    apply = at('step "deploy non-App bundle resources without activating an App candidate"')
    wr1 = at("ensure_quiesce_window_open_for WR1")
    update = at('run keep_cli_result_private databricks apps update "$_GRANTS_APP_NAME"')
    wr2 = at("ensure_quiesce_window_open_for WR2")
    role = at('step "converge App Lakebase OAuth role to exact LOGIN-only profile"')
    # The verifier exists in the agentic tier only (M2-PR3): its check and converge sit in that branch.
    verifier_check = at("\n  require_exact_verifier_role_outside_window\n")
    verifier = at('step "converge verifier Lakebase OAuth role to exact LOGIN-only profile"')
    jobs_reader_check = at("\nrequire_exact_jobs_reader_role_outside_window\n")
    jobs_reader = at('step "converge jobs-reader Lakebase OAuth role to exact LOGIN-only profile"')
    wr3 = at("ensure_quiesce_window_open_for WR3")
    migrate = at("databricks bundle run mip_lakebase_migrate")
    proven = at("LAKEBASE_RUNTIME_ACCESS_PROVEN=1", migrate)
    close = at("\nclose_quiesce_window\n", migrate)
    degrade = at("mark_blue_degraded", close)
    activation = at("end_quiesce_window_at_activation")
    green = at('deploy_app_snapshot "activate App snapshot', activation)
    capture = at('capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"', green)
    contract_close = at("\n  close_contract_window_after_capture\n", capture)

    assert wr4 < apply < wr1 < update < wr2 < role < verifier_check < verifier < wr3 < migrate
    assert verifier < jobs_reader_check < jobs_reader < wr3, "M2-PR5: proven before its converge"
    assert migrate < proven < close < degrade
    assert activation < green < capture < contract_close
    assert "--stop-app-for-mutation" not in text[steps:], "the converger never stops the App"
