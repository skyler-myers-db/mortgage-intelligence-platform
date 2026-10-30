"""deploy.sh runs the bundle plan gate before the lease and every App stop (M0-PR4).

The orchestration is read from ``deploy_entrypoint_text()`` (the sliced
deploy.sh layout) and pinned by position: SQL render and bundle validation,
then the pre-lease gate, then the ``--phase plan`` exit, then the signed lease,
and only then any App stop, direct or through a function that stops the App.
The pre-apply gate re-plans under the lease after Genie provisioning and
immediately before the non-App bundle apply. The pre-lease block is then
driven in a small harness with the real gate on a sanitized plan: the plan
phase exits 0 there, read-only, and never reaches the lease.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tests.fixtures.deploy_block_harness import block, drive, function, stub_tool
from tests.fixtures.deploy_script import DEPLOY_SCRIPT, deploy_contract_text, deploy_entrypoint_text

REPO = Path(__file__).resolve().parents[2]
PREFLIGHT = 'step "preflight — check .env.local, databricks CLI, venv"'
GATE_START = "# >>> bundle plan gate: pre-lease >>>"
GATE_END = "# <<< bundle plan gate: pre-lease <<<"
PRE_LEASE = "\nrun_bundle_plan_gate pre-lease\n"
PRE_APPLY = "\nrun_bundle_plan_gate pre-apply\n"
LEASE_ROOT = "tools.databricks.app_deployment_lease recovery-root"
LEASE = "tools.databricks.app_deployment_lease acquire"
# Every way the orchestration stops, quarantines or restarts the live App.
APP_STOPS = (
    "tools.databricks.stop_app_fail_closed",
    "--mode quarantine",
    "tools.databricks.app_deployment_rollback ensure",
    "apps stop",
    "--stop-app-for-mutation",
)
GATE_MODULES = (
    "bundle_plan_gate.py",
    "bundle_plan_instance_ack.py",
    "bundle_plan_lookups.py",
    "bundle_plan_shape.py",
    "deploy_plan_report.py",
)


def _steps(text: str) -> int:
    return text.index(PREFLIGHT)


def _functions_that_stop_the_app() -> set[str]:
    """Every shell function whose body (transitively) stops or restarts the App."""

    bodies = {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{\n(.*?)^\}\n", deploy_contract_text(), re.M | re.S
        )
    }
    stopping = {name for name, body in bodies.items() if any(stop in body for stop in APP_STOPS)}
    grew = True
    while grew:
        grew = False
        for name, body in bodies.items():
            if name not in stopping and any(_calls(body, other) for other in stopping):
                stopping.add(name)
                grew = True
    return stopping


def _calls(text: str, name: str) -> list[int]:
    return [
        match.start()
        for match in re.finditer(rf"(?<![\w-]){re.escape(name)}(?![\w-])(?!\(\) \{{)", text)
    ]


# --- ordering -----------------------------------------------------------------------


def test_the_pre_lease_gate_precedes_the_lease_and_every_app_stop() -> None:
    text = deploy_entrypoint_text()
    steps = _steps(text)
    assert text.count(PRE_LEASE) == 1
    gate = text.index(PRE_LEASE, steps)

    assert text.index("tools.databricks.deploy_prestop_probes", steps) < gate
    assert text.index("  # <<< pii-salt pre-stop check <<<", steps) < gate
    assert gate < text.index(LEASE_ROOT, steps) < text.index(LEASE, steps)
    assert gate < text.index(
        "APP_FAIL_CLOSED_ARMED=1", steps
    ), "the fail-closed stop is armed after it"
    for stop in APP_STOPS:
        assert all(gate < steps + site for site in _calls(text[steps:], stop)), stop
    assert len(_calls(text[steps:], "tools.databricks.stop_app_fail_closed")) >= 4
    stopping = _functions_that_stop_the_app()
    assert {
        "stop_and_quiesce_unproven_app",
        "recover_interrupted_first_install_app",
        "deploy_app_snapshot",
    } <= stopping
    for name in stopping:
        assert all(gate < steps + site for site in _calls(text[steps:], name)), name


def test_sql_render_and_bundle_validation_moved_ahead_of_the_gate_and_the_lease() -> None:
    text = deploy_entrypoint_text()
    steps = _steps(text)
    render = 'run "$PYTHON" tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"'
    validate = 'run "$PYTHON" -m tools.databricks.bundle_env validate -t "$TARGET"'

    assert text.count(render) == 1 and text.count(validate) == 1
    baseline = text.index("cannot import the deploy tooling baseline", steps)
    assert baseline < text.index(render) < text.index(validate) < text.index(PRE_LEASE)
    assert text.index("RESTORE_RENDERED_SQL_FAIL_CLOSED=1", steps) < text.index(PRE_LEASE)


def test_the_plan_phase_exits_at_top_level_between_the_gate_and_the_lease() -> None:
    text = deploy_entrypoint_text()
    gate_block = block(GATE_START, GATE_END)
    lines = gate_block.splitlines()

    assert text.index(GATE_END) < text.index(LEASE_ROOT, _steps(text))
    assert text[: text.index(GATE_START)].rstrip().endswith("fi"), "outside the dry-run branches"
    assert text[text.index(GATE_END) + len(GATE_END) :].startswith(
        '\nif [[ "$DRY_RUN" -eq 0 ]]; then\n'
    )
    code = [line for line in lines if not line.startswith("#")]
    assert code == [
        "run_bundle_plan_gate pre-lease",
        'if [[ "${DEPLOY_PHASE:-}" == "plan" ]]; then',
        '  echo "${GRN}[deploy] plan phase complete: read-only, stopped before the lease; nothing was changed.${RST}"',
        "  exit 0",
        "fi",
    ]


def test_the_pre_apply_gate_replans_after_genie_and_immediately_before_the_non_app_apply() -> None:
    text = deploy_entrypoint_text()
    steps = _steps(text)
    assert text.count(PRE_APPLY) == 1
    pre_apply = text.index(PRE_APPLY)
    apply = text.index('run "$PYTHON" -m tools.databricks.bundle_env deploy -t "$TARGET"')

    assert (
        text.index(LEASE, steps)
        < text.index("tools.databricks.provision_genie_space", steps)
        < pre_apply
        < apply
    )
    assert text.index("# <<< app release source: stage <<<") < pre_apply
    between = text[pre_apply:apply]
    # Only the empty pipeline namespace (not a bundle resource), the exact-source
    # gate and the read-only summary sit between the re-plan and the apply.
    tools = set(re.findall(r"tools\.databricks\.(\w+(?: summary)?)", between))
    assert tools == {"ensure_pipeline_namespace", "bundle_env summary"}
    assert between.count("verify_exact_deploy_source") == 2


def test_the_gate_function_is_the_only_bundle_planner() -> None:
    contract = deploy_contract_text()
    body = function("run_bundle_plan_gate")

    assert contract.count("tools.databricks.bundle_env plan") == 1
    assert (
        '"$PYTHON" -m tools.databricks.bundle_env plan -t "$TARGET" -o json "${genie_args[@]}"'
        in body
    )
    assert "      genie_args=(--allow-unprovisioned-genie)\n" in body
    assert "pre-apply) ;;" in body
    assert '--position "$position"' in body and "--report-out" in body
    assert body.count("exit 2") == 3


def test_only_the_pre_lease_gate_reads_the_first_install_journal() -> None:
    body = function("run_bundle_plan_gate")
    pre_lease = body[body.index("    pre-lease)\n") : body.index("    pre-apply) ;;")]

    assert pre_lease.count("journal_args=(") == 1
    for argument in (
        '--first-install-app-name "$_GRANTS_APP_NAME"',
        '--rollback-scope "$APP_ROLLBACK_SECRET_SCOPE"',
        '--lakebase-instance "$MIP_LAKEBASE_INSTANCE"',
    ):
        assert argument in pre_lease and body.count(argument) == 1, argument
    assert 'local -a genie_args=() journal_args=()' in body
    assert '--report-out "$report_json" \\\n    "${journal_args[@]}" || gate_rc=$?' in body


def test_the_phase_flag_is_parsed_and_the_live_plan_refused_before_any_databricks_call() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    phase = script[script.index("# >>> deploy phase >>>") : script.index("# <<< deploy phase <<<")]

    assert '[[ "$PHASE_SEEN" -eq 1 && "$DEPLOY_PHASE" != "plan" ]]' in phase
    assert 'echo "[deploy] plan is CI-only; use --dry-run or make bundle-validate" >&2' in phase
    assert (
        '"$DEPLOY_PHASE" == "plan" && "$DRY_RUN" -eq 0 && "${GITHUB_ACTIONS:-}" != "true"' in phase
    )
    assert script.index("# <<< deploy phase <<<") < script.index("\nverify_exact_deploy_source\n")
    assert "databricks " not in phase.replace("tools.databricks", "")


# --- the pre-lease block, driven -----------------------------------------------------


def _harness(
    tmp_path: Path,
    doc: dict[str, object] | str,
    *,
    phase: str,
    bundle_rc: int = 0,
    journal: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """Drive the pre-lease block: a stub bundle_env prints ``doc``, the real gate judges it.

    With ``journal``, the gate's workspace is a double in which the planned App
    exists, and the signed first-install journal reads ``journal``.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    if journal is not None:
        _workspace_and_journal_doubles(repo, journal)
    planned = stub_tool(
        repo,
        "bundle_env",
        argv=["plan", "-t", "dev", "-o", "json", "--allow-unprovisioned-genie"],
        body=f"sys.stdout.write({doc if isinstance(doc, str) else fx.dumps(doc)!r})\nsys.exit({bundle_rc})\n",
    )
    lease = stub_tool(repo, "app_deployment_lease", argv=["acquire"], body="")
    for module in GATE_MODULES:  # the real gate, beside the stubs
        shutil.copy2(REPO / "tools" / "databricks" / module, repo / "tools" / "databricks" / module)
    shell = (
        f"TMPDIR={tmp_path}\n"
        # macOS mktemp -t ignores TMPDIR; keep both temp files where the test looks.
        'mktemp() { [[ "$1" == "-t" ]] || return 97; local f="$TMPDIR/${2/XXXXXX/$RANDOM$RANDOM}"; '
        ': > "$f"; printf \'%s\\n\' "$f"; }\n'
        'GRN=""\n'
        'databricks() { echo "unexpected databricks $*" >&2; return 97; }\n'
        + function("run_json_to_file")
        + function("run_bundle_plan_gate")
        + block(GATE_START, GATE_END)
    )
    result = drive(
        repo,
        shell,
        dry_run=0,
        env={
            "TARGET": "dev",
            "DEPLOY_PHASE": phase,
            "_GRANTS_APP_NAME": APP_NAME,
            "APP_ROLLBACK_SECRET_SCOPE": "synthetic-rollback-scope",
            "MIP_LAKEBASE_INSTANCE": "synthetic-instance",
        },
        # The next statement in deploy.sh is the lease; reaching it would call this.
        epilogue='"$PYTHON" -m tools.databricks.app_deployment_lease acquire\n',
    )
    called = sorted(path.name for path in repo.glob("*.called"))
    assert planned.name in called, "bundle_env plan ran with exactly the pre-lease arguments"
    return result, called, lease


def test_the_plan_phase_exits_read_only_and_never_takes_the_lease(tmp_path: Path) -> None:
    result, called, lease = _harness(tmp_path, fx.adopted(), phase="plan")

    assert result.returncode == 0, result.stderr
    assert "plan phase complete: read-only, stopped before the lease" in result.stdout
    assert "[mip-plan-gate] pre-lease plan gate passed (read-only)" in result.stdout
    assert called == [
        "bundle_env.called"
    ], "bundle_env plan is the only stubbed call; the lease never runs"
    assert not lease.exists()
    assert "reached-after-block" not in result.stdout
    assert "unexpected databricks" not in result.stderr
    reports = list(tmp_path.glob("mip-bundle-plan-report.*"))
    assert len(reports) == 1 and not list(
        tmp_path.glob("mip-bundle-plan.*")
    ), "the raw plan is removed"


def test_a_full_run_passes_the_gate_and_continues_to_the_lease(tmp_path: Path) -> None:
    result, called, lease = _harness(tmp_path, fx.adopted(), phase="")

    assert result.returncode == 0, result.stderr
    assert "reached-after-block" in result.stdout
    assert called == ["app_deployment_lease.called", "bundle_env.called"]
    assert lease.exists()


@pytest.mark.parametrize("phase", ("plan", ""))
@pytest.mark.parametrize(
    "entries",
    (
        {"resources.jobs.retired_job": fx.entry("delete")},
        {"resources.database_instances.mip_app_state": fx.instance_update()},
    ),
    ids=("delete", "instance-update"),
)
def test_a_gate_refusal_exits_2_before_the_lease(
    tmp_path: Path, phase: str, entries: dict[str, object]
) -> None:
    result, called, lease = _harness(tmp_path, fx.with_entries(fx.adopted(), entries), phase=phase)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "the bundle plan gate refused (pre-lease)" in result.stderr
    assert "[mip-plan-gate] REFUSED " in result.stderr
    assert not lease.exists() and "reached-after-block" not in result.stdout
    assert called == ["bundle_env.called"]
    assert not list(tmp_path.glob("mip-bundle-plan.*")), "the raw plan is removed on a refusal too"


def test_a_plan_that_cannot_be_computed_exits_2_before_the_gate(tmp_path: Path) -> None:
    result, _called, lease = _harness(tmp_path, "", phase="plan", bundle_rc=2)

    assert result.returncode == 2
    assert "the bundle plan could not be computed (pre-lease)" in result.stderr
    assert "[mip-plan-gate]" not in result.stdout + result.stderr
    assert not lease.exists()


APP_NAME = fx.names_of("apps", fx.APP_KEY)[0]


def _workspace_and_journal_doubles(repo: Path, journal: str) -> None:
    """The gate's own workspace (the App exists, the caller is an admin) and journal read."""

    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    (package / "provision_runtime_secrets.py").write_text(
        "from types import SimpleNamespace\n"
        "def bound_workspace():\n"
        "    me = SimpleNamespace(groups=[SimpleNamespace(display='admins')])\n"
        "    app = SimpleNamespace(name=" + repr(APP_NAME) + ")\n"
        "    workspace = SimpleNamespace(\n"
        "        current_user=SimpleNamespace(me=lambda: me),\n"
        "        apps=SimpleNamespace(get=lambda name: app),\n"
        "    )\n"
        "    return workspace, 'https://workspace.example.invalid'\n",
        encoding="utf-8",
    )
    (package / "app_first_install_journal.py").write_text(
        "def recovery_state(workspace, *, app_name, rollback_scope, expected_lakebase_instance):\n"
        f"    open({str(repo / 'journal.read')!r}, 'w').write(\n"
        "        ' '.join((app_name, rollback_scope, expected_lakebase_instance))\n"
        "    )\n"
        f"    return {journal!r}\n",
        encoding="utf-8",
    )


def _retry_after_an_interrupted_first_install() -> dict[str, object]:
    """Every non-App resource is in state; the App was created but never bound."""

    return fx.with_entries(
        fx.adopted(),
        {"resources.apps.mip_app": fx.entry("create", value=fx.resolved_name("apps", fx.APP_KEY))},
    )


@pytest.mark.parametrize("state", ("unclaimed", "recover"))
def test_a_journal_owned_app_reaches_the_lease_and_its_recovery(
    tmp_path: Path, state: str
) -> None:
    result, called, lease = _harness(
        tmp_path, _retry_after_an_interrupted_first_install(), phase="", journal=state
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"[mip-plan-gate] DEFERRED journal_owned apps.mip_app ({state}): " in result.stdout
    assert "reached-after-block" in result.stdout and lease.exists()
    assert called == ["app_deployment_lease.called", "bundle_env.called"]
    assert (tmp_path / "repo" / "journal.read").read_text(encoding="utf-8") == (
        f"{APP_NAME} synthetic-rollback-scope synthetic-instance"
    ), "the gate read the journal the deploy names"


@pytest.mark.parametrize("state", ("absent", "signed"))
def test_an_app_no_journal_owns_still_refuses_before_the_lease(tmp_path: Path, state: str) -> None:
    result, called, lease = _harness(
        tmp_path, _retry_after_an_interrupted_first_install(), phase="", journal=state
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "[mip-plan-gate] REFUSED not_adopted apps.mip_app" in result.stderr
    assert "DEFERRED" not in result.stdout
    assert not lease.exists() and called == ["bundle_env.called"]


def test_the_harness_runs_the_real_gate(tmp_path: Path) -> None:
    # A positive control: a malformed plan reaches the copied gate, not a stub.
    result, _called, _lease = _harness(tmp_path, '{"plan_version": 1}', phase="plan")

    assert result.returncode == 2
    assert (
        "REFUSED malformed_plan bundle.plan (plan_version 1 is not the reviewed version 2)"
        in result.stderr
    )
