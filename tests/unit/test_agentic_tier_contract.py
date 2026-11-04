"""The agentic tier's identity contract, from the profile to the deploy shell (M2-PR3).

* ``m2m_identity_contract``: the core tier deploys the four core roles, and
  the release probe writes its lease and is its Lakebase bootstrap control;
  the agentic tier adds the verifier, the runtime and the proxy and keeps
  today's writer and control (the runtime). Any other tier value refuses.
* ``run_with_lakebase_bootstrap_authority`` hands the convergence child that
  control's credential in either tier, and the one-use creator's own check
  (lakebase_oauth_role_bootstrap_control) accepts it for every role the tier
  converges.
* ``tools/clients``: the policy and the client-target view carry the tier and
  the contract's client-id names; the core tier's bundle variables bind no
  verifier and hand the migrate job its absence flag; the agentic tier's are
  exactly today's.
* ``resolve_client_profile`` (scripts/lib/deploy_identity_lifecycle.sh): a view
  with no tier, an unreviewed tier or a contract that disagrees with its tier
  refuses with exit 2, before any other deploy step.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile, paychex_shaped_profile
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.policy import install_policy
from tools.databricks import m2m_identity_contract as contract
from tools.databricks.lakebase_oauth_role_bootstrap_control import bootstrap_control_credentials

REPO = Path(__file__).resolve().parents[2]
CORE_ENVS = (
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_OPERATOR2_CLIENT_ID",
    "DATABRICKS_ADMIN_CLIENT_ID",
    "DATABRICKS_RELEASE_PROBE_CLIENT_ID",
)
AGENTIC_ENVS = (
    "DATABRICKS_VERIFIER_CLIENT_ID",
    "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
    "DATABRICKS_AGENT_PROXY_CLIENT_ID",
)


def test_the_core_tier_deploys_the_four_core_roles_with_the_release_probe_writing_the_lease() -> None:
    assert contract.required_roles("off") == ("normal", "operator2", "admin", "release_probe")
    assert contract.lease_writer("off") == "release_probe"
    assert contract.lakebase_bootstrap_control("off") == "release_probe"


def test_the_agentic_tier_keeps_todays_roster_and_writer() -> None:
    assert contract.required_roles("on") == (
        "normal",
        "operator2",
        "admin",
        "release_probe",
        "verifier",
        "agent_runtime",
        "agent_proxy",
    )
    assert contract.lease_writer("on") == "agent_runtime"
    assert contract.lakebase_bootstrap_control("on") == "agent_runtime"


def test_every_role_has_one_deploy_client_id_name_and_the_writer_is_never_the_deployer() -> None:
    assert set(contract.DEPLOY_CLIENT_ID_ENV) == set(contract.IDENTITY_DEFAULTS)
    assert len(set(contract.DEPLOY_CLIENT_ID_ENV.values())) == len(contract.DEPLOY_CLIENT_ID_ENV)
    for tier in contract.AGENTIC_TIERS:
        assert contract.lease_writer(tier) in contract.required_roles(tier)
        # The control is a tier identity that is never a converged Lakebase
        # role (the App, the jobs reader and the verifier).
        assert contract.lakebase_bootstrap_control(tier) in contract.required_roles(tier)
        assert contract.lakebase_bootstrap_control(tier) != "verifier"


@pytest.mark.parametrize("value", ["", "core", "agentic", "On", "OFF", " off", "1", "true"])
def test_an_unreviewed_tier_value_refuses(value: str) -> None:
    with pytest.raises(ValueError, match="MIP_AGENTIC_TIER must be exactly off or on"):
        contract.agentic_tier(value)
    with pytest.raises(ValueError):
        contract.required_roles(value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        contract.lease_writer(value)  # type: ignore[arg-type]


def test_the_policy_refuses_a_tier_the_schema_was_bypassed_for() -> None:
    forged = resolve(RawProfile(values=paychex_shaped_profile())).model_copy(update={"agentic_tier": "maybe"})
    with pytest.raises(ProfileRefusal, match="MIP_AGENTIC_TIER: must be exactly off or on"):
        install_policy(forged)


def test_the_core_views_carry_the_core_contract_and_no_verifier() -> None:
    profile = resolve(RawProfile(values=core_tier_profile()))
    target = views.client_target(profile)
    assert target["MIP_AGENTIC_TIER"] == "off"
    assert target["MIP_TIER_LEASE_WRITER"] == "DATABRICKS_RELEASE_PROBE_CLIENT_ID"
    assert target["MIP_TIER_IDENTITIES"] == ",".join(CORE_ENVS)
    assert target["BUNDLE_VAR_require_ai_gateway_verifier"] == "--ai-gateway-verifier-absent"
    variables = views.bundle_vars(profile)
    assert "ai_gateway_verifier_client_id" not in variables
    assert variables["require_ai_gateway_verifier"] == "--ai-gateway-verifier-absent"
    # The view names the agentic contract's identities, never their values (the
    # deployer and jobs ids are the target's own root and run_as variables).
    assert not set(target.values()) & set(IDENTITY_IDS.values()) - {IDENTITY_IDS["deployer"], IDENTITY_IDS["jobs"]}


def test_a_core_profile_that_still_carries_a_verifier_never_binds_it() -> None:
    profile = resolve(RawProfile(values=core_tier_profile(MIP_IDENTITY_IDS_JSON=json.dumps(IDENTITY_IDS))))
    assert "ai_gateway_verifier_client_id" not in views.bundle_vars(profile)


def test_the_agentic_views_are_todays_plus_the_tier_names() -> None:
    profile = resolve(RawProfile(values=paychex_shaped_profile()))
    target = views.client_target(profile)
    assert target["MIP_AGENTIC_TIER"] == "on"
    assert target["MIP_TIER_LEASE_WRITER"] == "DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
    assert target["MIP_TIER_IDENTITIES"] == ",".join((*CORE_ENVS, *AGENTIC_ENVS))
    assert "BUNDLE_VAR_require_ai_gateway_verifier" not in target, "the bundle default stays"
    variables = views.bundle_vars(profile)
    assert variables["ai_gateway_verifier_client_id"] == IDENTITY_IDS["verifier"]
    assert "require_ai_gateway_verifier" not in variables


def test_the_bundle_default_is_the_agentic_requirement_and_the_job_reads_the_variable() -> None:
    import yaml

    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    assert bundle["variables"]["require_ai_gateway_verifier"]["default"] == "--require-ai-gateway-verifier"
    task = bundle["resources"]["jobs"]["mip_lakebase_migrate"]["tasks"][0]
    parameters = task["spark_python_task"]["parameters"]
    verifier = parameters.index("--ai-gateway-verifier-client-id=${var.ai_gateway_verifier_client_id}")
    assert parameters[verifier + 1] == "${var.require_ai_gateway_verifier}"
    assert "--require-ai-gateway-verifier" not in parameters, "the flag comes from the variable alone"
    assert views.CORE_TIER_VERIFIER_FLAG == "--ai-gateway-verifier-absent"


# --------------------------------------------- the shell's view parser


def _resolve_in_shell(tmp_path: Path, view: str) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "python-stub.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'while [[ $# -gt 0 ]]; do if [[ "$1" == --out ]]; then out="$2"; fi; shift; done\n'
        f"cat > \"$out\" <<'VIEW'\n{view}VIEW\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    script = (
        "set -euo pipefail\n"
        "RED=''; RST=''\n"
        f"PYTHON={stub}\n"
        f". {REPO / 'scripts' / 'lib' / 'deploy_identity_lifecycle.sh'}\n"
        "resolve_client_profile\n"
        'echo "tier=$CLIENT_AGENTIC_TIER writer=$CLIENT_LEASE_WRITER_ENV"\n'
        'tier_has_identity DATABRICKS_RELEASE_PROBE_CLIENT_ID && echo "has release probe"\n'
        'tier_has_identity DATABRICKS_AGENT_RUNTIME_CLIENT_ID || echo "no runtime"\n'
    )
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False, timeout=60)


def _view(**entries: str | None) -> str:
    profile = resolve(RawProfile(values=core_tier_profile()))
    values: dict[str, str | None] = {**views.client_target(profile), **entries}
    return "".join(f"{name}='{value}'\n" for name, value in values.items() if value is not None)


def test_the_shell_reads_the_core_contract(tmp_path: Path) -> None:
    result = _resolve_in_shell(tmp_path, _view())
    assert result.returncode == 0, result.stderr
    assert "agentic tier: off (core: the normal, operator2, admin and release-probe identities only)" in result.stdout
    assert "tier=off writer=DATABRICKS_RELEASE_PROBE_CLIENT_ID" in result.stdout
    assert "has release probe" in result.stdout and "no runtime" in result.stdout


@pytest.mark.parametrize(
    "entries",
    [
        {"MIP_AGENTIC_TIER": None},
        {"MIP_AGENTIC_TIER": "core"},
        {"MIP_AGENTIC_TIER": "on"},
        {"MIP_TIER_LEASE_WRITER": "DATABRICKS_AGENT_RUNTIME_CLIENT_ID"},
        {"MIP_TIER_IDENTITIES": ",".join((*CORE_ENVS, *AGENTIC_ENVS))},
        {"BUNDLE_VAR_require_ai_gateway_verifier": None},
        {"BUNDLE_VAR_require_ai_gateway_verifier": "--require-ai-gateway-verifier"},
    ],
    ids=["no-tier", "unreviewed-tier", "tier-contract-mismatch", "writer", "roster", "no-flag", "wrong-flag"],
)
def test_a_view_without_a_reviewed_tier_contract_refuses(tmp_path: Path, entries: dict[str, str | None]) -> None:
    result = _resolve_in_shell(tmp_path, _view(**entries))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "carries no reviewed agentic tier" in result.stderr
    assert "tier=" not in result.stdout


def test_the_shell_reads_the_agentic_contract(tmp_path: Path) -> None:
    profile = resolve(RawProfile(values=paychex_shaped_profile()))
    view = "".join(f"{name}='{value}'\n" for name, value in views.client_target(profile).items())
    result = _resolve_in_shell(tmp_path, view)
    assert result.returncode == 0, result.stderr
    assert "agentic tier:" not in result.stdout, "the agentic transcript is unchanged"
    assert "tier=on writer=DATABRICKS_AGENT_RUNTIME_CLIENT_ID" in result.stdout
    assert "has release probe" in result.stdout and "no runtime" not in result.stdout


# ------------------------------------------- the Lakebase bootstrap control


_CONTROL_PROBE = (
    "import os, sys\n"
    "from tools.databricks.lakebase_oauth_role_bootstrap_control import bootstrap_control_credentials\n"
    "print(bootstrap_control_credentials(os.environ, target_application_id=sys.argv[1])[0])\n"
)


def _bootstrap_authority(tier: str, target: str, **credentials: str) -> subprocess.CompletedProcess[str]:
    """``run_with_lakebase_bootstrap_authority`` around the one-use creator's own control check."""

    script = (
        "set -euo pipefail\n"
        "RED=''; RST=''; DRY_RUN=0\n"
        f". {REPO / 'scripts' / 'lib' / 'deploy_identity_lifecycle.sh'}\n"
        'run_with_account_identity() { "$@"; }\n'
        'run_with_proof_signing_authority() { "$@"; }\n'
        f"CLIENT_AGENTIC_TIER={tier}\n"
        f'run_with_lakebase_bootstrap_authority python -c "$1" {target}\n'
        'echo "parent control=${MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID:-unset}"\n'
    )
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "PYTHONPATH": str(REPO), **credentials}
    return subprocess.run(
        ["bash", "-c", script, "bootstrap-authority", _CONTROL_PROBE],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        env=env,
    )


_CREDENTIALS = {
    "DATABRICKS_RELEASE_PROBE_CLIENT_ID": IDENTITY_IDS["release_probe"],
    "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET": "secret-release-probe-PLACEHOLDER",
    "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": IDENTITY_IDS["runtime"],
    "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET": "secret-runtime-PLACEHOLDER",
}


@pytest.mark.parametrize("tier", ["off", "on"])
@pytest.mark.parametrize("target", ["app-client-EXAMPLE", IDENTITY_IDS["jobs"]])
def test_the_converger_gets_the_tiers_control_and_its_create_check_accepts_it(tier: str, target: str) -> None:
    # A first install creates the App's and the jobs reader's roles in either
    # tier: the one-use creator refuses without a control, so each tier hands one.
    role = contract.lakebase_bootstrap_control(tier)  # type: ignore[arg-type]
    expected = _CREDENTIALS[contract.IDENTITY_DEFAULTS[role].client_id_secret_name]
    core_only = {name: value for name, value in _CREDENTIALS.items() if "RELEASE_PROBE" in name}
    result = _bootstrap_authority(tier, target, **(core_only if tier == "off" else _CREDENTIALS))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [expected, "parent control=unset"], "exposed to the child alone"


def test_a_core_converger_without_the_release_probe_refuses_up_front() -> None:
    result = _bootstrap_authority("off", "app-client-EXAMPLE")
    assert result.returncode == 2
    assert "fresh OAuth-M2M Lakebase bootstrap control credentials are missing" in result.stderr


def test_the_create_check_refuses_a_missing_control_or_one_that_is_its_target() -> None:
    with pytest.raises(RuntimeError, match="control credentials are required"):
        bootstrap_control_credentials({}, target_application_id="app-client-EXAMPLE")
    control = {
        "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID": IDENTITY_IDS["release_probe"],
        "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET": "secret-release-probe-PLACEHOLDER",
    }
    with pytest.raises(RuntimeError, match="must be distinct from the target"):
        bootstrap_control_credentials(control, target_application_id=IDENTITY_IDS["release_probe"])
