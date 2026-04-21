"""Apply Lakebase schema + demo-campaign seed.

Runs as a Databricks Jobs Python task (``mip_lakebase_migrate`` in
``databricks.yml``). Opens a psycopg3 connection to the
``mip_app_state`` Lakebase instance and executes, in order:

    1. ``lakebase/schema.sql`` -- idempotent DDL (CREATE ... IF NOT
       EXISTS, CREATE INDEX IF NOT EXISTS) for campaigns, approvals,
       action_audit, agent_sessions, feedback.
    2. ``lakebase/seed_demo_campaigns.sql`` -- idempotent seed (stable
       UUIDs + ON CONFLICT DO NOTHING) for the Summit Mortgage
       campaigns + five sample approvals.

The task reads connection parameters from the job's environment. For
the Databricks Apps deploy path these are provided by the Lakebase
resource binding in ``resources/apps.yml``; for a manual local run,
source them from ``.env.local``.

Required env vars (raise RuntimeError on missing):
    LAKEBASE_HOST
    LAKEBASE_USER
    LAKEBASE_PASSWORD

Optional env vars (defaulted):
    LAKEBASE_PORT         5432
    LAKEBASE_DATABASE     mip_app_state
    LAKEBASE_SSLMODE      require

Exit codes:
    0 -- schema + seed applied cleanly.
    1 -- missing env var (operator-facing message, no traceback).
    2 -- psycopg / Postgres error (full error printed for debugging).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(
            f"[lakebase-migrate] missing required env var {name}. "
            f"Set it in the job environment or export from .env.local.",
            file=sys.stderr,
        )
        sys.exit(1)
    return v


def _run(sql_text: str, host: str, port: int, database: str, user: str,
         password: str, sslmode: str) -> None:
    import psycopg  # local import so `--help` still works without the wheel

    # psycopg 3 supports multi-statement exec via raw cursor.execute on
    # the whole text in server-side "simple query" mode. The schema +
    # seed SQL is idempotent so a partial-apply + re-run is safe.
    conn = psycopg.connect(
        host=host,
        port=port,
        dbname=database,
        user=user,
        password=password,
        sslmode=sslmode,
        autocommit=True,  # DDL / SEED runs are whole-program idempotent
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
    finally:
        conn.close()


def main() -> None:
    host = _require("LAKEBASE_HOST")
    user = _require("LAKEBASE_USER")
    password = _require("LAKEBASE_PASSWORD")
    port = int(os.environ.get("LAKEBASE_PORT", "5432"))
    database = os.environ.get("LAKEBASE_DATABASE", "mip_app_state")
    sslmode = os.environ.get("LAKEBASE_SSLMODE", "require")

    repo_root = Path(__file__).resolve().parents[1]
    schema_sql = (repo_root / "lakebase" / "schema.sql").read_text(encoding="utf-8")
    seed_sql = (repo_root / "lakebase" / "seed_demo_campaigns.sql").read_text(
        encoding="utf-8"
    )

    try:
        _run(schema_sql, host, port, database, user, password, sslmode)
        print("[lakebase-migrate] schema applied")
        _run(seed_sql, host, port, database, user, password, sslmode)
        print("[lakebase-migrate] demo seed applied")
    except Exception as exc:  # noqa: BLE001 -- operator-facing failure
        print(f"[lakebase-migrate] failed: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
