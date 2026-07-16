"""Contracts for the manual dev deployment workflow."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"
DEPLOY_SCRIPT = REPO / "scripts" / "deploy.sh"


def _deploy_exit_trap_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("restore_rendered_sql_fail_closed() {")
    trap_line = "trap restore_rendered_sql_fail_closed EXIT"
    end = text.index(trap_line, start) + len(trap_line)
    return text[start:end]


def _deploy_auth_function_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("dotenv_value() {")
    end = text.index("# Step 0: preflight", start)
    return text[start:end]


def _read_env_log(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
    )


def _run_deploy_auth_harness(tmp_path: Path, *, mode: str) -> dict[str, Any]:
    repo = tmp_path / mode
    tools_dir = repo / "tools"
    home = repo / "home"
    tools_dir.mkdir(parents=True)
    home.mkdir()
    reviewed_host = "https://reviewed-workspace.example"
    env_lines = [
        f"DATABRICKS_HOST={reviewed_host}",
        "DATABRICKS_CLIENT_ID=normal-app-client",
        "DATABRICKS_CLIENT_SECRET=normal-app-secret",
        "DATABRICKS_ADMIN_CLIENT_ID=admin-app-client",
        "DATABRICKS_ADMIN_CLIENT_SECRET=admin-app-secret",
        "DATABRICKS_VERIFIER_CLIENT_ID=verifier-client",
        "DATABRICKS_VERIFIER_CLIENT_SECRET=verifier-secret",
    ]
    if mode == "pat":
        env_lines.append("DATABRICKS_TOKEN=reviewed-deployer-pat")
    (repo / ".env.local").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    (repo / "databricks.yml").write_text(
        "workspace:\n"
        f"  host: {reviewed_host}\n"
        "targets:\n"
        "  dev:\n"
        "    workspace:\n"
        f"      host: {reviewed_host}\n",
        encoding="utf-8",
    )
    (home / ".databrickscfg").write_text(
        "[DEFAULT]\n"
        "host = https://conflicting-default.example\n"
        "token = conflicting-default-token\n"
        "[REVIEWED]\n"
        f"host = {reviewed_host}\n"
        "token = reviewed-profile-token\n",
        encoding="utf-8",
    )
    mock_mint = tools_dir / "oauth_m2m_mint.py"
    mock_mint.write_text(
        """from __future__ import annotations
import json
import os
import sys
from pathlib import Path

with Path(os.environ["MINT_ENV_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(dict(os.environ)) + "\\n")
output = Path(sys.argv[sys.argv.index("--output-file") + 1])
output.write_text("minted-token\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )
    deploy_log = repo / "deploy.env"
    mint_log = repo / "mint.json"
    m2m_log = repo / "m2m.env"
    profile_export = "export DATABRICKS_CONFIG_PROFILE=REVIEWED" if mode == "profile" else ""
    harness = repo / "auth-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
RED=""
DIM=""
RST=""
DRY_RUN=0
TARGET=dev
PYTHON={shlex.quote(sys.executable)}
MINT_ENV_LOG={shlex.quote(str(mint_log))}
M2M_ENV_LOG={shlex.quote(str(m2m_log))}
export MINT_ENV_LOG M2M_ENV_LOG
{profile_export}
{_deploy_auth_function_block()}
bind_deployment_workspace_auth
resolve_m2m_credential DATABRICKS_CLIENT_ID shell
resolve_m2m_credential DATABRICKS_CLIENT_SECRET shell
resolve_m2m_credential DATABRICKS_ADMIN_CLIENT_ID
resolve_m2m_credential DATABRICKS_ADMIN_CLIENT_SECRET
resolve_m2m_credential DATABRICKS_VERIFIER_CLIENT_ID
resolve_m2m_credential DATABRICKS_VERIFIER_CLIENT_SECRET
export DATABRICKS_ACCOUNT_CLIENT_ID=hostile-account-client
export DATABRICKS_ACCOUNT_CLIENT_SECRET=hostile-account-secret
env | sort > {shlex.quote(str(deploy_log))}
mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
mint_m2m_token MIP_ADMIN_BEARER_TOKEN DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET
run_as_m2m_identity verifier DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET \
  bash -c 'env | sort > "$M2M_ENV_LOG"'
""",
        encoding="utf-8",
    )
    run_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "DATABRICKS_HOST": "https://ambient-wrong.example",
        "DATABRICKS_TOKEN": "ambient-wrong-token",
        "DATABRICKS_AUTH_TYPE": "pat",
    }
    result = subprocess.run(
        ["env", "-i", *(f"{key}={value}" for key, value in run_env.items()), "bash", str(harness)],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return {
        "deploy": _read_env_log(deploy_log),
        "mints": [json.loads(line) for line in mint_log.read_text(encoding="utf-8").splitlines()],
        "m2m": _read_env_log(m2m_log),
    }


def _run_bind_harness(
    tmp_path: Path,
    *,
    env_local: str,
    bundle_host: str,
    ambient: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    repo = tmp_path / "bind"
    bin_dir = repo / "bin"
    home = repo / "home"
    bin_dir.mkdir(parents=True)
    home.mkdir()
    (repo / ".env.local").write_text(env_local, encoding="utf-8")
    (repo / "databricks.yml").write_text(
        "targets:\n" "  dev:\n" "    workspace:\n" f"      host: {bundle_host}\n",
        encoding="utf-8",
    )
    mutation_marker = repo / "network-mutation-attempted"
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text(
        "#!/usr/bin/env bash\n" f"touch {shlex.quote(str(mutation_marker))}\n",
        encoding="utf-8",
    )
    fake_databricks.chmod(0o755)
    harness = repo / "bind-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
RED=""
RST=""
DRY_RUN=0
TARGET=dev
PYTHON={shlex.quote(sys.executable)}
{_deploy_auth_function_block()}
bind_deployment_workspace_auth
databricks apps list
""",
        encoding="utf-8",
    )
    run_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        **ambient,
    }
    return (
        subprocess.run(
            [
                "env",
                "-i",
                *(f"{key}={value}" for key, value in run_env.items()),
                "bash",
                str(harness),
            ],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        ),
        mutation_marker,
    )


def _run_deploy_exit_trap_harness(
    tmp_path: Path,
    *,
    original_rc: int,
    stop_result: int,
    quiesce_result: int,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    compensation_log = tmp_path / "compensation.log"
    harness = tmp_path / "deploy-exit-trap-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
RED=""
YLW=""
DIM=""
RST=""
PYTHON=python3
COMPENSATION_LOG={compensation_log!s}
STOP_RESULT={stop_result}
QUIESCE_RESULT={quiesce_result}
stop_app_after_failed_deploy() {{
  printf 'stop\\n' >> "$COMPENSATION_LOG"
  return "$STOP_RESULT"
}}
quiesce_app_treatment_after_failed_stop() {{
  printf 'quiesce\\n' >> "$COMPENSATION_LOG"
  return "$QUIESCE_RESULT"
}}
{_deploy_exit_trap_block()}
exit {original_rc}
""",
        encoding="utf-8",
    )
    return (
        subprocess.run(
            ["bash", str(harness)],
            text=True,
            capture_output=True,
            check=False,
        ),
        compensation_log,
    )


def _commit_deploy_fixture(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        ".env.local\nfrontend/dist/\n.databricks/\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "add", "scripts/deploy.sh", ".gitignore"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Deploy Test",
            "-c",
            "user.email=deploy-test@example.com",
            "commit",
            "-qm",
            "deploy fixture",
        ],
        cwd=repo,
        check=True,
    )


def test_deploy_dev_runs_real_deploy_script_manual_only() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "cron:" not in text
    assert "./scripts/deploy.sh" in text
    assert "--no-confirm" in text
    assert "Run databricks bundle validate/deploy here" not in text


def test_deploy_script_shell_is_syntactically_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_deploy_mints_and_remints_distinct_admin_bearer_for_agent_eval() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "dotenv_value MIP_ADMIN_BEARER_TOKEN" not in text
    assert "databricks auth token" not in text
    assert "DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET" in text
    assert "normal, admin, and verifier M2M client IDs must be distinct" in text
    assert (
        text.count("mint_m2m_token MIP_ADMIN_BEARER_TOKEN") >= 2
    )  # initial per-run mint + immediate pre-eval remint
    remint_pos = text.index("A full deploy can exceed the workspace OAuth TTL")
    eval_pos = text.index("tools/databricks/run_agent_eval.py")
    assert remint_pos < eval_pos


def test_deploy_binds_deployer_auth_and_keeps_normal_app_oauth_shell_scoped() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    bind_call = text.index("\nbind_deployment_workspace_auth\n")
    m2m_resolution = text.index("for _M2M_NAME in")
    first_treatment_proof = text.index(
        'step "quiesce existing app campaign treatment writes before bundle deploy"'
    )
    assert bind_call < m2m_resolution < first_treatment_proof
    assert "MIP_DEPLOYER_DATABRICKS_HOST" in text
    assert "MIP_DEPLOYER_DATABRICKS_TOKEN" in text
    assert "MIP_DEPLOYER_DATABRICKS_PROFILE" in text
    assert "export -n DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET" in text
    assert 'resolve_m2m_credential "$_M2M_NAME" shell' in text
    assert 'DATABRICKS_HOST="${MIP_DATABRICKS_WORKSPACE_HOST:?}"' in text
    m2m_block = text[text.index("run_as_m2m_identity() {") : text.index("# Step 0: preflight")]
    assert "unset MIP_DEPLOYER_DATABRICKS_HOST MIP_DEPLOYER_DATABRICKS_TOKEN" in m2m_block
    for helper in (
        "converge_campaign_treatment_access.py",
        "ensure_campaign_treatment_table.py",
        "stop_app_fail_closed.py",
    ):
        helper_text = (REPO / "tools" / "databricks" / helper).read_text(encoding="utf-8")
        assert "deployment_workspace_client()" in helper_text


@pytest.mark.parametrize("mode", ("pat", "profile"))
def test_deploy_auth_handoff_is_single_workspace_and_child_isolated(
    tmp_path: Path,
    mode: str,
) -> None:
    logs = _run_deploy_auth_harness(tmp_path, mode=mode)
    deploy = logs["deploy"]
    mints = logs["mints"]
    m2m = logs["m2m"]
    expected_host = "https://reviewed-workspace.example"

    assert deploy["MIP_DATABRICKS_WORKSPACE_HOST"] == expected_host
    assert "DATABRICKS_CLIENT_ID" not in deploy
    assert "DATABRICKS_CLIENT_SECRET" not in deploy
    if mode == "pat":
        assert deploy["DATABRICKS_HOST"] == expected_host
        assert deploy["DATABRICKS_TOKEN"] == "reviewed-deployer-pat"
        assert deploy["DATABRICKS_AUTH_TYPE"] == "pat"
        assert "DATABRICKS_CONFIG_PROFILE" not in deploy
    else:
        assert deploy["DATABRICKS_CONFIG_PROFILE"] == "REVIEWED"
        assert "DATABRICKS_HOST" not in deploy
        assert "DATABRICKS_TOKEN" not in deploy
        assert "DATABRICKS_AUTH_TYPE" not in deploy

    assert len(mints) == 2
    assert [mint["DATABRICKS_CLIENT_ID"] for mint in mints] == [
        "normal-app-client",
        "admin-app-client",
    ]
    for mint in mints:
        assert mint["DATABRICKS_HOST"] == expected_host
        assert mint["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
        assert "DATABRICKS_TOKEN" not in mint
        assert "DATABRICKS_CONFIG_PROFILE" not in mint
        assert "MIP_DEPLOYER_DATABRICKS_TOKEN" not in mint
        assert "MIP_DEPLOYER_DATABRICKS_PROFILE" not in mint
        assert "MIP_BEARER_TOKEN" not in mint
        assert "MIP_ADMIN_BEARER_TOKEN" not in mint
        assert "DATABRICKS_VERIFIER_CLIENT_SECRET" not in mint
        assert "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in mint

    assert m2m["DATABRICKS_HOST"] == expected_host
    assert m2m["DATABRICKS_CLIENT_ID"] == "verifier-client"
    assert m2m["DATABRICKS_CLIENT_SECRET"] == "verifier-secret"
    assert m2m["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
    assert "DATABRICKS_TOKEN" not in m2m
    assert "DATABRICKS_CONFIG_PROFILE" not in m2m
    assert "MIP_DEPLOYER_DATABRICKS_TOKEN" not in m2m
    assert "MIP_DEPLOYER_DATABRICKS_PROFILE" not in m2m
    assert "MIP_BEARER_TOKEN" not in m2m
    assert "MIP_ADMIN_BEARER_TOKEN" not in m2m
    assert "DATABRICKS_ADMIN_CLIENT_SECRET" not in m2m
    assert "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in m2m


def test_deploy_refuses_mixed_ambient_pat_and_dotenv_host_before_network(
    tmp_path: Path,
) -> None:
    result, mutation_marker = _run_bind_harness(
        tmp_path,
        env_local="DATABRICKS_HOST=https://dotenv-workspace.example\n",
        bundle_host="https://ambient-workspace.example",
        ambient={
            "DATABRICKS_HOST": "https://ambient-workspace.example",
            "DATABRICKS_TOKEN": "ambient-pat",
        },
    )

    assert result.returncode == 2
    assert "refusing to combine an ambient PAT with a different .env.local host" in result.stderr
    assert not mutation_marker.exists()


def test_deploy_refuses_authenticated_host_outside_bundle_target_before_network(
    tmp_path: Path,
) -> None:
    result, mutation_marker = _run_bind_harness(
        tmp_path,
        env_local=(
            "DATABRICKS_HOST=https://reviewed-workspace.example\n" "DATABRICKS_TOKEN=reviewed-pat\n"
        ),
        bundle_host="https://different-target.example",
        ambient={},
    )

    assert result.returncode == 2
    assert (
        "authenticated workspace host does not match databricks.yml target 'dev'" in result.stderr
    )
    assert not mutation_marker.exists()


def test_deploy_dev_seeds_databricks_auth_without_printing_secrets() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    for secret in (
        "secrets.DATABRICKS_HOST",
        "secrets.DATABRICKS_TOKEN",
        "secrets.DATABRICKS_WAREHOUSE_ID",
        "secrets.GENIE_SPACE_ID",
    ):
        assert secret in text

    assert "$HOME/.databrickscfg" in text
    assert "auth_type = pat" in text
    assert 'chmod 600 "$HOME/.databrickscfg"' in text
    assert "chmod 600 .env.local" in text
    assert "cat .env.local" not in text
    assert 'echo "$DATABRICKS_TOKEN"' not in text
    assert 'echo "MIP_COTALITY_ID_MASK_SECRET=' not in text
    assert 'echo "MIP_GENIE_ACTION_SECRET_CURRENT=' not in text
    assert 'echo "MIP_GENIE_ACTION_SECRET_PREVIOUS=' not in text


def test_deploy_dev_binds_required_and_rotation_secrets() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    for binding in (
        "MIP_COTALITY_ID_MASK_SECRET: ${{ secrets.MIP_COTALITY_ID_MASK_SECRET }}",
        "MIP_GENIE_ACTION_SECRET_CURRENT: ${{ secrets.MIP_GENIE_ACTION_SECRET_CURRENT }}",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS: ${{ secrets.MIP_GENIE_ACTION_SECRET_PREVIOUS }}",
        "MIP_GENIE_ACTION_SECRET_KID: ${{ vars.MIP_GENIE_ACTION_SECRET_KID }}",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID: ${{ vars.MIP_GENIE_ACTION_SECRET_PREVIOUS_KID }}",
    ):
        assert binding in text
    assert "MIP_COTALITY_ID_MASK_SECRET MIP_GENIE_ACTION_SECRET_CURRENT" in text
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID=${MIP_GENIE_ACTION_SECRET_PREVIOUS_KID}" in text


def test_deploy_dev_has_cost_and_permission_guards() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "permissions:" in text
    assert "contents: read" in text
    assert "concurrency:" in text
    assert "group: mip-dev-deploy" in text
    assert "cancel-in-progress: false" in text


def test_deploy_dev_requires_explicit_admin_rbac_and_mints_distinct_app_bearers() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "MIP_ADMIN_EMAILS: ${{ vars.MIP_ADMIN_EMAILS }}" in text
    assert "MIP_ADMIN_GROUP_NAME: ${{ vars.MIP_ADMIN_GROUP_NAME }}" in text
    assert "DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_CLIENT_ID }}" in text
    assert "DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}" in text
    assert "DATABRICKS_ADMIN_CLIENT_ID: ${{ secrets.DATABRICKS_ADMIN_CLIENT_ID }}" in text
    assert "DATABRICKS_ADMIN_CLIENT_SECRET: ${{ secrets.DATABRICKS_ADMIN_CLIENT_SECRET }}" in text
    assert "secrets.MIP_ADMIN_BEARER_TOKEN" not in text
    assert "Mint initial per-run app Bearers" not in text
    assert "--github-env MIP_BEARER_TOKEN" not in text
    assert "--github-env MIP_ADMIN_BEARER_TOKEN" not in text
    assert "DATABRICKS_OPERATOR2_CLIENT_SECRET" not in text
    assert "mint_m2m_token MIP_BEARER_TOKEN" in script
    assert "mint_m2m_token MIP_ADMIN_BEARER_TOKEN" in script
    assert (
        "Normal, operator2, admin, and verifier M2M client IDs must be pairwise distinct." in text
    )
    assert (
        "MIP_APPROVER_IDENTITIES=${DATABRICKS_CLIENT_ID},${DATABRICKS_OPERATOR2_CLIENT_ID}" in text
    )
    assert "MIP_ADMIN_IDENTITIES=${DATABRICKS_ADMIN_CLIENT_ID}" in text
    assert "Configure MIP_ADMIN_EMAILS or MIP_ADMIN_GROUP_NAME" not in text


def test_deploy_uses_dedicated_verifier_for_gateway_proof_writes() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for secret in (
        "DATABRICKS_VERIFIER_CLIENT_ID",
        "DATABRICKS_VERIFIER_CLIENT_SECRET",
    ):
        assert f"{secret}: ${{{{ secrets.{secret} }}}}" in workflow
        assert secret in script
    assert "--identity-role verifier" in script
    assert '--expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in script
    assert '--warehouse-id "$_GRANTS_WAREHOUSE_ID"' in script
    assert "run_as_m2m_identity" in script
    assert "jobs/lakebase_migrate.py" in script
    assert 'export MIP_AI_GATEWAY_VERIFIER_CLIENT_ID="$DATABRICKS_VERIFIER_CLIENT_ID"' in script
    boundary = script.index(
        'step "prove verifier effective authorization boundary before exact Gateway proof"'
    )
    proof = script.index('step "verify AI Gateway exact inference-row proof')
    assert boundary < proof
    assert "tools/databricks/verify_verifier_identity_boundary.py" in script[boundary:proof]
    assert '--protected-service-principal-id "$APP_SP_SCIM_ID"' in script[boundary:proof]
    assert "DATABRICKS_ACCOUNT_ID: ${{ secrets.DATABRICKS_ACCOUNT_ID }}" in workflow


def test_deploy_accepts_numeric_app_service_principal_ids() -> None:
    """The live Databricks Apps API emits service_principal_id as a number."""

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'str(json.load(sys.stdin).get("service_principal_id") or "").strip()' in script


def test_gateway_proof_failure_only_blocks_strict_release_deploys() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    proof_step = script.index('step "verify AI Gateway exact inference-row proof')
    next_step = script.index("wait_for_app_deployable", proof_step)
    proof_block = script[proof_step:next_step]
    assert "--require-verifier-derived-auth" in proof_block
    assert '--warehouse-id "$_GRANTS_WAREHOUSE_ID"' in proof_block
    assert "AI_GATEWAY_PROOF_ARGS+=(--require-verified)" in proof_block
    assert "if ! run_as_m2m_identity \\" in proof_block
    assert "strict AI Gateway exact proof failed" in proof_block
    assert "exit 1" in proof_block
    assert "continuing with the capability honestly configured/unavailable" in proof_block
    assert "${YLW}" in proof_block
    assert "${YEL}" not in script


def test_gateway_grant_delivery_is_retryable_and_only_blocks_strict_release() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow = NIGHTLY.read_text(encoding="utf-8")

    grant_start = script.index("AI_GATEWAY_GRANTS_READY=1")
    proof_start = script.index('step "verify AI Gateway exact inference-row proof', grant_start)
    grant_block = script[grant_start:proof_start]
    assert 'if ! run "$PYTHON" tools/databricks/grant_ai_gateway_inference_table.py' in grant_block
    assert "strict AI Gateway inference-table grant convergence failed" in grant_block
    assert "delivery/grants are pending" in grant_block
    assert "honestly configured/unavailable" in grant_block
    assert "Reconcile delayed AI Gateway inference-table grants" in workflow
    assert workflow.count("tools/databricks/grant_ai_gateway_inference_table.py") >= 1


def test_fresh_deploy_creates_verifier_lakebase_role_before_first_migration() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    absent_or_converged = script.index(
        'step "prove absent or converge governed treatment table before first App creation"'
    )
    quiesce = script.index("--mode quiesce")
    bundle_apply = script.index('tools/databricks/bundle_env.py deploy -t "$TARGET"')
    role_bootstrap = script.index(
        'step "bootstrap dedicated AI Gateway verifier Lakebase OAuth role"'
    )
    first_migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    assert quiesce < absent_or_converged < bundle_apply < role_bootstrap < first_migration
    first_install_block = script[absent_or_converged:bundle_apply]
    assert "tools.databricks.ensure_campaign_treatment_table" in first_install_block
    assert "--allow-absent" in first_install_block
    bootstrap_block = script[role_bootstrap:first_migration]
    assert "tools/databricks/provision_m2m_oauth.py" in bootstrap_block
    assert "--identity-role verifier" in bootstrap_block
    assert '--expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in bootstrap_block
    assert "--no-mint-secret" in bootstrap_block
    assert "--gateway-endpoint" not in bootstrap_block
    assert "--warehouse-id" not in bootstrap_block


def test_fresh_deploy_creates_governed_uc_tables_before_table_grants() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")

    migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    bundle_apply = script.index('tools/databricks/bundle_env.py deploy -t "$TARGET"')
    post_bundle_quiesce = script.index(
        'step "quiesce bundle-resolved app treatment writes before migrations"'
    )
    uc_init = script.index(
        'run_job_with_retry databricks bundle run mip_init_catalog_schemas -t "$TARGET"'
    )
    constraint_convergence = script.index(
        "tools.databricks.ensure_campaign_treatment_table", uc_init
    )
    table_grants = script.index('step "apply UC grants to the app service principal')
    skip_silver_branch = script.index('if [[ "$SKIP_SILVER" -eq 1 ]]')

    assert (
        bundle_apply
        < post_bundle_quiesce
        < migration
        < uc_init
        < constraint_convergence
        < table_grants
        < skip_silver_branch
    )
    assert script.count("--mode quiesce") == 3
    assert "quiesce_app_treatment_after_failed_stop" in script
    assert 'step "quiesce existing app campaign treatment writes before bundle deploy"' in script
    assert 'step "quiesce bundle-resolved app treatment writes before migrations"' in script
    assert "mip_init_catalog_schemas:" in bundle
    assert "path: sql/_rendered/ddl/001_catalogs_schemas.sql" in bundle


def test_first_install_dry_run_does_not_require_a_live_app_identity() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    resolve_start = script.index('if [[ "$DRY_RUN" -eq 0 ]]; then\n  APP_RESOURCE_JSON=')
    quiesce = script.index('step "quiesce bundle-resolved app treatment writes before migrations"')
    resolve_block = script[resolve_start:quiesce]
    assert 'databricks apps get "$_GRANTS_APP_NAME"' in resolve_block
    assert 'APP_SP_CLIENT_ID="dry-run-app-client-id"' in resolve_block
    assert 'APP_SP_SCIM_ID="dry-run-app-scim-id"' in resolve_block


def test_first_install_dry_run_executes_no_databricks_operations(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--local", str(REPO), str(checkout)],
        check=True,
    )
    deploy_copy = checkout / "scripts" / "deploy.sh"
    deploy_copy.write_text(DEPLOY_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    payload_copy = checkout / "tools" / "databricks" / "app_deploy_payload.py"
    payload_copy.write_text(
        (REPO / "tools" / "databricks" / "app_deploy_payload.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "scripts/deploy.sh", "tools/databricks/app_deploy_payload.py"],
        cwd=checkout,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Dry Run Contract",
            "-c",
            "user.email=dry-run@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "test current dry-run contract",
        ],
        cwd=checkout,
        check=True,
    )
    (checkout / ".venv").symlink_to(REPO / ".venv", target_is_directory=True)
    with (checkout / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
        exclude.write(".venv\n")
    (checkout / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-1\n"
        "GENIE_SPACE_ID=space-1\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "databricks.log"
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DATABRICKS_STUB_LOG"\n'
        'if [[ "$1" == "--version" ]]; then\n'
        "  echo 'Databricks CLI v-test'\n"
        "  exit 0\n"
        "fi\n"
        'echo "unexpected Databricks dry-run invocation: $*" >&2\n'
        "exit 97\n",
        encoding="utf-8",
    )
    fake_databricks.chmod(0o755)
    env = {
        **{
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("COV_CORE", "COVERAGE"))
        },
        "APP_ENV": "local",
        "DATABRICKS_STUB_LOG": str(log),
        "GENIE_SPACE_ID": "space-1",
        "MIP_ADMIN_EMAILS": "operator@example.invalid",
        "MIP_COTALITY_ID_MASK_SECRET": "stable-local-mask-secret",
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-local-action-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }

    result = subprocess.run(
        [
            "bash",
            "scripts/deploy.sh",
            "-t",
            "dev",
            "--dry-run",
            "--no-confirm",
            "--skip-silver",
            "--skip-smoke",
        ],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"]
    assert "would grant: GRANT USE CATALOG" in result.stdout
    assert "would verify: campaign treatment table append-only" in result.stdout
    assert "would inspect/create: scope mip and write-once pii-salt-v1" in result.stdout
    assert "deploy Databricks App snapshot with Agent Evaluation proof" in result.stdout


def test_deploy_dev_wires_required_gateway_proof_signing_key() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    secret_binding = (
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY: " "${{ secrets.MIP_AI_GATEWAY_PROOF_SIGNING_KEY }}"
    )
    assert workflow.count(secret_binding) == 2
    required_loop = workflow[workflow.index('missing=""') : workflow.index("python - <<'PY'")]
    assert "MIP_GENIE_ACTION_SECRET_CURRENT" in required_loop
    assert "MIP_AI_GATEWAY_PROOF_SIGNING_KEY" in required_loop


def test_deploy_dev_wires_optional_approved_uc_owner_contract() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    binding = "MIP_UC_APPROVED_OWNER_PRINCIPALS: " "${{ vars.MIP_UC_APPROVED_OWNER_PRINCIPALS }}"
    assert workflow.count(binding) == 2
    assert "MIP_UC_APPROVED_OWNER_PRINCIPALS=${MIP_UC_APPROVED_OWNER_PRINCIPALS}" in workflow
    assert "DATABRICKS_ACCOUNT_CLIENT_ID: ${{ secrets.DATABRICKS_ACCOUNT_CLIENT_ID }}" in workflow
    assert (
        "DATABRICKS_ACCOUNT_CLIENT_SECRET: " "${{ secrets.DATABRICKS_ACCOUNT_CLIENT_SECRET }}"
    ) in workflow
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "dotenv_value MIP_UC_APPROVED_OWNER_PRINCIPALS" in script
    assert 'resolve_m2m_credential "$_ACCOUNT_AUTH_NAME"' in script
    assert "account-SCIM OAuth client must be distinct from" in script
    assert script.index("existing target App service principal") < script.index(
        'step "quiesce existing app campaign treatment writes before bundle deploy"'
    )
    assert "Account-SCIM OAuth client must be distinct from every app-facing" in workflow
    assert (
        "DATABRICKS_ACCOUNT_CLIENT_ID"
        in workflow[
            workflow.index("Configure Databricks dev credentials") : workflow.index(
                "Deploy dev Databricks App"
            )
        ]
    )
    app_yaml = (REPO / "app.yaml").read_text(encoding="utf-8")
    assert "MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED" in app_yaml
    assert 'value: "0"' in app_yaml
    assert "--enable-campaign-treatment-runtime" in script
    assert "export MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED=1" not in script
    first_snapshot = script.index(
        'deploy_app_snapshot "deploy Databricks App snapshot from uploaded bundle source"'
    )
    runtime_restore = script.index(
        "restore exact app campaign treatment runtime privileges after enabled snapshot promotion"
    )
    assert runtime_restore > first_snapshot
    bundle_index = script.index('run "$PYTHON" tools/databricks/bundle_env.py deploy -t "$TARGET"')
    fail_closed_arm = script.index("APP_FAIL_CLOSED_ARMED=1")
    first_treatment_proof = script.index(
        'step "quiesce existing app campaign treatment writes before bundle deploy"'
    )
    assert fail_closed_arm < first_treatment_proof < bundle_index
    assert "stop_app_after_failed_deploy" in script
    assert "tools.databricks.stop_app_fail_closed" in script
    assert "quiesce_app_treatment_after_failed_stop" in script
    assert "--mode quiesce" in script
    assert "original failure was followed by unproven App shutdown" in script
    assert "exit 90" in script
    assert script.index("APP_FAIL_CLOSED_ARMED=0", bundle_index) > script.index(
        'deploy_app_snapshot "deploy Databricks App snapshot with Agent Evaluation proof"'
    )


@pytest.mark.parametrize(
    ("original_rc", "stop_result", "quiesce_result", "expected_rc", "expected_log"),
    (
        (0, 1, 1, 0, ""),
        (7, 0, 1, 7, "stop\n"),
        (7, 1, 0, 90, "stop\nquiesce\n"),
        (7, 1, 1, 90, "stop\nquiesce\n"),
    ),
)
def test_deploy_exit_trap_preserves_failure_or_fails_closed(
    tmp_path: Path,
    original_rc: int,
    stop_result: int,
    quiesce_result: int,
    expected_rc: int,
    expected_log: str,
) -> None:
    result, compensation_log = _run_deploy_exit_trap_harness(
        tmp_path,
        original_rc=original_rc,
        stop_result=stop_result,
        quiesce_result=quiesce_result,
    )

    assert result.returncode == expected_rc, result.stderr
    actual_log = compensation_log.read_text(encoding="utf-8") if compensation_log.exists() else ""
    assert actual_log == expected_log
    if expected_rc == 90:
        assert "original failure was followed by unproven App shutdown" in result.stderr


def test_deploy_dev_wires_optional_salesforce_external_id_upsert_without_preflight() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    for binding in (
        "SALESFORCE_EXTERNAL_ID_FIELD: ${{ vars.SALESFORCE_EXTERNAL_ID_FIELD }}",
        "SALESFORCE_CLIENT_SECRET: ${{ secrets.SALESFORCE_CLIENT_SECRET }}",
        "SALESFORCE_PASSWORD: ${{ secrets.SALESFORCE_PASSWORD }}",
    ):
        assert binding in workflow
    required_preflight = workflow[
        workflow.index('missing=""') : workflow.index('if [ -n "$missing" ]')
    ]
    assert "SALESFORCE" not in required_preflight


def test_deploy_script_requires_cotality_mask_secret_for_non_dev_targets(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'APP_RUNTIME_ENV="${APP_ENV:-}"' in text
    assert "MIP_COTALITY_ID_MASK_SECRET is required for target" in text
    assert "source-known compatibility namespace is allowed only for local/test" in text
    assert text.index("provision_runtime_secrets.py") < text.index("bundle_env.py validate")
    assert 'RUNTIME_SECRET_SCOPE="${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"' in text
    assert 'export BUNDLE_VAR_runtime_secret_scope="$RUNTIME_SECRET_SCOPE"' in text
    assert '--scope "$RUNTIME_SECRET_SCOPE"' in text
    assert "Apps deploy payload\n# carries only value_from resource names" in text

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n" "DATABRICKS_WAREHOUSE_ID=abc123\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("MIP_COTALITY_ID_MASK_SECRET", None)
    env.pop("MIP_GENIE_ACTION_SECRET", None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "customer", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'customer' (APP_ENV=customer)"
        in result.stderr
    )
    assert "step 1: preflight" in result.stdout
    assert "step 2:" not in result.stdout


def test_deploy_script_rejects_placeholder_current_action_secret_for_sandbox(
    tmp_path: Path,
) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=abc123\n"
        "MIP_COTALITY_ID_MASK_SECRET=stable-sandbox-mask-secret\n"
        "MIP_GENIE_ACTION_SECRET=legacy-does-not-count\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "REDACTED",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    for name in ("APP_ENV", "MIP_GENIE_ACTION_SECRET"):
        env.pop(name, None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "dev", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_GENIE_ACTION_SECRET_CURRENT is required for target 'dev' (APP_ENV=sandbox)"
        in result.stderr
    )
    assert "step 2:" not in result.stdout


def test_deploy_script_requires_cotality_mask_secret_for_customer_runtime_env(
    tmp_path: Path,
) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n" "DATABRICKS_WAREHOUSE_ID=abc123\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "APP_ENV": "customer",
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("MIP_COTALITY_ID_MASK_SECRET", None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "dev", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'dev' (APP_ENV=customer)"
        in result.stderr
    )
    assert "step 2:" not in result.stdout


def test_deploy_script_rejects_legacy_genie_secret_as_mask_secret(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=abc123\n"
        "MIP_GENIE_ACTION_SECRET=legacy-does-not-count\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("MIP_COTALITY_ID_MASK_SECRET", None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "customer", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'customer' (APP_ENV=customer)"
        in result.stderr
    )
    assert "cotality id-mask secret: configured" not in result.stdout
    assert "step 2:" not in result.stdout


def test_deploy_script_rejects_placeholder_cotality_mask_secret(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=abc123\n"
        "MIP_COTALITY_ID_MASK_SECRET=REDACTED\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "customer", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'customer' (APP_ENV=customer)"
        in result.stderr
    )
    assert "cotality id-mask secret: configured" not in result.stdout
    assert "step 2:" not in result.stdout


def test_exact_source_gate_allows_standard_ignored_artifacts(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'APP_GIT_SHA="$SOURCE_GIT_SHA"' in text
    assert text.count("verify_exact_deploy_source") >= 3
    assert text.rindex("verify_exact_deploy_source") < text.index(
        'bundle_env.py deploy -t "$TARGET"'
    )

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    script_dir.mkdir(parents=True)
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    _commit_deploy_fixture(repo)
    (repo / ".env.local").write_text("local-only=true\n", encoding="utf-8")
    (repo / "frontend" / "dist").mkdir(parents=True)
    (repo / "frontend" / "dist" / "index.html").write_text("built", encoding="utf-8")
    (repo / ".databricks").mkdir()
    (repo / ".databricks" / "cache.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(deploy_copy), "--verify-source-only"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "exact source:" in result.stdout
    assert "tracked and untracked source clean" in result.stdout


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_exact_source_gate_rejects_dirty_uploaded_source(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    script_dir.mkdir(parents=True)
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(DEPLOY_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    deploy_copy.chmod(0o755)
    _commit_deploy_fixture(repo)
    expected_path = "scripts/deploy.sh" if dirty_kind == "tracked" else "unreviewed.py"
    if dirty_kind == "tracked":
        deploy_copy.write_text(
            deploy_copy.read_text(encoding="utf-8") + "\n# dirty source\n",
            encoding="utf-8",
        )
    else:
        (repo / expected_path).write_text("print('not committed')\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(deploy_copy), "--verify-source-only"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "refusing deployment from dirty source" in result.stderr
    assert expected_path in result.stderr
