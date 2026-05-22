from __future__ import annotations

from tools.databricks.app_deploy_payload import build_payload


def _env_map(payload: dict[str, object]) -> dict[str, dict[str, str]]:
    return {item["name"]: item for item in payload["env_vars"]}  # type: ignore[index]


def test_app_deploy_payload_preserves_resource_bindings_and_safe_runtime_config(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIP_LENDER_NAME", "Acme Mortgage")
    monkeypatch.setenv("MIP_TRUST_FORWARDED_HEADERS", "false")
    monkeypatch.setenv("MIP_RUM_ENABLED", "1")
    monkeypatch.setenv("MIP_GENIE_CONCURRENCY_LIMIT", "8")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        current_user_email="se@example.com",
        catalog="acme_mip",
        schema="gold",
    )
    env = _env_map(payload)

    assert payload["source_code_path"] == "/Workspace/app/files"
    assert env["DATABRICKS_WAREHOUSE_ID"] == {
        "name": "DATABRICKS_WAREHOUSE_ID",
        "value_from": "sql_warehouse",
    }
    assert env["GENIE_SPACE_ID"]["value_from"] == "genie_space"
    assert env["LAKEBASE_HOST"]["value_from"] == "database"
    assert env["MIP_DEFAULT_CATALOG"]["value"] == "acme_mip"
    assert env["MIP_LENDER_NAME"]["value"] == "Acme Mortgage"
    assert env["MIP_TRUST_FORWARDED_HEADERS"]["value"] == "false"
    assert env["MIP_RUM_ENABLED"]["value"] == "1"
    assert env["MIP_GENIE_CONCURRENCY_LIMIT"]["value"] == "8"
    assert "MIP_ADMIN_EMAILS" not in env


def test_app_deploy_payload_does_not_overlay_lakebase_dsn_fragments(monkeypatch) -> None:
    """Databricks Apps database bindings own PG* runtime values.

    Local `.env.local` DSN hints are useful for laptops and migration jobs, but
    they must not override the bound app database. A stale LAKEBASE_DATABASE
    value would make the deployed app connect to the wrong Postgres database.
    """
    monkeypatch.setenv("LAKEBASE_PORT", "6543")
    monkeypatch.setenv("LAKEBASE_DATABASE", "wrong_db")
    monkeypatch.setenv("LAKEBASE_SSLMODE", "disable")

    payload = build_payload(source_code_path="/Workspace/app/files", target="prod")
    env = _env_map(payload)

    assert env["PGHOST"]["value_from"] == "database"
    assert env["LAKEBASE_HOST"]["value_from"] == "database"
    assert "LAKEBASE_PORT" not in env
    assert "LAKEBASE_DATABASE" not in env
    assert "LAKEBASE_SSLMODE" not in env


def test_dev_payload_bootstraps_current_user_admin_without_changing_prod_default() -> None:
    dev = build_payload(
        source_code_path="/Workspace/app/files",
        target="dev",
        current_user_email="skyler@entrada.ai",
    )
    prod = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        current_user_email="skyler@entrada.ai",
    )

    assert _env_map(dev)["MIP_ADMIN_EMAILS"]["value"] == "skyler@entrada.ai"
    assert "MIP_ADMIN_EMAILS" not in _env_map(prod)
