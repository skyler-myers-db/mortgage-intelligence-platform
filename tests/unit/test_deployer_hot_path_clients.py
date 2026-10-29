"""High-volume deployer paths build their client from the governed deployer binding."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.github_oidc import (
    DEPLOYER_CLIENT_ID,
    WORKSPACE_HOST,
    install_github_oidc_deployer,
)
from tools.databricks import app_deployment_lease as lease
from tools.databricks import app_release_source as source
from tools.databricks.github_oidc_auth import CachedGitHubOidcCredentials
from tools.databricks.workspace_auth import deployment_workspace_client

SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_release_publish_defaults_to_the_governed_deployer_binding() -> None:
    default = inspect.signature(source.main).parameters["client"].default

    assert default is deployment_workspace_client


def _run_heartbeat(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict[str, Any]], list[object]]:
    built: list[dict[str, Any]] = []
    heartbeats: list[object] = []

    def workspace_client(**kwargs: Any) -> object:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(lease, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(
        lease,
        "_heartbeat",
        lambda workspace, **_kwargs: heartbeats.append(workspace),
    )
    assert (
        lease.main(
            [
                "heartbeat",
                "--app-name",
                "mip-app",
                "--lease-id",
                "lease-id",
                "--source-git-sha",
                SOURCE_SHA,
                "--parent-pid",
                "1",
            ]
        )
        == 0
    )
    return built, heartbeats


def test_lease_heartbeat_client_uses_the_cached_github_oidc_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_github_oidc_deployer(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "normal-app-client")

    built, heartbeats = _run_heartbeat(monkeypatch)

    assert len(built) == 1 and len(heartbeats) == 1
    assert isinstance(built[0].pop("credentials_strategy"), CachedGitHubOidcCredentials)
    assert built == [
        {
            "profile": "DEFAULT",
            "host": WORKSPACE_HOST,
            "client_id": DEPLOYER_CLIENT_ID,
            "auth_type": "github-oidc",
        }
    ]


def test_lease_cli_pat_binding_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_HOST", "https://workspace.cloud.databricks.com")
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_TOKEN", "deployer-pat")
    monkeypatch.delenv("MIP_DEPLOYER_DATABRICKS_PROFILE", raising=False)

    built, _heartbeats = _run_heartbeat(monkeypatch)

    assert built == [
        {
            "host": "https://workspace.cloud.databricks.com",
            "token": "deployer-pat",
            "auth_type": "pat",
        }
    ]


def test_lease_cli_without_a_deployer_binding_keeps_sdk_default_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MIP_DEPLOYER_DATABRICKS_HOST",
        "MIP_DEPLOYER_DATABRICKS_TOKEN",
        "MIP_DEPLOYER_DATABRICKS_PROFILE",
        "DATABRICKS_CONFIG_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)

    built, _heartbeats = _run_heartbeat(monkeypatch)

    assert built == [{}]
