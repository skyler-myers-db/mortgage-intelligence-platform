"""The tier decides every runtime-identity site of the deploy's plan (M2-PR3).

``scripts/deploy.sh -t client --dry-run`` in the offline sandbox
(tests/fixtures/deploy_sandbox.py):

* **agentic tier** (client #1's shape): every tier-aware site plans exactly
  what it planned before this change. The lines pinned here are copied from
  the origin/main transcript the change was proven against (its full stdout
  hashes equal; see the PR), so a site that stops planning its agentic
  identity fails here. No core-only line appears.
* **core tier**: the plan names no verifier, runtime or proxy anywhere, grants
  and proves only the App, skips the agentic identity audits, and stops at
  the activation boundary with exit 2 (a live run would stop there too).
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
CORE_ONLY = ("agentic tier: off", "core tier", "--agentic-tier", "not used by the core tier")


def _plan(tmp_path: Path, profile: dict[str, str] | None = None) -> tuple[int, str, str]:
    checkout, env, _log = sandbox(tmp_path)
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
        assert any(expected in line for line in lines), expected
    for marker in CORE_ONLY:
        assert marker not in out, marker


def test_the_core_tier_plans_no_agentic_identity_and_stops_at_the_activation_boundary(tmp_path: Path) -> None:
    code, out, err = _plan(tmp_path, core_tier_profile())
    assert code == 2, out[-3000:] + err[-3000:]
    assert "the core tier (MIP_AGENTIC_TIER=off) stops at the activation boundary" in err
    assert "agentic tier: off (core: the normal, operator2, admin and release-probe identities only)" in out
    assert "  Gateway model attestation: not used by the core tier" in out
    # (The post-lease skips print only in a live run: tests/unit/test_core_tier_release_sandbox.py.)
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
    grants = [line for line in out.splitlines() if line.startswith("  would grant: ")]
    assert grants and all(line.endswith("TO `dry-run-app-client-id`") for line in grants), grants
    assert (
        "$ .venv/bin/python -m tools.databricks.verify_reviewed_function_execute_grants --catalog mip "
        "--app-application-id dry-run-app-client-id --agentic-tier off"
    ) in out
    namespace = next(line for line in out.splitlines() if "ensure_pipeline_namespace" in line)
    assert namespace.count("--forbidden-owner-principal") == 4
    # Nothing past the boundary is planned.
    assert "app_release_source publish" not in out and "provision_agentic_resources" not in out
