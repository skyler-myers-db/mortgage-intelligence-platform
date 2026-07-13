"""Contracts for the manual dev deployment workflow."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
DEPLOY_SCRIPT = REPO / "scripts" / "deploy.sh"


def test_deploy_dev_runs_real_deploy_script_manual_only() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "cron:" not in text
    assert "./scripts/deploy.sh" in text
    assert "--no-confirm" in text
    assert "Run databricks bundle validate/deploy here" not in text


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
    assert "chmod 600 \"$HOME/.databrickscfg\"" in text
    assert "chmod 600 .env.local" in text
    assert "cat .env.local" not in text
    assert "echo \"$DATABRICKS_TOKEN\"" not in text


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
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS=${MIP_GENIE_ACTION_SECRET_PREVIOUS}" in text
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID=${MIP_GENIE_ACTION_SECRET_PREVIOUS_KID}" in text


def test_deploy_dev_has_cost_and_permission_guards() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "permissions:" in text
    assert "contents: read" in text
    assert "concurrency:" in text
    assert "group: mip-dev-deploy" in text
    assert "cancel-in-progress: false" in text


def test_deploy_dev_requires_explicit_admin_rbac_and_mints_app_bearer() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "MIP_ADMIN_EMAILS: ${{ vars.MIP_ADMIN_EMAILS }}" in text
    assert "MIP_ADMIN_GROUP_NAME: ${{ vars.MIP_ADMIN_GROUP_NAME }}" in text
    assert "Configure MIP_ADMIN_EMAILS or MIP_ADMIN_GROUP_NAME" in text
    assert "DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_CLIENT_ID }}" in text
    assert "DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}" in text
    assert "MIP_ADMIN_BEARER_TOKEN: ${{ secrets.MIP_ADMIN_BEARER_TOKEN }}" in text
    assert "python tools/oauth_m2m_mint.py" in text
    assert "MIP_BEARER_TOKEN=$bearer" in text


def test_deploy_script_requires_cotality_mask_secret_for_non_dev_targets(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'APP_RUNTIME_ENV="${APP_ENV:-}"' in text
    assert "MIP_COTALITY_ID_MASK_SECRET is required for target" in text
    assert "source-known compatibility namespace is allowed only for local/test" in text

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
        "DATABRICKS_WAREHOUSE_ID=abc123\n",
        encoding="utf-8",
    )
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
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=abc123\n",
        encoding="utf-8",
    )
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
