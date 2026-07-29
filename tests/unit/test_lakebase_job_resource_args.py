from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY

import pytest

from jobs import kpi_snapshot, lakebase_migrate, sync_lifecycle_state

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_lakebase_credential_error_never_logs_response_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LAKEBASE_HOST", raising=False)
    monkeypatch.delenv("LAKEBASE_USER", raising=False)
    monkeypatch.delenv("LAKEBASE_PASSWORD", raising=False)
    sentinel = "SENSITIVE_CREDENTIAL_SENTINEL"

    def do(method: str, _path: str, **_kwargs: object) -> dict[str, str]:
        if method == "GET":
            return {"read_write_dns": "isolated.example.database.cloud.databricks.com"}
        return {"access_token": sentinel, "error_detail": sentinel}

    client = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="operator@example.com", display_name=None)
        ),
        api_client=SimpleNamespace(do=do),
    )
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", lambda: client)

    with pytest.raises(SystemExit, match="3"):
        lakebase_migrate._resolve_connection()

    captured = capsys.readouterr()
    assert "credential response missing token" in captured.err
    assert sentinel not in captured.err


def test_lakebase_sdk_exception_never_reflects_secret_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LAKEBASE_HOST", raising=False)
    monkeypatch.delenv("LAKEBASE_USER", raising=False)
    monkeypatch.delenv("LAKEBASE_PASSWORD", raising=False)
    sentinel = "SENSITIVE_SDK_EXCEPTION_SENTINEL"

    def do(method: str, _path: str, **_kwargs: object) -> dict[str, str]:
        if method == "GET":
            return {"read_write_dns": "isolated.example.database.cloud.databricks.com"}
        raise RuntimeError(f"credential exchange failed with secret={sentinel}")

    client = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="operator@example.com", display_name=None)
        ),
        api_client=SimpleNamespace(do=do),
    )
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", lambda: client)

    with pytest.raises(SystemExit, match="3"):
        lakebase_migrate._resolve_connection()

    captured = capsys.readouterr()
    assert "SDK auth/resolution failed" in captured.err
    assert "database credential permissions" in captured.err
    assert sentinel not in captured.out
    assert sentinel not in captured.err


@pytest.mark.parametrize(
    ("stage", "expected_message"),
    (
        ("verifier", "verifier identity preflight failed"),
        ("roles", "runtime-role preflight failed"),
        ("transaction", "schema transaction failed"),
        ("grants", "app-role grants failed"),
    ),
)
def test_lakebase_migration_failure_boundaries_never_reflect_exception_text(
    stage: str,
    expected_message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "SENSITIVE_MIGRATION_EXCEPTION_SENTINEL"

    def failure(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(
        lakebase_migrate,
        "_resolve_ai_gateway_verifier_role",
        lambda *_args, **_kwargs: "verifier-role",
    )
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda *_a, **_k: {})
    monkeypatch.setattr(
        lakebase_migrate,
        "_preflight_database_roles",
        lambda *_args, **_kwargs: ("app-role", "verifier-role"),
    )
    monkeypatch.setattr(lakebase_migrate, "_tenant_disclosure_seed_sql", lambda **_k: "")
    monkeypatch.setattr(lakebase_migrate, "_run_transaction", lambda *_a, **_k: None)
    monkeypatch.setattr(lakebase_migrate, "_apply_app_role_grants", lambda *_a, **_k: None)
    target = {
        "verifier": "_resolve_ai_gateway_verifier_role",
        "roles": "_preflight_database_roles",
        "transaction": "_run_transaction",
        "grants": "_apply_app_role_grants",
    }[stage]
    monkeypatch.setattr(lakebase_migrate, target, failure)

    with pytest.raises(SystemExit, match="2"):
        lakebase_migrate.main(
            app_name="mip-app-pr105-staging",
            lakebase_instance="mip-pr105-state",
            lakebase_database="mip_app_state",
            ai_gateway_verifier_client_id="verifier-client-id",
            require_ai_gateway_verifier=True,
        )

    captured = capsys.readouterr()
    assert expected_message in captured.err
    assert sentinel not in captured.out
    assert sentinel not in captured.err


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
    args = lakebase_migrate.build_parser().parse_args(["--app-name=mip-app-pr105-staging"])

    assert args.app_name == "mip-app-pr105-staging"


def test_lakebase_migration_parser_accepts_required_explicit_verifier_identity() -> None:
    args = lakebase_migrate.build_parser().parse_args(
        [
            "--ai-gateway-verifier-client-id=verifier-client-id",
            "--require-ai-gateway-verifier",
        ]
    )

    assert args.ai_gateway_verifier_client_id == "verifier-client-id"
    assert args.require_ai_gateway_verifier is True


def test_lakebase_migration_direct_help_bootstraps_repo_without_pythonpath(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "jobs" / "lakebase_migrate.py"), "--help"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Apply the governed Lakebase schema and seed" in result.stdout


def test_lakebase_migration_facade_preserves_reviewed_import_seams() -> None:
    expected = {
        "_APP_ROLE_ROUTINE_PRIVILEGES",
        "_APP_ROLE_SEQUENCE_PRIVILEGES",
        "_APP_ROLE_TABLE_PRIVILEGES",
        "_APP_TRIGGER_CONTRACT",
        "_AUDIT_SEQUENCE_DEFAULT_EXPRESSION",
        "_COLUMN_PRIVILEGE_NAMES",
        "_QUARANTINED_CONSTRAINT_LEGACY_EXPRESSION_CONTRACT",
        "_QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT",
        "_UNSAFE_ROLE_ATTRIBUTE_NAMES",
        "_apply_app_role_grants",
        "_expect_database_rejection",
        "_postflight_ai_gateway_verifier_grants",
        "_postflight_app_role_grants",
        "_postflight_direct_column_privileges",
        "_postflight_effective_column_only_privileges",
        "_postflight_effective_default_privileges",
        "_postflight_effective_routine_privileges",
        "_postflight_effective_schema_privileges",
        "_postflight_event_trigger_inventory",
        "_postflight_role_security",
        "_postflight_trigger_inventory",
        "_preflight_executable_schema_hooks",
        "_quarantine_existing_reviewed_triggers",
        "_quarantine_reviewed_constraints",
        "_raise_object_inventory_mismatch",
        "_repo_root",
        "_resolve_ai_gateway_verifier_role",
        "_resolve_app_role",
        "_resolve_connection",
        "_run_outreach_integrity_probe",
        "_run_transaction",
        "_schema_hook_function_calls",
        "_sql_literal",
        "_split_schema_sql",
        "_tenant_disclosure_seed_sql",
        "build_parser",
        "main",
        "time",
    }

    assert expected <= vars(lakebase_migrate).keys()
