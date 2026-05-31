"""Contracts for the manual dev deployment workflow."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"


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
