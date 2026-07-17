#!/usr/bin/env python3
"""Sync Lakebase lifecycle state into UC gold via SQL Warehouse."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = str(Path(__file__).resolve().parent)
# Direct execution (``python tools/sync_lifecycle_warehouse.py``) puts the
# repository's ``tools/`` directory ahead of site-packages.  Its local
# ``tools/databricks`` package would then shadow the installed
# ``databricks-sdk`` namespace used by workspace_auth.
while TOOLS_DIR in sys.path:
    sys.path.remove(TOOLS_DIR)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from tools.databricks.workspace_auth import deployment_workspace_client  # noqa: E402

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}\Z")


def _workspace_token_provider(workspace: Any):
    def token() -> str:
        headers = workspace.config.authenticate()
        authorization = str(headers.get("Authorization") or "")
        if not authorization.startswith("Bearer "):
            raise RuntimeError("deployer workspace auth returned no bearer token")
        return authorization.removeprefix("Bearer ").strip()

    return token


def _build_client(timeout_s: int, settings: Any, workspace: Any | None = None):
    from backend.services.databricks_sql import DatabricksSqlClient

    if workspace is not None:
        host = str(workspace.config.host or "").strip()
        warehouse_id = (
            os.environ.get("DATABRICKS_WAREHOUSE_ID") or settings.databricks_warehouse_id or ""
        ).strip()
        if not host or not warehouse_id:
            raise RuntimeError("deployer workspace host and warehouse are required")
        token_provider = _workspace_token_provider(workspace)
    else:
        try:
            host, token_provider, warehouse_id = settings.require_databricks_creds()
        except RuntimeError:
            host = settings.databricks_host
            warehouse_id = settings.databricks_warehouse_id
            if not host or not warehouse_id:
                raise

        def token_provider() -> str:
            return _cli_token(host)

    return DatabricksSqlClient(
        host=host,
        token=token_provider,
        warehouse_id=warehouse_id,
        timeout_s=timeout_s,
    )


def _cli_token(host: str) -> str:
    raw = subprocess.check_output(
        ["databricks", "auth", "token", "--host", host],
        text=True,
    )
    token = json.loads(raw).get("access_token") or ""
    if not token:
        raise RuntimeError("databricks auth token returned no access_token")
    return token


def _workspace_token(host: str) -> str:
    """Use the configured PAT for its exact host, otherwise mint OAuth."""
    token = (os.environ.get("DATABRICKS_TOKEN") or "").strip()
    configured_host = (os.environ.get("DATABRICKS_HOST") or "").strip().rstrip("/")
    requested_host = host.strip().rstrip("/")
    if token and configured_host and configured_host == requested_host:
        return token
    return _cli_token(host)


def main() -> None:
    _load_local_env()
    from backend.config.settings import settings
    from backend.services.lifecycle_sync import sync_lifecycle_state_via_warehouse

    parser = argparse.ArgumentParser(
        description="Mirror Lakebase approvals/outreach state into UC gold without a Spark job."
    )
    parser.add_argument("--catalog", default=settings.mip_default_catalog)
    parser.add_argument(
        "--lakebase-instance",
        default=os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state"),
    )
    parser.add_argument(
        "--lakebase-database",
        default=os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
    )
    parser.add_argument("--timeout-s", type=int, default=50)
    parser.add_argument(
        "--funnel-sql",
        type=Path,
        default=Path("sql/_rendered/transformations/gold_funnel_snapshot_daily.sql"),
    )
    parser.add_argument("--skip-funnel", action="store_true")
    args = parser.parse_args()
    deployer_bound = any(
        os.environ.get(name, "").strip()
        for name in (
            "MIP_DEPLOYER_DATABRICKS_HOST",
            "MIP_DEPLOYER_DATABRICKS_TOKEN",
            "MIP_DEPLOYER_DATABRICKS_PROFILE",
        )
    )
    workspace = deployment_workspace_client() if deployer_bound else None
    _ensure_lakebase_env(
        settings,
        workspace=workspace,
        instance_name=args.lakebase_instance,
        database_name=args.lakebase_database,
    )

    result = sync_lifecycle_state_via_warehouse(
        catalog=args.catalog,
        sql_client=_build_client(args.timeout_s, settings, workspace),
        record_funnel_snapshot=not args.skip_funnel,
        funnel_sql_path=args.funnel_sql,
    )
    print(json.dumps(result.__dict__, sort_keys=True))


def _load_local_env() -> None:
    from dotenv import dotenv_values

    deployer_auth_bound = any(
        os.environ.get(name, "").strip()
        for name in (
            "MIP_DEPLOYER_DATABRICKS_HOST",
            "MIP_DEPLOYER_DATABRICKS_TOKEN",
            "MIP_DEPLOYER_DATABRICKS_PROFILE",
        )
    )
    immutable_workspace_auth = {
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_CONFIG_PROFILE",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    }
    for path in (REPO_ROOT / ".env", REPO_ROOT / ".env.local"):
        if not path.exists():
            continue
        for name, value in dotenv_values(path).items():
            if value is None:
                continue
            if name in {"DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"}:
                continue
            if deployer_auth_bound and name in immutable_workspace_auth:
                continue
            os.environ.setdefault(name, value)


def _ensure_lakebase_env(
    settings: Any,
    *,
    workspace: Any | None = None,
    instance_name: str | None = None,
    database_name: str | None = None,
) -> None:
    instance_name = instance_name or os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    database_name = database_name or os.environ.get("LAKEBASE_DATABASE", "mip_app_state")
    if _IDENTIFIER.fullmatch(database_name) is None:
        raise ValueError("Lakebase database must be an unquoted identifier")
    host = (os.environ.get("LAKEBASE_HOST") or "").strip().lower()
    password = (os.environ.get("LAKEBASE_PASSWORD") or "").strip()
    if workspace is None and host and host not in {"localhost", "127.0.0.1", "::1"} and password:
        os.environ["LAKEBASE_DATABASE"] = database_name
        os.environ["PGDATABASE"] = database_name
        settings.lakebase_database = database_name
        return

    if workspace is not None:
        dns = workspace.database.get_database_instance(instance_name).read_write_dns
        credential = workspace.database.generate_database_credential(
            instance_names=[instance_name],
            request_id=f"mip-sync-lifecycle-cli-{uuid.uuid4()}",
        )
        user_name = workspace.current_user.me().user_name
        _set_lakebase_auth(
            settings,
            str(dns),
            str(user_name),
            str(credential.token),
            database_name=database_name,
        )
        return

    workspace_host = settings.databricks_host
    if not workspace_host:
        return
    token = _workspace_token(workspace_host)
    api_host = workspace_host.rstrip("/")

    inst = _api_json(
        api_host,
        token,
        "GET",
        f"/api/2.0/database/instances/{instance_name}",
    )
    me = _api_json(api_host, token, "GET", "/api/2.0/preview/scim/v2/Me")
    cred = _api_json(
        api_host,
        token,
        "POST",
        "/api/2.0/database/credentials",
        body={
            "request_id": f"mip-sync-lifecycle-cli-{uuid.uuid4()}",
            "instance_names": [instance_name],
        },
    )

    _set_lakebase_auth(
        settings,
        str(inst.get("read_write_dns") or ""),
        str(me.get("userName") or ""),
        str(cred.get("token") or ""),
        database_name=database_name,
    )


def _set_lakebase_auth(
    settings: Any,
    host: str,
    user: str,
    password: str,
    *,
    database_name: str,
) -> None:
    if not host or not user or not password:
        raise RuntimeError("deployer-derived Lakebase credentials were incomplete")
    values = {
        "LAKEBASE_HOST": host,
        "LAKEBASE_PORT": "5432",
        "LAKEBASE_DATABASE": database_name,
        "LAKEBASE_USER": user,
        "LAKEBASE_PASSWORD": password,
        "LAKEBASE_SSLMODE": "require",
        "PGHOST": host,
        "PGPORT": "5432",
        "PGDATABASE": database_name,
        "PGUSER": user,
        "PGPASSWORD": password,
        "PGSSLMODE": "require",
    }
    os.environ.update(values)
    settings.lakebase_host = host
    settings.lakebase_port = 5432
    settings.lakebase_database = database_name
    settings.lakebase_user = user
    from pydantic import SecretStr

    settings.lakebase_password = SecretStr(password)
    settings.lakebase_sslmode = "require"


def _api_json(
    host: str,
    token: str,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        host + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


if __name__ == "__main__":
    main()
