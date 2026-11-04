"""``deploy.sh --phase adopt|unadopt``: plan before the lease, edit state under it, never stop (M2-PR6).

Pinned on ``deploy_entrypoint_text()`` (the sliced layout) by position: the
read-only adopt plan replaces the pre-lease gate and precedes the lease and
every App stop; the apply follows the lease heartbeat and precedes every other
mutation of the run, and ends the run. The two adopt functions are driven in
the block harness with a stub tool, and the whole dry run in the offline
sandbox. A lexical contract keeps every stop, start, deploy, grant and delete
primitive off the adopt path (M0-PR8's repository-wide stop contract, once on
main, scans the slice too).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tests.fixtures.deploy_block_harness import block, drive, function
from tests.fixtures.deploy_sandbox import ANSI, planned_commands, sandbox
from tests.fixtures.deploy_script import deploy_entrypoint_text

REPO = Path(__file__).resolve().parents[2]
SLICE = REPO / "scripts" / "lib" / "deploy_adopt_phase_lifecycle.sh"
ADOPT_PATH = (
    SLICE,
    REPO / "tools" / "databricks" / "install_state_adopt.py",
    REPO / "tools" / "databricks" / "bundle_env_adopt.py",
    REPO / "tools" / "databricks" / "adopt_legacy_map.py",
    REPO / "tools" / "databricks" / "bundle_plan_adopt.py",
    REPO / "tools" / "clients" / "archive_legacy_root.py",
)
PLAN_START, PLAN_END = "# >>> adopt phase: plan >>>", "# <<< adopt phase: plan <<<"
APPLY_START, APPLY_END = "# >>> adopt phase: apply >>>", "# <<< adopt phase: apply <<<"
ACK = "a" * 64
# Every way a shell or Python file could stop, start, deploy or update the
# App, change a grant or permission, or delete (cf. M0-PR8's stop map).
FORBIDDEN = (
    r"\bapps\s+(?:stop|start|deploy|update|delete)\b",
    r"\bapps\.(?:stop|start|deploy|update|delete)\w*\(",
    r"_and_wait\(",
    r"stop_app_fail_closed",
    r"--stop-app-for-mutation",
    r"app_deployment_rollback",
    r"quiesce_window",
    r"--mode quarantine",
    r"\bstop_and_quiesce_unproven_app\b",
    r"\bdeploy_app_snapshot\b",
    r"\b(?:GRANT|REVOKE)\s",
    r"\b(?:set|update)_permissions\b",
    r"\.permissions\.(?:set|update)\(",
    r"\bgrants\.update\(",
    r"\bworkspace\s+delete\b",
    r"\.delete\w*\(",
    r"\.trash\(",
    r"\brm\s+-",
)
CARRIERS = (
    'databricks apps stop "$APP"',
    'run databricks apps start "$APP"',
    "workspace.apps.stop(name)",
    "client.apps.deploy(name, deployment)",
    "workspace.apps.start_and_wait(name)",
    '"$PYTHON" -m tools.databricks.stop_app_fail_closed --app-name "$APP"',
    '"$PYTHON" -m tools.databricks.app_deployment_rollback restore',
    "GRANT USE CATALOG ON CATALOG mip TO `x`",
    "workspace.grants.update(securable_type, name, changes=changes)",
    "workspace.workspace.delete(path, recursive=True)",
    "workspace.lakeview.trash(dashboard_id)",
    "workspace.permissions.set(object_type, object_id)",
)


def violations(text: str) -> list[str]:
    """Every forbidden primitive in ``text``, comments included (a comment is cheap to reword)."""

    return [pattern for pattern in FORBIDDEN if re.search(pattern, text)]


# --- lexical: the adopt path never stops, grants or deletes --------------------------


@pytest.mark.parametrize("path", ADOPT_PATH, ids=lambda path: path.name)
def test_the_adopt_path_holds_no_stop_grant_or_delete_primitive(path: Path) -> None:
    assert violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("carrier", CARRIERS)
@pytest.mark.parametrize("path", ADOPT_PATH, ids=lambda path: path.name)
def test_the_lexical_contract_sees_each_primitive_in_each_file(path: Path, carrier: str) -> None:
    """Positive control: one primitive inserted anywhere on the adopt path is reported."""

    assert violations(path.read_text(encoding="utf-8") + f"\n{carrier}\n"), carrier


def test_the_slice_calls_only_the_adopt_tool() -> None:
    text = SLICE.read_text(encoding="utf-8")

    assert set(re.findall(r"-m (tools\.[\w.]+)", text)) == {"tools.databricks.install_state_adopt"}
    assert "databricks " not in text.replace("tools.databricks", "")
    for line in text.splitlines():
        assert not line.startswith(("set ", "trap ", "exit ")), line


# --- ordering in the command of record ------------------------------------------------


def _steps(text: str) -> int:
    return text.index('step "preflight — check .env.local, databricks CLI, venv"')


def test_the_adopt_plan_replaces_the_pre_lease_gate_before_the_lease_and_every_stop() -> None:
    text = deploy_entrypoint_text()
    steps = _steps(text)
    plan = text.index(PLAN_START, steps)
    gate = text.index("# >>> bundle plan gate: pre-lease >>>", steps)
    lease = text.index("tools.databricks.app_deployment_lease acquire", steps)

    assert text.index("tools.databricks.deploy_prestop_probes", steps) < plan < gate < lease
    assert plan < text.index("APP_FAIL_CLOSED_ARMED=1", steps)
    gate_block = block("# >>> bundle plan gate: pre-lease >>>", "# <<< bundle plan gate: pre-lease <<<")
    assert (
        'if [[ "${DEPLOY_PHASE:-}" != "adopt" && "${DEPLOY_PHASE:-}" != "unadopt" ]]; then\n'
        "  run_bundle_plan_gate pre-lease\nfi\n"
    ) in gate_block
    assert [line for line in block(PLAN_START, PLAN_END).splitlines() if not line.startswith("#")] == [
        'if [[ "${DEPLOY_PHASE:-}" == "adopt" || "${DEPLOY_PHASE:-}" == "unadopt" ]]; then',
        '  run_adopt_phase_plan "$DEPLOY_PHASE"',
        "fi",
    ]


def test_the_apply_follows_the_lease_heartbeat_and_precedes_every_other_mutation() -> None:
    text = deploy_entrypoint_text()
    steps = _steps(text)
    apply = text.index(APPLY_START, steps)

    assert text.index("tools.databricks.app_deployment_lease heartbeat", steps) < apply
    for later in (
        "tools.databricks.oauth_credential_recovery_cli",
        "tools.databricks.app_rollback_secret_scope ensure",
        "databricks apps list -o json",
        "APP_FAIL_CLOSED_ARMED=1",
        "tools.databricks.stop_app_fail_closed",
        'run "$PYTHON" -m tools.databricks.bundle_env deploy -t "$TARGET"',
    ):
        assert apply < text.index(later, steps), later
    assert text.count(APPLY_START) == 1 and text.count('run_adopt_phase_apply "$DEPLOY_PHASE"') == 1
    assert text.count('run_adopt_phase_plan "$DEPLOY_PHASE"') == 1


def test_both_adopt_functions_end_the_run_on_every_path() -> None:
    plan, apply = function("run_adopt_phase_plan"), function("run_adopt_phase_apply")

    assert plan.count("exit 0") == 2 and plan.count("exit 2") == 1
    assert apply.count("    exit 3") == 1 and apply.count("    exit 2") == 1
    assert apply.rstrip().endswith("exit 0\n}"), "the apply ends the run: no later step runs in an adopt phase"
    assert "run_with_proof_signing_authority" in apply, "the tool proves the signed lease is held"
    for flag in ("--ack-plan", "--app-name", "--lease-id", "--source-git-sha", "--report-out"):
        assert flag in apply, flag


# --- the two functions, driven ---------------------------------------------------------


def _recording_tool(repo: Path, tool_rc: int) -> Path:
    """A stub ``tools.databricks.install_state_adopt`` that records its argv and exits ``tool_rc``."""

    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    recorded = repo / "argv.json"
    (package / "install_state_adopt.py").write_text(
        f"import json, sys\nopen({str(recorded)!r}, 'w').write(json.dumps(sys.argv[1:]))\nsys.exit({tool_rc})\n",
        encoding="utf-8",
    )
    return recorded


def _drive(
    tmp_path: Path, name: str, *, ack: str, tool_rc: int, dry_run: int = 0
) -> tuple[subprocess.CompletedProcess[str], Path]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    recorded = _recording_tool(repo, tool_rc)
    shell = (
        f"TMPDIR={tmp_path}\n"
        'mktemp() { local f="$TMPDIR/report.$RANDOM"; : > "$f"; printf \'%s\\n\' "$f"; }\n'
        'GRN=""\n'
        'run_with_proof_signing_authority() { "$@"; }\n'
        + function(name)
        + f"{name} adopt\n"
    )
    env = {
        "MIP_ADOPT_PLAN_ACK": ack,
        "_GRANTS_APP_NAME": "synthetic-app",
        "MIP_APP_DEPLOYMENT_LEASE_ID": "lease-1",
        "SOURCE_GIT_SHA": "0" * 40,
    }
    result = drive(repo, shell, dry_run=dry_run, env=env, epilogue="echo reached-the-lease\n")
    return result, recorded


def _argv(recorded: Path) -> list[str]:
    import json

    return json.loads(recorded.read_text(encoding="utf-8"))


def test_without_an_ack_the_plan_ends_the_run_read_only_before_the_lease(tmp_path: Path) -> None:
    result, recorded = _drive(tmp_path, "run_adopt_phase_plan", ack="", tool_rc=0)

    assert result.returncode == 0, result.stderr
    assert "adopt plan complete: read-only, stopped before the lease" in result.stdout
    assert "reached-the-lease" not in result.stdout
    argv = _argv(recorded)
    assert argv[:3] == ["plan", "--phase", "adopt"] and "--ack-plan" not in argv


def test_with_the_ack_the_plan_continues_to_the_lease(tmp_path: Path) -> None:
    result, recorded = _drive(tmp_path, "run_adopt_phase_plan", ack=ACK, tool_rc=0)

    assert result.returncode == 0, result.stderr
    assert "reached-the-lease" in result.stdout
    assert _argv(recorded)[-2:] == ["--ack-plan", ACK]


def test_a_dry_run_with_the_ack_stops_before_the_lease(tmp_path: Path) -> None:
    result, recorded = _drive(tmp_path, "run_adopt_phase_plan", ack=ACK, tool_rc=0, dry_run=1)

    assert result.returncode == 0 and "reached-the-lease" not in result.stdout
    assert not recorded.exists(), "a dry run prints, never runs"
    assert "would take the signed lease, then:" in result.stdout


def test_a_refused_plan_exits_2_before_the_lease(tmp_path: Path) -> None:
    result, _recorded = _drive(tmp_path, "run_adopt_phase_plan", ack=ACK, tool_rc=2)

    assert result.returncode == 2 and "reached-the-lease" not in result.stdout
    assert "the adopt plan refused before the lease" in result.stderr


@pytest.mark.parametrize(("tool_rc", "rc"), ((0, 0), (2, 2), (3, 3), (1, 2)))
def test_the_apply_ends_the_run_with_the_tools_verdict(tmp_path: Path, tool_rc: int, rc: int) -> None:
    result, recorded = _drive(tmp_path, "run_adopt_phase_apply", ack=ACK, tool_rc=tool_rc)

    assert result.returncode == rc, result.stdout + result.stderr
    assert "reached-the-lease" not in result.stdout
    assert _argv(recorded)[:5] == ["apply", "--phase", "adopt", "--ack-plan", ACK]
    assert _argv(recorded)[7:9] == ["--lease-id", "lease-1"]


# --- the whole dry run, offline ---------------------------------------------------------


def _deploy(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/deploy.sh", *args], cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=300
    )


def _refresh_off(checkout: Path) -> None:
    values = {
        **paychex_shaped_profile(MIP_SCHEDULED_REFRESH="off", MIP_SCHEDULED_REFRESH_RECIPIENTS=None),
        "DATABRICKS_WAREHOUSE_ID": "warehouse-1",
    }
    (checkout / ".env.local").write_text(env_file_text(values), encoding="utf-8")


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_the_adopt_phase_dry_run_plans_in_place_of_the_gate_and_stops_before_the_lease(
    tmp_path: Path, phase: str
) -> None:
    checkout, env, log = sandbox(tmp_path)
    _refresh_off(checkout)

    result = _deploy(checkout, {**env, "MIP_ADOPT_PLAN_ACK": ACK}, "-t", "client", "--phase", phase, "--dry-run", "--no-confirm")

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    commands = planned_commands(result.stdout)
    assert commands[0] == f".venv/bin/python -m tools.clients.live_checks --phase {phase}"
    assert commands[-1] == (
        f".venv/bin/python -m tools.databricks.install_state_adopt plan --phase {phase}"
        f" --report-out /tmp/mip-dry-run-{phase}-plan-report.json --ack-plan {ACK}"
    )
    assert not any("bundle_plan_gate" in command or "bundle_env plan" in command for command in commands)
    out = ANSI.sub("", result.stdout)
    assert "would take the signed lease, then:" in out
    assert out.rstrip().endswith(f"[deploy] {phase} dry run complete: stopped before the lease; nothing was changed.")
    for later in ("rollback secret scope", "resolve governed Genie space", "build frontend"):
        assert later not in out, later


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_a_live_adopt_phase_outside_github_actions_is_refused_before_any_call(tmp_path: Path, phase: str) -> None:
    checkout, env, log = sandbox(tmp_path)

    result = _deploy(checkout, env, "-t", "client", "--phase", phase, "--no-confirm")

    assert result.returncode == 2
    assert result.stderr.strip() == f"[deploy] {phase} is CI-only; use --dry-run or make bundle-validate"
    assert not log.exists() or log.read_text(encoding="utf-8") == ""
