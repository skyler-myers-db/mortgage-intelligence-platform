"""The whole live ``--phase plan`` (DRY_RUN=0), preflight included, runs only read-only commands (M1-PR6).

tests/unit/test_plan_phase_live_shape.py pins what the automation step runs
after the preflight. This runs the real ``scripts/deploy.sh -t client --phase
plan`` from its first line, with ``DRY_RUN=0`` and ``GITHUB_ACTIONS=true`` (a
live phase is CI-only), in the offline release sandbox
(tests/fixtures/release_sandbox.py): its ``databricks`` CLI answers only what
it knows and exits 97 on anything else, and its deploy interpreter records
every call. The profile is the core tier, cutover run 1's.

It pins every Databricks CLI call (the version check alone) and every tool
the plan runs (each ``-m`` module or repository script, with the arguments
that make it read-only), in order, through the preflight, the probes without
their Genie turn, the pre-lease plan gate and the phase budgets. The plan
takes no lease and leaves no sandbox state. With no ``GITHUB_RUN_ID`` or
``GITHUB_STEP_SUMMARY`` it publishes no PlanReport (M1-PR7); that write is
pinned by tests/unit/test_plan_phase_live_shape.py. The local programs (``-c`` and
heredoc ``-`` bodies: interpreter, dotenv and profile checks) are recorded
by kind only; their text is the slices' own, reviewed with them, and each
runs for real against the offline fake estate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.client_profiles import core_tier_profile
from tests.fixtures.release_sandbox import Release, core_client_ids, first_install_estate, release

# Every tool a live core-tier --phase plan runs, in order (a prefix of its argv).
PLAN_TOOLS = (
    "tools.clients resolve --emit client-target",  # the profile, resolved locally
    "tools.python_deps_check",
    "tools.requirements_digest --check",
    "tools.databricks.app_runtime_closure check",
    "tools.clients.live_checks",  # read-only workspace checks
    "tools.databricks.bundle_state warehouse-id",
    "tools.frontend_deps_check",
    "tools.branding.lender_mark validate",
    "tools.databricks.provision_runtime_secrets --check",
    "tools.databricks.provision_genie_space --plan-pin",
    "tools/render_sql.py --catalog mip --storage-root-env",  # a local render
    "tools.databricks.bundle_env validate -t client",
    "tools.databricks.deploy_prestop_probes --app-name mip-app",
    "tools.databricks.pii_salt_scope check",
    "tools.databricks.bundle_env plan -t client -o json",
    "tools.databricks.catalog_bootstrap_skip_rule check",
    "tools.databricks.bundle_plan_gate check",
    "tools.databricks.monitor_scheduler_gate check",
    "tools.databricks.quiesce_window applicable",
    "tools.databricks.quiesce_window plan",
    "tools.databricks.phase_budget describe --phase plan",
    "tools.databricks.phase_budget describe --phase release",
    "tools.databricks.phase_budget describe --phase data",
    "tools.databricks.active_deployment_pin digest --app-name mip-app",  # one apps.get (M1-PR7)
    # B3: the activation's rollback proof, predicted read-only (a record read, no signing key).
    "tools.databricks.activation_prediction predict --phase plan --app-name mip-app",
    "tools/render_sql.py --catalog mip",  # the EXIT trap's local restore
)


def plan_phase(tmp_path: Path) -> Release:
    return release(
        tmp_path,
        core_tier_profile(),
        first_install_estate(),
        client_ids=core_client_ids(),
        flags=("--phase", "plan"),
        env={"GITHUB_ACTIONS": "true"},
    )


@pytest.fixture(scope="module")
def plan_run(tmp_path_factory: pytest.TempPathFactory) -> Release:
    return plan_phase(tmp_path_factory.mktemp("live-plan"))


def tools(run: Release) -> list[str]:
    """Each ``-m`` module or repository script the run handed the interpreter, with its arguments."""

    return [
        " ".join(call["argv"][1:] if call["argv"][:1] == ["-m"] else call["argv"])
        for call in run.python_calls
        if call["argv"][:1] == ["-m"] or call["argv"][0].startswith("tools/")
    ]


def _detail(run: Release) -> str:
    return run.result.stdout[-4000:] + run.result.stderr[-4000:]


def test_the_live_plan_phase_completes_before_the_lease(plan_run: Release) -> None:
    assert plan_run.result.returncode == 0, _detail(plan_run)
    assert "plan phase complete: stopped before the lease; no governed resource was changed" in plan_run.result.stdout
    assert "acquire signed workspace lease" not in plan_run.result.stdout
    assert plan_run.state == {}, "no lease, statement, bundle run or App create"


def test_every_databricks_cli_call_is_the_version_check(plan_run: Release) -> None:
    assert [call["argv"] for call in plan_run.cli_calls] == [["--version"]], _detail(plan_run)


def test_every_tool_the_live_plan_runs_is_a_pinned_read_only_one(plan_run: Release) -> None:
    ran = tools(plan_run)
    assert len(ran) == len(PLAN_TOOLS) and all(map(str.startswith, ran, PLAN_TOOLS)), ran
    probe = next(tool for tool in ran if tool.startswith("tools.databricks.deploy_prestop_probes"))
    assert "--genie-turn off" in probe and "--agentic-tier off" in probe
    gate = next(tool for tool in ran if tool.startswith("tools.databricks.bundle_plan_gate"))
    assert "--position pre-lease" in gate
    # Only argv[0] decides a call's kind: no call is left unclassified.
    kinds = {call["argv"][0] for call in plan_run.python_calls if call["argv"][:1] != ["-m"]}
    assert kinds <= {"-c", "-", "tools/render_sql.py"}, kinds
