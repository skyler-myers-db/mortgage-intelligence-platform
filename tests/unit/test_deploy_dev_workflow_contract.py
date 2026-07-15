"""Contracts for the manual dev deployment workflow."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
DEPLOY_SCRIPT = REPO / "scripts" / "deploy.sh"


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
    assert text.count(
        "mint_m2m_token MIP_ADMIN_BEARER_TOKEN"
    ) >= 2  # initial per-run mint + immediate pre-eval remint
    remint_pos = text.index("A full deploy can exceed the workspace OAuth TTL")
    eval_pos = text.index("tools/databricks/run_agent_eval.py")
    assert remint_pos < eval_pos


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

    assert "MIP_ADMIN_EMAILS: ${{ vars.MIP_ADMIN_EMAILS }}" in text
    assert "MIP_ADMIN_GROUP_NAME: ${{ vars.MIP_ADMIN_GROUP_NAME }}" in text
    assert "Configure MIP_ADMIN_EMAILS or MIP_ADMIN_GROUP_NAME" in text
    assert "DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_CLIENT_ID }}" in text
    assert "DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}" in text
    assert "DATABRICKS_ADMIN_CLIENT_ID: ${{ secrets.DATABRICKS_ADMIN_CLIENT_ID }}" in text
    assert (
        "DATABRICKS_ADMIN_CLIENT_SECRET: ${{ secrets.DATABRICKS_ADMIN_CLIENT_SECRET }}" in text
    )
    assert "secrets.MIP_ADMIN_BEARER_TOKEN" not in text
    assert "python tools/oauth_m2m_mint.py" in text
    assert "--github-env MIP_BEARER_TOKEN" in text
    assert "--github-env MIP_ADMIN_BEARER_TOKEN" in text
    assert "Normal and admin M2M client IDs must be distinct" in text


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
    assert "run_as_m2m_identity" in script
    assert "jobs/lakebase_migrate.py" in script
    assert 'export MIP_AI_GATEWAY_VERIFIER_CLIENT_ID="$DATABRICKS_VERIFIER_CLIENT_ID"' in script


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
