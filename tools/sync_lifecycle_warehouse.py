#!/usr/bin/env python3
"""Sync Lakebase lifecycle state into UC gold via SQL Warehouse."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def _build_client(timeout_s: int, settings: object):
    from backend.services.databricks_sql import DatabricksSqlClient

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


def main() -> None:
    _load_local_env()
    from backend.config.settings import settings
    from backend.services.lifecycle_sync import sync_lifecycle_state_via_warehouse

    parser = argparse.ArgumentParser(
        description="Mirror Lakebase approvals/outreach state into UC gold without a Spark job."
    )
    parser.add_argument("--catalog", default=settings.mip_default_catalog)
    parser.add_argument("--timeout-s", type=int, default=50)
    parser.add_argument(
        "--funnel-sql",
        type=Path,
        default=Path("sql/_rendered/transformations/gold_funnel_snapshot_daily.sql"),
    )
    parser.add_argument("--skip-funnel", action="store_true")
    args = parser.parse_args()
    _ensure_lakebase_env(settings)

    result = sync_lifecycle_state_via_warehouse(
        catalog=args.catalog,
        sql_client=_build_client(args.timeout_s, settings),
        record_funnel_snapshot=not args.skip_funnel,
        funnel_sql_path=args.funnel_sql,
    )
    print(json.dumps(result.__dict__, sort_keys=True))


def _load_local_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / ".env.local", override=False)


def _ensure_lakebase_env(settings: object) -> None:
    host = (os.environ.get("LAKEBASE_HOST") or "").strip().lower()
    password = (os.environ.get("LAKEBASE_PASSWORD") or "").strip()
    if host and host not in {"localhost", "127.0.0.1", "::1"} and password:
        return

    workspace_host = settings.databricks_host
    if not workspace_host:
        return
    token = _cli_token(workspace_host)
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
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

    os.environ["LAKEBASE_HOST"] = inst.get("read_write_dns") or os.environ.get("LAKEBASE_HOST", "")
    os.environ["LAKEBASE_PORT"] = os.environ.get("LAKEBASE_PORT", "5432")
    os.environ["LAKEBASE_DATABASE"] = "mip_app_state"
    os.environ["LAKEBASE_USER"] = me.get("userName") or os.environ.get("LAKEBASE_USER", "")
    os.environ["LAKEBASE_PASSWORD"] = cred.get("token") or os.environ.get("LAKEBASE_PASSWORD", "")
    os.environ["LAKEBASE_SSLMODE"] = os.environ.get("LAKEBASE_SSLMODE", "require")


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
