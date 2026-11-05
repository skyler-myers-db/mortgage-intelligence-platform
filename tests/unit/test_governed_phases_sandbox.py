"""The governed phases, driven offline end to end (M1-PR6): plan, release, data and no phase.

Each run is ``scripts/deploy.sh --dry-run`` in the sandbox clone
(tests/fixtures/deploy_sandbox.py), whose stub ``databricks`` fails on
anything but ``--version``, so the stub's log is the spy: no phase makes a
Databricks call in a dry run. The matrix:

* **release** is the run with no ``--phase`` minus exactly the data jobs
  (silver, gold and its grant reconcile, the stale-rate guard, the lifecycle
  sync, the KPI snapshot), plus three documented banners: its budget
  admission, the deferral and, on a first install, the owner-bootstrap gate
  between the App's creation and its first identity proof.
* **data** runs only the job phase runner, the reconcile and verify after each
  gold-touching job, and the data smoke: no bundle plan or apply, no App tool
  and no ``databricks apps`` command, no pre-stop probe.
* **plan** plans only read-only commands and predicts every phase's budget.
* **no phase** prints none of the phase banners and keeps every data step, in
  today's order (the base-vs-head transcript identity is the PR's proof).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import core_tier_profile, env_file_text
from tests.fixtures.deploy_sandbox import ANSI, planned_commands, sandbox

DATA_JOB_COMMANDS = (
    "databricks bundle run mip_fred_rates_ingest",
    "databricks bundle run mip_refresh_silver",
    "databricks bundle run mip_refresh_scores",
    "databricks bundle run mip_kpi_snapshot",
    "databricks bundle run mip_scheduled_refresh",
    "tools.sync_lifecycle_warehouse",
    "tools.databricks.verify_market_rate_alignment",
    "tools.databricks.job_phase_runner",
)
RELEASE_BANNERS = (
    "admit the release against the job deadline (read-only)",
    "data jobs deferred to --phase data",
    "first install: prove the owner bootstrap --post-first-install before any App-identity proof",
)
APP_TOOLS = (
    "app_release_source",
    "converge_static_app_source",
    "app_deployment_rollback",
    "app_resource_bindings",
    "provision_m2m_oauth",
    "converge_lakebase_oauth_role",
    "converge_app_",
    "app_first_install_journal",
    "first_install_bootstrap",
    "deploy_prestop_probes",
    "provision_genie_space",
    "quiesce_window",
    "bundle_env",
    "app_deployment_lease acquire",
)
# Every command --phase plan may plan: none of them writes.
PLAN_READ_ONLY = (
    "tools.clients.live_checks",
    "tools/render_sql.py",
    "tools.databricks.bundle_env validate",
    "tools.databricks.bundle_env plan",
    "tools.databricks.bundle_plan_gate check",
    "tools.databricks.monitor_scheduler_gate check",
)


def _deploy(checkout: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/deploy.sh", "-t", "client", "--dry-run", "--no-confirm", "--skip-smoke", *args],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )


# ``mktemp -t mip-x.XXXXXX.env`` names its file two ways. BSD (macOS) keeps
# the template and appends a random tail: ``mip-x.XXXXXX.env.AbC123de``. GNU
# (the Linux CI runner) replaces the X's: ``mip-x.lwE9vH.env``. Both become
# ``mip-x.<random>.env``; nothing else in a command is rewritten.
_MKTEMP_NAME = re.compile(
    r"(/mip-[a-z0-9-]+)\."
    r"(?:XXXXXX(?P<bsd_ext>\.[a-z]+)?\.[A-Za-z0-9]+|[A-Za-z0-9]{6}(?P<gnu_ext>\.[a-z]+)?)"
    r"(?=[\s'\",;:)]|$)"
)


def _mktemp_normal(match: re.Match[str]) -> str:
    return f"{match.group(1)}.<random>{match.group('bsd_ext') or match.group('gnu_ext') or ''}"


def _normal(commands: list[str]) -> list[str]:
    """Each sandbox commits its own overlay: its sha and mktemp names differ, nothing else."""

    out = []
    for command in commands:
        command = re.sub(r"\b[0-9a-f]{40}\b", "<sha>", command)
        command = re.sub(r"\b[0-9a-f]{12}(-dry-run)?\b", "<release>", command)
        out.append(_MKTEMP_NAME.sub(_mktemp_normal, command))
    return out


def _steps(stdout: str) -> list[str]:
    return [re.sub(r"^\[deploy\] step \d+: ", "", line) for line in ANSI.sub("", stdout).splitlines() if re.match(r"\[deploy\] step \d+: ", line)]


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, subprocess.CompletedProcess[str]]:
    """One sandbox per phase (and a core-tier release), each with its stub log."""

    results: dict[str, subprocess.CompletedProcess[str]] = {}
    for name, args in (
        ("none", ()),
        ("release", ("--phase", "release")),
        ("data", ("--phase", "data")),
        ("data-skip-silver", ("--phase", "data", "--skip-silver")),
        ("plan", ("--phase", "plan")),
    ):
        checkout, env, log = sandbox(tmp_path_factory.mktemp(name))
        result = _deploy(checkout, env, *args)
        assert result.returncode == 0, name + result.stdout[-3000:] + result.stderr[-3000:]
        assert log.read_text(encoding="utf-8").splitlines() == ["--version"], f"{name}: no Databricks call"
        results[name] = result
    return results


def test_the_release_is_the_full_run_minus_the_data_jobs_plus_its_banners(runs: dict[str, subprocess.CompletedProcess[str]]) -> None:
    full, release = runs["none"], runs["release"]
    full_commands, release_commands = _normal(planned_commands(full.stdout)), _normal(planned_commands(release.stdout))
    removed = [command for command in full_commands if any(job in command for job in DATA_JOB_COMMANDS)]
    # FRED, silver, gold, the stale-rate guard, the lifecycle sync and the KPI
    # snapshot; the gold refresh's reconcile goes with its job.
    assert len(removed) == 6, removed
    reconcile = [i for i, c in enumerate(full_commands) if "verify_reviewed_function_execute_grants" in c]
    gold = next(i for i, c in enumerate(full_commands) if "bundle run mip_refresh_scores" in c)
    after_gold = [i for i in reconcile if i > gold][0]
    expected = [c for i, c in enumerate(full_commands) if c not in removed and i != after_gold]
    assert release_commands == expected, "release = the full run minus exactly the data jobs"
    assert not any(job in command for command in release_commands for job in DATA_JOB_COMMANDS)

    full_steps, release_steps = _normal(_steps(full.stdout)), _normal(_steps(release.stdout))
    banners = [step for step in release_steps if step not in full_steps]
    assert [b for b in banners if not b.startswith(RELEASE_BANNERS)] == [], banners
    assert len(banners) == 3 and all(any(b.startswith(prefix) for b in banners) for prefix in RELEASE_BANNERS)
    assert not any(step.startswith(RELEASE_BANNERS) for step in full_steps), "no banner without a phase"


def test_a_first_install_release_proves_the_owner_bootstrap_after_creation_before_any_identity_proof(
    runs: dict[str, subprocess.CompletedProcess[str]],
) -> None:
    out = ANSI.sub("", runs["release"].stdout)
    bind = out.index("deployment bind mip_app mip-app -t client")
    gate = out.index("first install: prove the owner bootstrap --post-first-install")
    role = out.index("converge App Lakebase OAuth role to exact LOGIN-only profile")
    assert bind < gate < role
    assert "until it does the release stops here with exit 3 (awaiting owner bootstrap --post-first-install)" in out
    assert "first install: prove the owner bootstrap" not in ANSI.sub("", runs["none"].stdout)


def test_the_data_phase_runs_only_its_jobs_and_never_touches_the_app(runs: dict[str, subprocess.CompletedProcess[str]]) -> None:
    for name, jobs in (("data", ["mip_refresh_silver", "mip_scheduled_refresh"]), ("data-skip-silver", ["mip_scheduled_refresh"])):
        result = runs[name]
        commands = planned_commands(result.stdout)
        assert not any(tool in command for command in commands for tool in APP_TOOLS[:-1] if "bundle_env validate" not in command), commands
        assert not any(re.search(r"(?:^|\s)databricks\s+apps\s", command) for command in commands)
        assert "tools.databricks.app_deployment_lease acquire" not in result.stdout
        runner = [command.split()[3] for command in commands if "tools.databricks.job_phase_runner" in command]
        assert runner == ["plan", "begin", *(["run"] * len(jobs)), "close"]
        keys = re.findall(r"job_phase_runner run .*?--job-key (\S+)", "\n".join(commands))
        assert keys == jobs
        # After each gold-touching job: the reconcile, then its read-only verify (attack O11).
        out = ANSI.sub("", result.stdout)
        for job in jobs:
            run = out.index(f"--job-key {job}")
            reconcile = out.index(f"reconcile and prove reviewed function EXECUTE grants after {job}", run)
            verify = out.index("verify_reviewed_function_execute_grants", reconcile)
            assert run < reconcile < verify
        assert out.index("data smoke: gold scored at the rate silver publishes as latest") > verify
        assert "pre-stop probes" not in out and "plan direct deployment" not in out
        assert "would read: databricks apps get mip-app -o json" in out, "its one App read, read-only"
        assert out.rstrip().endswith("the App was not touched.")


def test_the_plan_phase_plans_only_read_only_commands_and_every_phase_budget(runs: dict[str, subprocess.CompletedProcess[str]]) -> None:
    result = runs["plan"]
    commands = planned_commands(result.stdout)
    assert commands and all(command.split(" -m ")[-1].startswith(PLAN_READ_ONLY) or "render_sql.py" in command for command in commands), commands
    out = ANSI.sub("", result.stdout)
    for phase in ("plan", "release", "data"):
        assert re.search(rf"phase budget: {phase} predicts \d+ min \(limit 330, margin 15\)", out), phase
    assert "app_deployment_lease" not in result.stdout


def test_no_phase_keeps_every_data_step_in_todays_order(runs: dict[str, subprocess.CompletedProcess[str]]) -> None:
    out = ANSI.sub("", runs["none"].stdout)
    order = [
        "refresh silver — FRED MORTGAGE30US rates",
        "refresh silver — Cotality share",
        "refresh gold — borrower_360",
        "reconcile and prove reviewed function EXECUTE grants after governed job",
        "smoke — gold market rate matches silver is_latest",
        "sync lifecycle state from Lakebase",
        "record headline KPI snapshot",
        "verify mip_scheduled_refresh",
        "rebind Genie space",
    ]
    positions = [out.index(step, out.index("deploy first-install Databricks App snapshot")) for step in order]
    assert positions == sorted(positions)
    for banner in (*RELEASE_BANNERS, "data phase", "phase budget:"):
        assert banner not in out, banner


def test_a_core_tier_release_refuses_like_a_run_with_no_phase(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)
    values = {**core_tier_profile(), "DATABRICKS_WAREHOUSE_ID": "warehouse-1", "GENIE_SPACE_ID": "space-1"}
    (checkout / ".env.local").write_text(env_file_text(values), encoding="utf-8")
    for args in ((), ("--phase", "release")):
        result = _deploy(checkout, env, *args)
        assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
        assert "the core tier (MIP_AGENTIC_TIER=off) cannot release yet" in result.stderr
        assert "admit the release" not in result.stdout, "refused before the admission and the lease"
    # The data phase never activates anything: the core tier runs it.
    data = _deploy(checkout, env, "--phase", "data")
    assert data.returncode == 0, data.stdout[-2000:] + data.stderr[-2000:]
    assert "--agent-runtime-application-id" not in ANSI.sub("", data.stdout), "the core tier reconciles the App's grants only"
