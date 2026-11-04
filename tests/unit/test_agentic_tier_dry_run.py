"""The tier decides every runtime-identity site of the deploy's plan (M2-PR3).

``scripts/deploy.sh -t client --dry-run`` in the offline sandbox
(tests/fixtures/deploy_sandbox.py):

* **agentic tier** (client #1's shape): every tier-aware site plans exactly
  what it planned before this change. The lines pinned here are copied from
  the origin/main transcript the change was proven against (its full stdout
  hashes equal; see the PR), so a site that stops planning its agentic
  identity fails here. No core-only line appears.
* **core tier**: the plan names no verifier, runtime or proxy and needs no
  model-attestation key; after the read-only checks it refuses with exit 2
  before the lease, as a live run does (the core tier cannot release yet).
  The sites past the lease run for real in
  tests/unit/test_core_tier_release_sandbox.py, with that refusal excised.
"""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile, env_file_text
from tests.fixtures.deploy_sandbox import ANSI, dry_run, sandbox

RUNTIME = IDENTITY_IDS["runtime"]
# Lines of the agentic tier's dry-run plan, verbatim from origin/main (3b103e6d).
AGENTIC_LINES = (
    "  Gateway model attestation: separated model-provenance key configured",
    "  would probe read-only: the creator of Supervisor 'Mortgage Growth Agent' (MIP_RETIRE_HUMAN_SUPERVISOR), "
    "Genie group grants, the signed rollback record in mip-app-rollback, governed owners and owner-equivalent "
    "grantees, App serving access, and one Genie turn",
    "--forbidden-owner-principal  --forbidden-owner-principal  --forbidden-owner-principal  "
    f"--forbidden-owner-principal  --forbidden-owner-principal  --forbidden-owner-principal {RUNTIME} "
    "(bounded account-SCIM identity)",
    "$ run_with_proof_signing_authority .venv/bin/python -m tools.databricks.converge_lakebase_oauth_role "
    "--lakebase-instance mip-app-state --lakebase-database mip_app_state --application-id  --role-contract "
    "verifier --repair-legacy-replication (bounded account-SCIM identity)",
    "$ .venv/bin/python -m tools.databricks.provision_m2m_oauth --identity-role verifier --expected-application-id  "
    "--lakebase-instance mip-app-state --no-mint-secret",
    f"$ .venv/bin/python -m tools.databricks.provision_m2m_oauth --identity-role agent_runtime "
    f"--expected-application-id {RUNTIME} --no-mint-secret",
    "$ .venv/bin/python -m tools.databricks.provision_m2m_oauth --identity-role agent_proxy --expected-application-id  "
    "--no-mint-secret",
    f"  would grant: GRANT EXECUTE ON FUNCTION mip.gold.fn_build_cohort TO `{RUNTIME}`",
    "  would grant: GRANT EXECUTE ON FUNCTION mip.gold.fn_lead_queue_url TO ``",
    f"  would grant: GRANT CREATE MODEL ON SCHEMA mip.audit TO `{RUNTIME}`",
    f"$ .venv/bin/python -m tools.databricks.verify_reviewed_function_execute_grants --catalog mip "
    f"--app-application-id dry-run-app-client-id --agent-runtime-application-id {RUNTIME} "
    "--agent-proxy-application-id ",
)
CORE_ONLY = ("agentic tier: off", "core tier", "--agentic-tier")
# Profile names the sandbox does not strip (deploy_sandbox.INHERITED_PREFIXES):
# a shell value wins over .env.local, so an ambient one would change the plan.
_AMBIENT = ("LAKEBASE_", "APP_ENV")


def _plan(tmp_path: Path, profile: dict[str, str] | None = None) -> tuple[int, str, str]:
    checkout, env, _log = sandbox(tmp_path)
    env = {name: value for name, value in env.items() if not name.startswith(_AMBIENT)}
    if profile is not None:
        resources = {"DATABRICKS_WAREHOUSE_ID": "warehouse-1"}
        (checkout / ".env.local").write_text(env_file_text({**profile, **resources}), encoding="utf-8")
        env.pop("DATABRICKS_AGENT_RUNTIME_CLIENT_ID")
    result = dry_run(checkout, env)
    return result.returncode, ANSI.sub("", result.stdout), ANSI.sub("", result.stderr)


def test_the_agentic_tier_plans_every_site_as_before(tmp_path: Path) -> None:
    code, out, err = _plan(tmp_path)
    assert code == 0, out[-3000:] + err[-3000:]
    lines = out.splitlines()
    for expected in AGENTIC_LINES:
        near = [line for line in lines if expected.split(" --", 1)[0].strip() in line]
        assert any(expected in line for line in lines), (expected, near[:3])
    for marker in CORE_ONLY:
        assert marker not in out, marker


def test_the_core_tier_plans_no_agentic_identity_and_refuses_before_the_lease(tmp_path: Path) -> None:
    code, out, err = _plan(tmp_path, core_tier_profile())
    assert code == 2, out[-3000:] + err[-3000:]
    assert "the core tier (MIP_AGENTIC_TIER=off) cannot release yet" in err
    assert "agentic tier: off (core: the normal, operator2, admin and release-probe identities only)" in out
    # The core profile holds no model-attestation key, and none is required.
    assert "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY is required" not in err
    assert "Gateway model attestation:" not in out
    # Every read-only step was planned, then nothing past the lease.
    assert "would probe read-only:" in out
    assert "tools.databricks.bundle_plan_gate" in out
    assert "acquire signed workspace lease" not in out
    for absent in (
        "--identity-role verifier",
        "--identity-role agent_runtime",
        "--identity-role agent_proxy",
        "--role-contract verifier",
        "--agent-runtime-application-id",
        "CREATE MODEL",
        RUNTIME,
    ):
        assert absent not in out, absent
