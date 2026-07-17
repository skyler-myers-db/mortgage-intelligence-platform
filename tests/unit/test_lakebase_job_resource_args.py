from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY

import pytest

from jobs import kpi_snapshot, lakebase_migrate, sync_lifecycle_state


@pytest.mark.parametrize(
    "module",
    (lakebase_migrate, sync_lifecycle_state, kpi_snapshot),
)
def test_explicit_lakebase_resource_args_override_ambient_environment(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAKEBASE_HOST", raising=False)
    monkeypatch.delenv("LAKEBASE_USER", raising=False)
    monkeypatch.delenv("LAKEBASE_PASSWORD", raising=False)
    monkeypatch.setenv("LAKEBASE_INSTANCE_NAME", "wrong-ambient-instance")
    monkeypatch.setenv("LAKEBASE_DATABASE", "wrong_ambient_database")

    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def do(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        calls.append((method, path, body))
        if method == "GET":
            return {"read_write_dns": "isolated.example.database.cloud.databricks.com"}
        return {"token": "bounded-oauth-token"}

    client = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="operator@example.com", display_name=None)
        ),
        api_client=SimpleNamespace(do=do),
    )
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", lambda: client)

    resolved = module._resolve_connection(
        instance_name="mip-pr105-state",
        database_name="mip_pr105_database",
    )

    assert resolved["dbname"] == "mip_pr105_database"
    assert calls == [
        ("GET", "/api/2.0/database/instances/mip-pr105-state", None),
        (
            "POST",
            "/api/2.0/database/credentials",
            {
                "request_id": ANY,
                "instance_names": ["mip-pr105-state"],
            },
        ),
    ]


@pytest.mark.parametrize(
    "module",
    (lakebase_migrate, sync_lifecycle_state, kpi_snapshot),
)
def test_lakebase_job_parser_accepts_explicit_resource_args(module: Any) -> None:
    args = module.build_parser().parse_args(
        [
            "--lakebase-instance=mip-pr105-state",
            "--lakebase-database=mip_pr105_database",
        ]
    )

    assert args.lakebase_instance == "mip-pr105-state"
    assert args.lakebase_database == "mip_pr105_database"


def test_lakebase_migration_parser_accepts_explicit_app_name() -> None:
    args = lakebase_migrate.build_parser().parse_args(
        ["--app-name=mip-app-pr105-staging"]
    )

    assert args.app_name == "mip-app-pr105-staging"
