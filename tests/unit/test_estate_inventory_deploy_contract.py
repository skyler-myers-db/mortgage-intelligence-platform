"""The deploy wiring of the estate inventory: agentic tier only, before the lease (M4-PR1).

``scripts/lib/deploy_estate_inventory_lifecycle.sh`` retires
``MIP_UC_FOREIGN_CATALOG_BINDING_POLICY`` (refused when set in the agentic
tier, one notice in the core tier, which never audited with it) and runs the
read-only pre-lease inventory check in the agentic tier only. The core tier
neither runs the foreign UC audit nor needs an inventory: its dry run plans
nothing new, and every audit call sits on an agentic-only path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import core_tier_profile
from tests.fixtures.deploy_script import REPO, deploy_contract_text
from tests.unit.test_agentic_tier_dry_run import _plan

LIB = REPO / "scripts" / "lib" / "deploy_estate_inventory_lifecycle.sh"
AUDIT = "-m tools.databricks.audit_agent_runtime_foreign_uc_access"
POLICY = '{"version":1,"catalogs":{}}'


def _call(function: str, tmp_path: Path, *, tier: str, policy: str = "", dry_run: int = 0, phase: str = "",
          check_rc: int = 0) -> subprocess.CompletedProcess[str]:  # fmt: skip
    python = tmp_path / "python"
    python.write_text(f'#!/usr/bin/env bash\necho "PY $*" >> "{tmp_path}/calls"\nexit {check_rc}\n', encoding="utf-8")
    python.chmod(0o755)
    script = f"""
set -euo pipefail
RED="" RST=""
DRY_RUN={dry_run}
DEPLOY_PHASE="{phase}"
PYTHON="{python}"
MIP_DEFAULT_CATALOG=mip
CLIENT_AGENTIC_TIER="{tier}"
tier_is_agentic() {{ [[ "$CLIENT_AGENTIC_TIER" == "on" ]]; }}
step() {{ echo "==> $*"; }}
deployment_control_value() {{ printf '%s' "${{!1:-}}"; }}
export MIP_UC_FOREIGN_CATALOG_BINDING_POLICY='{policy}'
. "{LIB}"
{function}
echo "after: policy=${{MIP_UC_FOREIGN_CATALOG_BINDING_POLICY-unset}}"
"""
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False, timeout=60)


def _calls(tmp_path: Path) -> str:
    path = tmp_path / "calls"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_the_retired_policy_refuses_when_set_in_the_agentic_tier(tmp_path: Path) -> None:
    result = _call("retire_foreign_catalog_binding_policy", tmp_path, tier="on", policy=POLICY)

    assert result.returncode == 2
    assert "MIP_UC_FOREIGN_CATALOG_BINDING_POLICY is retired" in result.stderr
    assert "MIP_ESTATE_INVENTORY_JSON" in result.stderr and "MIP_ACK_ESTATE_INVENTORY" in result.stderr
    assert POLICY not in result.stdout + result.stderr


def test_the_retired_policy_is_one_notice_in_the_core_tier_and_never_exported(tmp_path: Path) -> None:
    noticed = _call("retire_foreign_catalog_binding_policy", tmp_path, tier="off", policy=POLICY)
    silent = [
        _call("retire_foreign_catalog_binding_policy", tmp_path, tier=tier, policy=value)
        for tier in ("on", "off")
        for value in ("", "   ")
    ]

    assert noticed.returncode == 0
    assert noticed.stdout.splitlines() == [
        "  retired: MIP_UC_FOREIGN_CATALOG_BINDING_POLICY is set but unused (the core tier runs no "
        "foreign UC audit); unset it",
        "after: policy=unset",
    ]
    for result in silent:
        assert (result.returncode, result.stdout, result.stderr) == (0, "after: policy=unset\n", "")


def test_the_core_tier_runs_no_inventory_check(tmp_path: Path) -> None:
    for dry_run in (0, 1):
        result = _call("check_estate_inventory_prelease", tmp_path, tier="off", dry_run=dry_run)
        assert (result.returncode, result.stdout, result.stderr) == (0, "after: policy=\n", "")
    assert _calls(tmp_path) == ""


def test_the_agentic_tier_checks_the_inventory_before_the_lease(tmp_path: Path) -> None:
    planned = _call("check_estate_inventory_prelease", tmp_path, tier="on", dry_run=1)
    assert "would check read-only: MIP_ESTATE_INVENTORY_JSON or MIP_ESTATE_INVENTORY_FILE" in planned.stdout
    assert _calls(tmp_path) == ""

    checked = _call("check_estate_inventory_prelease", tmp_path, tier="on")
    assert checked.returncode == 0
    assert _calls(tmp_path) == "PY -m tools.databricks.estate.inventory_check --catalog mip\n"

    refused = _call("check_estate_inventory_prelease", tmp_path, tier="on", check_rc=2)
    assert refused.returncode == 2
    assert "refused before the lease" in refused.stderr


@pytest.mark.parametrize("phase", ["adopt", "unadopt"])
def test_the_adopt_phases_need_no_inventory(tmp_path: Path, phase: str) -> None:
    result = _call("check_estate_inventory_prelease", tmp_path, tier="on", phase=phase)

    assert (result.returncode, result.stdout) == (0, "after: policy=\n")
    assert _calls(tmp_path) == ""


def test_the_inventory_and_its_ack_are_never_read_from_dotenv() -> None:
    script = deploy_contract_text()

    for name in ("MIP_ACK_ESTATE_INVENTORY", "MIP_ESTATE_INVENTORY_JSON", "MIP_ESTATE_INVENTORY_FILE"):
        assert not re.search(rf"(deployment_control_value|nonblank_control_value|dotenv_value) {name}\b", script)
    assert "export MIP_UC_FOREIGN_CATALOG_BINDING_POLICY" not in script
    assert script.count("retire_foreign_catalog_binding_policy\n") == 1


def test_the_inventory_check_runs_before_the_lease_and_the_plan_exit() -> None:
    script = deploy_contract_text()
    call = script.index("\ncheck_estate_inventory_prelease\n")

    assert script.index('step "pre-stop probes:') < call
    assert call < script.index('if [[ "${DEPLOY_PHASE:-}" == "plan" ]]; then')
    assert call < script.index("app_deployment_lease recovery-root")


def test_every_audit_call_is_agentic_only_and_carries_the_self_view_credential() -> None:
    script = deploy_contract_text()
    boundary = script.index("# >>> core tier: activation boundary >>>")
    calls = [match.start() for match in re.finditer(re.escape(AUDIT), script)]

    assert len(calls) == 2
    early, provisioning = calls
    # Before the lease boundary only inside the agentic branch; the other after the core stop.
    branch = script.rindex("if tier_is_agentic; then", 0, early)
    assert script.index("core tier: no agent-runtime UC audit", branch) > early
    assert boundary < provisioning
    for start in calls:
        invocation = script[script.rindex("run_with_account_identity", 0, start) : script.index("\n", start)]
        assert "run_with_agent_runtime_credentials" in invocation
        assert "--exposure-identity runtime" in script[start : script.index("\nstep", start)]


def test_a_core_dry_run_plans_no_inventory_and_no_audit(tmp_path: Path) -> None:
    code, out, err = _plan(tmp_path, core_tier_profile())

    assert code == 2, out[-2000:] + err[-2000:]
    for absent in ("estate inventory", "inventory_check", "audit_agent_runtime_foreign_uc_access", "retired:"):
        assert absent not in out + err, absent


def test_an_agentic_dry_run_plans_the_inventory_check_and_the_self_view_credential(tmp_path: Path) -> None:
    code, out, err = _plan(tmp_path)

    assert code == 0, out[-2000:] + err[-2000:]
    assert "estate inventory: the owner's metastore inventory and its exact estate ack (read-only)" in out
    audits = [line for line in out.splitlines() if "audit_agent_runtime_foreign_uc_access" in line]
    assert audits and all("run_with_agent_runtime_credentials" in line for line in audits)
