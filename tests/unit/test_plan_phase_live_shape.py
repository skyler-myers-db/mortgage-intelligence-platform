"""A live ``--phase plan`` (DRY_RUN=0) runs only read-only tools and no Genie turn (M1-PR6).

The sandbox phase matrix (tests/unit/test_governed_phases_sandbox.py) runs
every phase with ``--dry-run``, where no Databricks command runs at all, so it
cannot show what a live plan phase runs. This harness sources the real
automation step file (scripts/lib/deploy_step_automation_identities.sh) and
the real governed phases library with ``DRY_RUN=0`` and a recording
``$PYTHON``, the way tests/unit/test_deploy_prestop_probes.py drives the
pre-stop probe. It pins the exact tools the live plan phase runs after the
preflight, each a read-only one, the probe's ``--genie-turn off`` (its one
call that is not a GET or a list is the Genie turn, which opens a
conversation), and that it exits before the signed lease. A release and a run
with no ``--phase`` still ask the turn. The whole live plan, preflight
included, is tests/unit/test_plan_phase_live_sandbox.py's.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AUTOMATION_STEP = REPO / "scripts" / "lib" / "deploy_step_automation_identities.sh"
PHASES = REPO / "scripts" / "lib" / "deploy_governed_phases_lifecycle.sh"
PLAN_REPORT = REPO / "scripts" / "lib" / "deploy_plan_report_lifecycle.sh"
# Every command a live --phase plan runs in this step file, in order: local
# parses and renders, the read-only bundle validate and plan gate, the probes
# without their Genie turn, the pii-salt listing and each phase's budget.
PLAN_CALLS = (
    "tools.databricks.agent_proxy_credential_bundle all-fields",  # parses the bundle variable
    "-c import dotenv, pydantic",  # the interpreter check
    "tools/render_sql.py --catalog mip",  # a local render
    "tools.databricks.bundle_env validate -t client",
    "- ",  # a local heredoc: the per-run identities are pairwise distinct
    "- ",  # a local heredoc: the admin-scope identities
    "tools.databricks.deploy_prestop_probes --app-name mip-app",
    "tools.databricks.pii_salt_scope check",
    "run_bundle_plan_gate pre-lease",
    "tools.databricks.phase_budget describe --phase plan",
    "tools.databricks.phase_budget describe --phase release",
    "tools.databricks.phase_budget describe --phase data",
    # M1-PR7: the active deployment's digest (one apps.get) a release may pin.
    "tools.databricks.active_deployment_pin digest --app-name mip-app",
)
# Under GitHub Actions, with a gate report: the PlanReport's public half to the
# step summary (a local file) and its full half to the deployer's own
# plan-reports folder, create-only: the plan phase's one workspace write.
PLAN_PUBLISH_CALLS = (
    "tools.databricks.plan_report_publish summary --public",
    "tools.databricks.plan_report_publish publish --full",
)


def _drive(tmp_path: Path, phase: str, *, actions: bool = False) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "calls.log"
    fake_python = tmp_path / "fake-python.sh"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        'case "$*" in\n'
        "  *'agent_proxy_credential_bundle all-fields'*)\n"
        "    printf 'proxy-client\\tproxy-credential\\tproxy-secret\\n'; exit 0 ;;\n"
        "  *deploy_prestop_probes*|*pii_salt_scope*|*phase_budget*|*active_deployment_pin*|*plan_report_publish*) exit 0 ;;\n"
        "esac\n"
        "cat >/dev/null\n"
        "printf 'admin-client,release-probe-client\\n'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    gate_report = (
        f'BUNDLE_PLAN_REPORT_JSON={shlex.quote(str(tmp_path / "report.json"))}; '
        f'BUNDLE_PLAN_PUBLIC_JSON={shlex.quote(str(tmp_path / "report.public.json"))};'
        if actions
        else ":;"
    )
    actions_env = (
        f"GITHUB_ACTIONS=true GITHUB_RUN_ID=123 GITHUB_RUN_ATTEMPT=1 GITHUB_STEP_SUMMARY={shlex.quote(str(tmp_path / 'summary.md'))}\n"
        "CLIENT_DEPLOYER=deployer-application-id CLIENT_KEY=client-one"
        if actions
        else ""
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
cd {shlex.quote(str(REPO))}
export TMPDIR={shlex.quote(str(tmp_path))}
DRY_RUN=0
PYTHON={shlex.quote(str(fake_python))}
RED=""; RST=""; GRN=""; YLW=""; DIM=""
step() {{ echo "[step] $*"; }}
run() {{ "$@"; }}
run_with_proof_signing_authority() {{ printf 'SIGNING %s\\n' "$*" >> {shlex.quote(str(calls))}; "$@"; }}
run_with_account_identity() {{ "$@"; }}
resolve_m2m_credential() {{ :; }}
deployment_control_value() {{ printf ''; }}
same_identity_casefold() {{ [[ "$1" == "$2" ]]; }}
start_proof_signing_heartbeat() {{ printf 'heartbeat\\n' >> {shlex.quote(str(calls))}; }}
dotenv_value() {{ printf ''; }}
run_bundle_plan_gate() {{ printf 'run_bundle_plan_gate %s\\n' "$*" >> {shlex.quote(str(calls))}; {gate_report} }}
TARGET=client
CLIENT_INSTALL_CLASS=demo
CLIENT_DEMO_FEEDS=1
CLIENT_AGENTIC_TIER=on
CLIENT_LEASE_WRITER_ENV=DATABRICKS_AGENT_RUNTIME_CLIENT_ID
tier_is_agentic() {{ [[ "$CLIENT_AGENTIC_TIER" == "on" ]]; }}
DEPLOY_PHASE={shlex.quote(phase)}
SKIP_SILVER=0
DEPLOY_INVENTORY_PRINCIPAL=deployer@example.invalid
MIP_PII_SALT_SCOPE=mip
PII_SALT_REQUIRE_EXISTING=0
APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback
MIP_LAKEBASE_INSTANCE=mip-app-state
MIP_GENIE_SPACE_NAME="Mortgage Lead Intelligence"
DATABRICKS_CLIENT_ID=normal-client DATABRICKS_CLIENT_SECRET=s1
DATABRICKS_OPERATOR2_CLIENT_ID=operator2-client
DATABRICKS_ADMIN_CLIENT_ID=admin-client DATABRICKS_ADMIN_CLIENT_SECRET=s2
DATABRICKS_RELEASE_PROBE_CLIENT_ID=release-probe-client
DATABRICKS_RELEASE_PROBE_CLIENT_SECRET=s3
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client DATABRICKS_VERIFIER_CLIENT_SECRET=s4
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=s5
DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE=bundle
DATABRICKS_ACCOUNT_HOST=https://accounts.cloud.databricks.com
DATABRICKS_ACCOUNT_ID=account
DATABRICKS_ACCOUNT_CLIENT_ID=account-client DATABRICKS_ACCOUNT_CLIENT_SECRET=s6
databricks() {{ printf 'databricks %s\\n' "$*" >> {shlex.quote(str(calls))}; return 97; }}
{actions_env}
source {shlex.quote(str(PHASES))}
source {shlex.quote(str(PLAN_REPORT))}
source {shlex.quote(str(AUTOMATION_STEP))}
echo PAST-THE-STEP-FILE
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, check=False
    )
    return result, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def _tool(call: str) -> str:
    return call.removeprefix("-m ")


def test_a_live_plan_phase_runs_only_read_only_tools_and_no_genie_turn(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, "plan")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "plan phase complete: stopped before the lease; no governed resource was changed" in result.stdout
    assert "PAST-THE-STEP-FILE" not in result.stdout
    tools = [_tool(call) for call in calls]
    assert len(tools) == len(PLAN_CALLS) and all(map(str.startswith, tools, PLAN_CALLS)), tools
    probe = next(tool for tool in tools if tool.startswith("tools.databricks.deploy_prestop_probes"))
    assert "--genie-turn off" in probe, "no Genie conversation in a plan"
    assert not any(call.startswith(("SIGNING", "heartbeat", "databricks")) for call in calls), calls
    assert not any("app_deployment_lease" in call for call in calls)


def test_a_live_plan_phase_under_actions_publishes_both_report_halves_and_nothing_else(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, "plan", actions=True)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    tools = [_tool(call) for call in calls]
    assert len(tools) == len(PLAN_CALLS) + len(PLAN_PUBLISH_CALLS), tools
    assert all(map(str.startswith, tools, (*PLAN_CALLS, *PLAN_PUBLISH_CALLS))), tools
    publish = tools[-1]
    assert "--principal deployer-application-id --client client-one --run-id 123 --run-attempt 1" in publish
    assert not any(call.startswith(("SIGNING", "heartbeat", "databricks")) for call in calls), calls


@pytest.mark.parametrize("phase", ("", "release"))
def test_a_release_or_a_run_with_no_phase_still_asks_the_genie_turn(tmp_path: Path, phase: str) -> None:
    _result, calls = _drive(tmp_path, phase)
    probe = next(call for call in calls if "tools.databricks.deploy_prestop_probes" in call)
    assert "--genie-turn" not in probe, "the probe's default: one real turn before the lease and the stop"
