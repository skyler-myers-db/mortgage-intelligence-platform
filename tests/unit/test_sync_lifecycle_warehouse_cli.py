"""Authentication contracts for the lifecycle-sync CLI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "tools" / "sync_lifecycle_warehouse.py"
SPEC = importlib.util.spec_from_file_location("mip_sync_lifecycle_warehouse_cli", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_workspace_token_reuses_pat_for_exact_configured_host(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example.com/")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test-pat")

    with patch.object(MODULE, "_cli_token") as cli_token:
        assert MODULE._workspace_token("https://dbc.example.com") == "dapi-test-pat"

    cli_token.assert_not_called()


def test_workspace_token_never_forwards_pat_to_other_host(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test-pat")

    with patch.object(MODULE, "_cli_token", return_value="oauth-token") as cli_token:
        assert MODULE._workspace_token("https://other.example.com") == "oauth-token"

    cli_token.assert_called_once_with("https://other.example.com")


def test_workspace_token_mints_oauth_when_pat_is_absent(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example.com")
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    with patch.object(MODULE, "_cli_token", return_value="oauth-token") as cli_token:
        assert MODULE._workspace_token("https://dbc.example.com") == "oauth-token"

    cli_token.assert_called_once_with("https://dbc.example.com")
