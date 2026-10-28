"""deploy.sh's best-effort prune of App release folders, after a green capture.

The last block of the release (``# >>> app release source: prune >>>``) runs
tools/databricks/app_release_prune.py only in a dry run (printed, never
executed) or once ``APP_UPGRADE_STATE`` is ``green_verified``, which the
rollback branch never sets. The call sits in an ``if`` condition, so neither
errexit nor the ERR trap can fire on a prune failure, including ``run``'s own
lease-heartbeat refusal: the deploy only warns. The harness
(tests/fixtures/deploy_block_harness.py) runs the real block under
``set -Eeuo pipefail`` with an ERR trap that exits 99.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import (
    BUNDLE,
    PRINCIPAL,
    RELEASE_ID,
    SHA,
    block,
    code,
    drive,
    stub_tool,
)
from tests.fixtures.deploy_script import deploy_entrypoint_text

PRUNE_START = "# >>> app release source: prune >>>"
PRUNE_END = "# <<< app release source: prune <<<"
PRUNE_GATE = 'if [[ "$DRY_RUN" -eq 1 || "$APP_UPGRADE_STATE" == "green_verified" ]]; then'
PRUNE_ARGS = [
    "--app-name", "mip-app", "--expected-app-id", "app-id-1", "--current-release", RELEASE_ID,
    "--rollback-deployment-id", "deployment-1", "--deployment-lease-id", "lease-1",
    "--source-git-sha", SHA, "--files-root", f"{BUNDLE}/dev/files",
    "--expected-principal", PRINCIPAL, "--expected-target", "dev", "--expected-root-leaf", "dev",
]


def _prune(repo: Path, *, dry_run: int, state: str, exit_code: int = 0, extra: dict[str, str] | None = None):
    argv = list(PRUNE_ARGS)
    env = {
        "APP_UPGRADE_STATE": state, "APP_NAME": "mip-app", "APP_OBJECT_ID": "app-id-1",
        "APP_RELEASE_ID": RELEASE_ID, "MIP_APP_ROLLBACK_DEPLOYMENT_ID": "deployment-1",
        "MIP_APP_DEPLOYMENT_LEASE_ID": "lease-1", "SOURCE_GIT_SHA": SHA,
        "APP_BUNDLE_FILES_PATH": f"{BUNDLE}/dev/files", "APP_CURRENT_USER": PRINCIPAL, "TARGET": "dev",
        **(extra or {}),
    }
    if dry_run:
        del env["APP_OBJECT_ID"]  # assigned only in a real run (step 4)
        argv[argv.index("app-id-1")] = "dry-run-app-id"
    called = stub_tool(repo, "app_release_prune", argv=argv, body=f"sys.exit({exit_code})\n")
    result = drive(repo, block(PRUNE_START, PRUNE_END), dry_run=dry_run, env=env, epilogue="")
    return result, called


def test_prune_is_the_last_block_of_the_release_and_never_on_the_rollback_branch() -> None:
    text = deploy_entrypoint_text()
    shell = code(block(PRUNE_START, PRUNE_END))
    prune = text.index(PRUNE_START)

    assert text.index('  APP_UPGRADE_STATE="green_verified"\n') < prune
    assert text.index("  run restore_signed_blue_while_quiesced\nfi\n") < prune
    assert text.index(PRUNE_END) < text.index('echo "${GRN}[deploy] complete.${RST}"')
    assert shell.startswith(f'{PRUNE_GATE}\n  step "prune App release folders no retained deployment references (best effort)"\n')
    assert '  if ! run "$PYTHON" -m tools.databricks.app_release_prune \\\n' in shell
    assert '--expected-app-id "${APP_OBJECT_ID:-dry-run-app-id}"' in shell
    assert "exit" not in shell and shell.count("-m tools.") == 1
    assert text.count("-m tools.databricks.app_release_prune") == 1


def test_the_deploy_never_deletes_workspace_objects_through_the_cli() -> None:
    text = deploy_entrypoint_text()

    assert "databricks workspace delete" not in text
    assert "workspace delete" not in text


@pytest.mark.parametrize("exit_code", [1, 2, 3])
def test_a_failing_prune_only_warns_under_errexit_and_the_err_trap(tmp_path: Path, exit_code: int) -> None:
    result, called = _prune(tmp_path, dry_run=0, state="green_verified", exit_code=exit_code)

    assert result.returncode == 0, result.stderr
    assert called.exists()
    assert "App release folder prune did not finish" in result.stderr
    assert "reached-after-block" in result.stdout


def test_a_dead_lease_heartbeat_stops_the_prune_call_and_only_warns(tmp_path: Path) -> None:
    dead = subprocess.Popen(["true"])
    dead.wait()

    result, called = _prune(
        tmp_path, dry_run=0, state="green_verified", extra={"APP_DEPLOYMENT_LEASE_HEARTBEAT_PID": str(dead.pid)}
    )

    assert result.returncode == 0, result.stderr
    assert not called.exists()
    assert "lease heartbeat is not running" in result.stderr
    assert "App release folder prune did not finish" in result.stderr


def test_a_green_prune_runs_once_with_the_reviewed_arguments(tmp_path: Path) -> None:
    result, called = _prune(tmp_path, dry_run=0, state="green_verified")

    assert result.returncode == 0, result.stderr
    assert called.read_text() == "called\n"
    assert "prune did not finish" not in result.stderr


@pytest.mark.parametrize(
    "state",
    ["first_install", "blue_active", "blue_quiesced", "green_activating_quiesced",
     "green_treatment_pending_capture", "green_captured_cleanup_pending"],
)
def test_a_real_run_that_is_not_green_verified_never_calls_prune(tmp_path: Path, state: str) -> None:
    result, called = _prune(tmp_path, dry_run=0, state=state)

    assert result.returncode == 0, result.stderr
    assert not called.exists()
    assert "step: prune" not in result.stdout


def test_a_dry_run_prints_the_prune_with_an_inert_app_id(tmp_path: Path) -> None:
    result, called = _prune(tmp_path, dry_run=1, state="first_install")

    assert result.returncode == 0, result.stderr
    assert not called.exists()
    assert "--expected-app-id dry-run-app-id --current-release" in result.stdout
    assert "step: prune App release folders" in result.stdout
