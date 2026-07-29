"""CLI boundary tests for authenticated Gateway lifecycle mutation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import gateway_model_archival_cli as cli


def _argv(*, archive_owner: str = "governance@example.com") -> list[str]:
    return [
        "archive-unprotected",
        "--app-name",
        "mip-app",
        "--lease-id",
        "11111111-1111-4111-8111-111111111111",
        "--source-git-sha",
        "a" * 40,
        "--runtime-application-id",
        "runtime-application-id",
        "--app-application-id",
        "app-application-id",
        "--proxy-application-id",
        "proxy-application-id",
        "--verifier-application-id",
        "verifier-application-id",
        "--archive-owner",
        archive_owner,
        "--governance-group",
        "mortgage-governance",
        "--catalog",
        "mip",
        "--model-family",
        "mip.audit.mortgage_growth_supervisor_proxy",
        "--experiment-base",
        "mip-agent-runtime-gateway-proxy",
        "--inference-schema",
        "audit",
        "--inference-table-prefix",
        "mip_agent_gateway_growth_agent",
        "--rollback-scope",
        "production",
        "--lakebase-instance",
        "mip-lakebase",
        "--warehouse-id",
        "warehouse-id",
        "--expected-inventory-principal",
        "governance@example.com",
    ]


@pytest.mark.parametrize(
    ("caller", "archive_owner"),
    [
        ("other@example.com", "governance@example.com"),
        ("governance@example.com", "other-owner@example.com"),
    ],
)
def test_cli_rejects_admin_or_archive_owner_mismatch_before_mutation_clients(
    monkeypatch: pytest.MonkeyPatch,
    caller: str,
    archive_owner: str,
) -> None:
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name=caller, application_id="")
        ),
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id="metastore-id")
        ),
    )
    mutation_clients: list[str] = []

    monkeypatch.setattr(cli, "WorkspaceClient", lambda: workspace)

    def forbidden_client(*_args: Any, **_kwargs: Any) -> Any:
        mutation_clients.append("mlflow")
        raise AssertionError("MLflow client created before admin authentication")

    def forbidden_archive(*_args: Any, **_kwargs: Any) -> Any:
        mutation_clients.append("archive")
        raise AssertionError("archive mutation ran before admin authentication")

    monkeypatch.setattr(cli, "MlflowClient", forbidden_client)
    monkeypatch.setattr(cli, "archive_unprotected_gateway_models", forbidden_archive)

    with pytest.raises(RuntimeError, match="admin inventory identity is not exact"):
        cli.main(_argv(archive_owner=archive_owner))

    assert mutation_clients == []
