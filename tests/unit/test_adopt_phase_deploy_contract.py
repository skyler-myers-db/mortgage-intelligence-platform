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
    # adopt-live (M2-PR7): it signs a record and copies a folder, never an App mutation.
    REPO / "tools" / "databricks" / "adopt_live.py",
    REPO / "tools" / "databricks" / "adopt_live_facts.py",
    REPO / "tools" / "databricks" / "adopted_source_copy.py",
    REPO / "tools" / "databricks" / "app_rollback_record_v7.py",
    REPO / "tools" / "databricks" / "operation_ack.py",
    # The owner's read-only preflight of the bind plan (cutover step 6c): GETs and a private report.
    REPO / "tools" / "databricks" / "adopt_preflight.py",
)
PLAN_START, PLAN_END = "# >>> adopt phase: plan >>>", "# <<< adopt phase: plan <<<"
APPLY_START, APPLY_END = "# >>> adopt phase: apply >>>", "# <<< adopt phase: apply <<<"
# The window library's functions that stop or start the App (M0-PR8), as
# tests/unit/test_bundle_plan_gate_deploy_contract.py lists them.
WINDOW_STOPS = (
    "stop_unsigned_rebase_app",
    "stop_journal_owned_first_install_app",
    "stop_ungoverned_existing_app_before_binding_update",
    "stop_unproven_candidate_before_rollback",
    "start_stopped_app_before_activation",
    "prove_or_restore_signed_blue",
    "ensure_quiesce_window_open_for",
    "close_quiesce_window",
    "stop_and_quiesce_unproven_app",
)
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


def test_the_slice_calls_only_the_adopt_tools() -> None:
    text = SLICE.read_text(encoding="utf-8")

    # The bind, adopt-live, and the adopted blue's pre-activation proof (M2-PR7),
    # with the core tier's read-only rebuild of blue's restore payload (M2-PR7b).
    assert set(re.findall(r"-m (tools\.[\w.]+)", text)) == {
        "tools.databricks.install_state_adopt",
        "tools.databricks.adopt_live",
        "tools.databricks.blue_restore_proof",
        "tools.databricks.app_core_rollback",
    }
    assert re.findall(r"-m tools\.databricks\.app_core_rollback (\S+)", text) == ["restore-payload"]
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
    # Nor does the data phase (M1-PR6), whose read-only data plan stands in its place.
    assert (
        'if [[ "${DEPLOY_PHASE:-}" != "adopt" && "${DEPLOY_PHASE:-}" != "unadopt" && \\\n'
        '      "${DEPLOY_PHASE:-}" != "data" ]]; then\n'
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
        'run "$PYTHON" -m tools.databricks.bundle_env deploy -t "$TARGET"',
    ):
        assert apply < text.index(later, steps), later
    # Since M0-PR8 the steps never stop the App inline: every stop is a call to
    # a window-library function, so the apply precedes the first call to each.
    assert "tools.databricks.stop_app_fail_closed" not in text[steps:apply]
    called = {name: m.start() for name in WINDOW_STOPS if (m := re.search(rf"\b{name}\b", text[steps:]))}
    assert {"stop_unsigned_rebase_app", "ensure_quiesce_window_open_for"} <= set(called)
    for name, offset in called.items():
        assert apply < steps + offset, name
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


def _recording_tool(repo: Path, tool_rc: int, *, writes_report: bool = True) -> Path:
    """A stub ``tools.databricks.install_state_adopt`` that records its argv, writes a report and exits ``tool_rc``."""

    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    recorded = repo / "argv.json"
    report = (
        "out = sys.argv[sys.argv.index('--report-out') + 1]\n"
        "open(out, 'w').write(json.dumps({'phase': sys.argv[sys.argv.index('--phase') + 1]}))\n"
        if writes_report
        else ""
    )
    (package / "install_state_adopt.py").write_text(
        f"import json, sys\nopen({str(recorded)!r}, 'w').write(json.dumps(sys.argv[1:]))\n{report}sys.exit({tool_rc})\n",
        encoding="utf-8",
    )
    return recorded


def _recording_publisher(repo: Path, publish_rc: int) -> Path:
    """A stub ``tools.databricks.plan_report_publish`` that records each argv and the report it was handed."""

    recorded = repo / "published.jsonl"
    (repo / "tools" / "databricks" / "plan_report_publish.py").write_text(
        "import json, sys\n"
        "full = open(sys.argv[sys.argv.index('--full') + 1]).read()\n"
        f"open({str(recorded)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'full': full}}) + '\\n')\n"
        f"sys.exit({publish_rc})\n",
        encoding="utf-8",
    )
    return recorded


def _drive(
    tmp_path: Path,
    name: str,
    *,
    ack: str,
    tool_rc: int,
    dry_run: int = 0,
    gha: bool = False,
    publish_rc: int = 0,
    writes_report: bool = True,
    phase: str = "adopt",
    run_id: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    recorded = _recording_tool(repo, tool_rc, writes_report=writes_report)
    _recording_publisher(repo, publish_rc)
    shell = (
        f"TMPDIR={tmp_path}\n"
        'mktemp() { local f="$TMPDIR/report.$RANDOM"; : > "$f"; printf \'%s\\n\' "$f"; }\n'
        'GRN=""\n'
        'run_with_proof_signing_authority() { "$@"; }\n'
        # adopt-live's own functions are driven in tests/unit/test_adopt_live_deploy_contract.py.
        'run_adopt_live_plan() { echo "adopt-live-planned"; }\nrun_adopt_live_apply() { :; }\n'
        # The report publish (wave 7f) is the real function, over a recording publisher.
        "BUNDLE_PLAN_REPORT_JSON=''\nBUNDLE_PLAN_PUBLIC_JSON=''\nBUNDLE_PLAN_REPORT_AFTER_EDIT=0\n"
        "BUNDLE_PLAN_REPORT_PUBLISHED=''\n"
        + function("publish_plan_report")
        + function("plan_report_write_note")
        + function(name)
        + f"{name} {phase}\n"
    )
    env = {
        "MIP_ADOPT_PLAN_ACK": ack,
        "_GRANTS_APP_NAME": "synthetic-app",
        "MIP_APP_DEPLOYMENT_LEASE_ID": "lease-1",
        "SOURCE_GIT_SHA": "0" * 40,
        "DEPLOY_PHASE": phase,
        "CLIENT_DEPLOYER": "00000000-0000-4000-8000-00000000d0e1",
        "CLIENT_KEY": "client-one",
        "GITHUB_ACTIONS": "true" if gha else "",
        # The run id alone (run_id, without gha) is not GitHub Actions: the publish guard needs both.
        "GITHUB_RUN_ID": ("4242" if gha else "") if run_id is None else run_id,
        "GITHUB_RUN_ATTEMPT": "2",
    }
    result = drive(repo, shell, dry_run=dry_run, env=env, epilogue="echo reached-the-lease\n")
    return result, recorded


def _published(tmp_path: Path) -> list[dict[str, object]]:
    import json

    path = tmp_path / "repo" / "published.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _argv(recorded: Path) -> list[str]:
    import json

    return json.loads(recorded.read_text(encoding="utf-8"))


def test_without_an_ack_the_plan_ends_the_run_read_only_before_the_lease(tmp_path: Path) -> None:
    result, recorded = _drive(tmp_path, "run_adopt_phase_plan", ack="", tool_rc=0)

    assert result.returncode == 0, result.stderr
    assert "adopt plan complete: read-only, stopped before the lease; nothing was changed." in result.stdout
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
    assert result.stderr.rstrip().endswith("; nothing was changed."), "outside GitHub Actions the run wrote nothing"


@pytest.mark.parametrize(("tool_rc", "rc"), ((0, 0), (2, 2), (3, 3), (1, 2)))
def test_the_apply_ends_the_run_with_the_tools_verdict(tmp_path: Path, tool_rc: int, rc: int) -> None:
    result, recorded = _drive(tmp_path, "run_adopt_phase_apply", ack=ACK, tool_rc=tool_rc)

    assert result.returncode == rc, result.stdout + result.stderr
    assert "reached-the-lease" not in result.stdout
    assert _argv(recorded)[:5] == ["apply", "--phase", "adopt", "--ack-plan", ACK]
    assert _argv(recorded)[7:9] == ["--lease-id", "lease-1"]


# --- the private report reaches the deployer's plan-reports folder (wave 7f) ---------------


def _publish_argv(report: str) -> list[str]:
    return [
        "publish", "--full", report, "--principal", "00000000-0000-4000-8000-00000000d0e1",
        "--client", "client-one", "--run-id", "4242", "--run-attempt", "2",
    ]  # fmt: skip


# Both phases publish: unadopt is 10B's recovery after an exit 3, and its report names what it unbound.
@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
@pytest.mark.parametrize(("ack", "tool_rc", "rc"), (("", 2, 2), (ACK, 2, 2), ("", 0, 0)), ids=("refused", "refused-acked", "no-ack"))
def test_a_run_that_ends_at_the_plan_publishes_its_report_once(
    tmp_path: Path, ack: str, tool_rc: int, rc: int, phase: str
) -> None:
    result, recorded = _drive(tmp_path, "run_adopt_phase_plan", ack=ack, tool_rc=tool_rc, gha=True, phase=phase)

    assert result.returncode == rc, result.stdout + result.stderr
    (published,) = _published(tmp_path)
    report = _argv(recorded)[_argv(recorded).index("--report-out") + 1]
    assert _argv(recorded)[:3] == ["plan", "--phase", phase]
    assert published == {"argv": _publish_argv(report), "full": f'{{"phase": "{phase}"}}'}
    assert "reached-the-lease" not in result.stdout
    if tool_rc == 0:
        assert result.stdout.index("plan_report_publish publish") < result.stdout.index("adopt-live-planned"), (
            "published before adopt-live's plan can refuse"
        )
    # The published report is the run's one write: its closing line says so, never "read-only".
    wrote = (
        "no governed resource was changed (its one write, under GitHub Actions only, is the create-only "
        "report in the deployer's plan-reports folder as 4242-2.json)"
    )
    closing = (result.stdout if tool_rc == 0 else result.stderr).rstrip().splitlines()[-1]
    assert wrote in closing and "nothing was changed" not in closing and "read-only" not in closing
    if tool_rc == 0:
        assert f"[deploy] {phase} plan complete: stopped before the lease; {wrote}. Review it" in closing
    else:
        assert f"[deploy] the {phase} plan refused before the lease;" in closing


def test_an_acked_plan_that_passes_leaves_the_publish_to_the_apply(tmp_path: Path) -> None:
    result, _recorded = _drive(tmp_path, "run_adopt_phase_plan", ack=ACK, tool_rc=0, gha=True)

    assert result.returncode == 0 and "reached-the-lease" in result.stdout
    assert _published(tmp_path) == []


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
@pytest.mark.parametrize(("tool_rc", "rc"), ((0, 0), (2, 2), (3, 3)))
@pytest.mark.parametrize("publish_rc", (0, 2))
def test_the_apply_publishes_its_report_and_a_failed_publish_never_changes_the_verdict(
    tmp_path: Path, tool_rc: int, rc: int, publish_rc: int, phase: str
) -> None:
    result, recorded = _drive(
        tmp_path, "run_adopt_phase_apply", ack=ACK, tool_rc=tool_rc, gha=True, publish_rc=publish_rc, phase=phase
    )

    assert result.returncode == rc, result.stdout + result.stderr
    (published,) = _published(tmp_path)
    assert _argv(recorded)[:3] == ["apply", "--phase", phase]
    assert published["argv"] == _publish_argv(_argv(recorded)[_argv(recorded).index("--report-out") + 1])
    assert published["full"] == f'{{"phase": "{phase}"}}'
    failed = f"the private {phase} report could not be written to the deployer's plan-reports folder"
    assert (failed in result.stderr) == (publish_rc != 0)


def test_a_failed_publish_of_a_plan_that_ended_the_run_refuses(tmp_path: Path) -> None:
    result, _recorded = _drive(tmp_path, "run_adopt_phase_plan", ack="", tool_rc=0, gha=True, publish_rc=2)

    assert result.returncode == 2
    assert "the full plan report could not be written to the deployer's plan-reports folder" in result.stderr


@pytest.mark.parametrize("name", ("run_adopt_phase_plan", "run_adopt_phase_apply"))
def test_outside_github_actions_or_without_a_report_nothing_is_published(tmp_path: Path, name: str) -> None:
    local, _recorded = _drive(tmp_path / "local", name, ack=ACK, tool_rc=2)
    # A run id without GITHUB_ACTIONS=true is not a GitHub Actions run: the sink stays CI-only.
    run_id_only, _recorded = _drive(tmp_path / "run-id-only", name, ack=ACK, tool_rc=2, run_id="4242")
    empty, _recorded = _drive(tmp_path / "empty", name, ack=ACK, tool_rc=2, gha=True, writes_report=False)

    assert local.returncode == run_id_only.returncode == empty.returncode == 2
    assert _published(tmp_path / "local") == [] and _published(tmp_path / "run-id-only") == []
    assert _published(tmp_path / "empty") == []
    if name == "run_adopt_phase_plan":
        for run in (local, run_id_only, empty):
            assert run.stderr.rstrip().endswith("; nothing was changed."), "a run that published nothing wrote nothing"
    assert "no adopt report to publish: the tool wrote none" in empty.stdout


def test_a_dry_run_says_it_would_publish_the_private_report(tmp_path: Path) -> None:
    result, _recorded = _drive(tmp_path, "run_adopt_phase_plan", ack="", tool_rc=0, dry_run=1, gha=True)

    assert result.returncode == 0
    assert "would publish: the private adopt report to the deployer's plan-reports folder (CI only)" in result.stdout
    assert _published(tmp_path) == []


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

    acks = {"MIP_ADOPT_PLAN_ACK": ACK, "MIP_ACK_ADOPT_LIVE": ACK, "MIP_ACK_UNADOPT": ACK}
    result = _deploy(checkout, {**env, **acks}, "-t", "client", "--phase", phase, "--dry-run", "--no-confirm")

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    commands = planned_commands(result.stdout)
    assert commands[0] == f".venv/bin/python -m tools.clients.live_checks --phase {phase}"
    assert commands[-2] == (
        f".venv/bin/python -m tools.databricks.install_state_adopt plan --phase {phase}"
        f" --report-out /tmp/mip-dry-run-{phase}-plan-report.json --ack-plan {ACK}"
    )
    # adopt-live's read-only plan follows the bind plan, before the lease (M2-PR7).
    assert commands[-1].startswith(f".venv/bin/python -m tools.databricks.adopt_live plan --mode {phase} ")
    assert commands[-1].endswith(f" --ack {ACK}")
    assert not any("bundle_plan_gate" in command or "bundle_env plan" in command for command in commands)
    out = ANSI.sub("", result.stdout)
    assert "would take the signed lease, then:" in out
    assert out.rstrip().endswith(f"[deploy] {phase} dry run complete: stopped before the lease; nothing was changed.")
    for later in ("rollback secret scope", "resolve governed Genie space", "build frontend"):
        assert later not in out, later


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_the_adopt_phase_dry_run_reads_and_hands_on_no_warehouse(tmp_path: Path, phase: str) -> None:
    """B1: the adopt phases run no SQL, so they take no warehouse, not even an ambient export.

    The ``.env.local`` is CI's shape (no DATABRICKS_WAREHOUSE_ID: it is no
    profile field), and the shell exports one the run must never pass on.
    """

    checkout, env, log = sandbox(tmp_path)
    values = paychex_shaped_profile(MIP_SCHEDULED_REFRESH="off", MIP_SCHEDULED_REFRESH_RECIPIENTS=None)
    (checkout / ".env.local").write_text(env_file_text(values), encoding="utf-8")
    ambient = "ambient-warehouse-1"

    acks = {"MIP_ADOPT_PLAN_ACK": ACK, "MIP_ACK_ADOPT_LIVE": ACK, "MIP_ACK_UNADOPT": ACK}
    result = _deploy(
        checkout,
        {**env, **acks, "DATABRICKS_WAREHOUSE_ID": ambient},
        "-t", "client", "--phase", phase, "--dry-run", "--no-confirm",
    )  # fmt: skip

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    out = ANSI.sub("", result.stdout)
    assert f"  sql warehouse: none (--phase {phase} runs no SQL)\n" in out
    assert out.rstrip().endswith(f"[deploy] {phase} dry run complete: stopped before the lease; nothing was changed.")
    assert [command for command in planned_commands(result.stdout) if "warehouse" in command.lower()] == []
    assert ambient not in result.stdout + result.stderr


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_a_live_adopt_phase_outside_github_actions_is_refused_before_any_call(tmp_path: Path, phase: str) -> None:
    checkout, env, log = sandbox(tmp_path)

    result = _deploy(checkout, env, "-t", "client", "--phase", phase, "--no-confirm")

    assert result.returncode == 2
    assert result.stderr.strip() == f"[deploy] {phase} is CI-only; use --dry-run or make bundle-validate"
    assert not log.exists() or log.read_text(encoding="utf-8") == ""
