"""Apply Lakebase schema + Summit Mortgage campaign seed.

Runs as a Databricks Jobs Python task (``mip_lakebase_migrate`` in
``databricks.yml``). Opens a psycopg3 connection to the
``mip-app-state`` Lakebase instance and executes, in order:

    1. ``lakebase/schema.sql`` -- idempotent DDL (CREATE ... IF NOT
       EXISTS, CREATE INDEX IF NOT EXISTS) for campaigns, approvals,
       action_audit, archive-run/migration ledgers, agent_sessions, feedback.
    2. ``lakebase/seed_campaigns.sql`` -- idempotent seed (stable
       UUIDs + ON CONFLICT DO NOTHING) for the Summit Mortgage
       campaigns + five sample approvals.

Auth model (self-contained, no env-var plumbing required):
    On Databricks the task runs under the workspace identity (service
    principal when deployed; user identity for `bundle run`). We fetch a
    fresh short-lived Postgres credential via
    ``WorkspaceClient().database.generate_database_credential(...)``
    and use the identity's user_name as the Postgres user. This avoids
    the OAuth-token-expiry problem of stuffing a long-lived password
    into .env.local / secret scope.

Env-var overrides (optional; used for local runs off Databricks):
    LAKEBASE_HOST              -- DNS name; otherwise resolved from the
                                  ``mip-app-state`` database_instance.
    LAKEBASE_USER              -- Postgres user; otherwise the current
                                  Databricks identity's user_name.
    LAKEBASE_PASSWORD          -- Postgres password; otherwise fetched
                                  via generate_database_credential.
    LAKEBASE_DATABASE          -- default ``mip_app_state``.
    LAKEBASE_PORT              -- default 5432.
    LAKEBASE_SSLMODE           -- default ``require``.
    LAKEBASE_INSTANCE_NAME     -- default ``mip-app-state``.

Exit codes:
    0 -- schema + seed applied cleanly.
    2 -- psycopg / Postgres error (full error printed for debugging).
    3 -- SDK / auth error resolving connection parameters.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_connection() -> dict:
    """Return a dict of connection kwargs for psycopg.connect.

    Prefers env-var overrides; otherwise uses the Databricks SDK with
    the ambient workspace identity to resolve the DNS + fetch a fresh
    Postgres credential.
    """
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    host = os.environ.get("LAKEBASE_HOST")
    user = os.environ.get("LAKEBASE_USER")
    password = os.environ.get("LAKEBASE_PASSWORD")

    if host and user and password:
        return {
            "host": host,
            "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
            "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
            "user": user,
            "password": password,
            "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
        }

    # SDK-based resolution. Import lazily so --help doesn't require the
    # wheel and so local CI doesn't need the SDK unless resolution is
    # actually needed.
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        print(
            "[lakebase-migrate] databricks-sdk is not installed; either "
            "install it (`pip install databricks-sdk`) or set the "
            "LAKEBASE_* env vars explicitly.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        identity = me.user_name or me.display_name
        if not identity:
            print(
                "[lakebase-migrate] could not resolve current workspace "
                "identity user_name.",
                file=sys.stderr,
            )
            sys.exit(3)

        # Resolve via raw REST rather than the typed ``w.database`` service.
        # Older databricks-sdk builds (e.g. the baseline shipped with
        # serverless py_default) don't expose ``database`` as a typed
        # attribute; the underlying REST endpoints are stable, so
        # ``api_client.do`` is the portable surface.
        inst = w.api_client.do("GET", f"/api/2.0/database/instances/{instance_name}")
        resolved_host = host or inst.get("read_write_dns")
        if not resolved_host:
            print(
                f"[lakebase-migrate] instance {instance_name!r} has no "
                f"read_write_dns; check provisioning state.",
                file=sys.stderr,
            )
            sys.exit(3)

        cred = w.api_client.do(
            "POST",
            "/api/2.0/database/credentials",
            body={
                "request_id": (
                    f"mip-lakebase-migrate-"
                    f"{os.environ.get('DATABRICKS_JOB_RUN_ID','local')}"
                ),
                "instance_names": [instance_name],
            },
        )
        cred_token = cred.get("token")
        if not cred_token:
            print(
                f"[lakebase-migrate] credential response missing token: {cred}",
                file=sys.stderr,
            )
            sys.exit(3)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- operator-facing
        print(
            f"[lakebase-migrate] SDK auth/resolution failed: {exc}",
            file=sys.stderr,
        )
        sys.exit(3)

    return {
        "host": resolved_host,
        "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
        "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
        "user": user or identity,
        "password": password or cred_token,
        "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
    }


def _run(sql_text: str, conn_kwargs: dict) -> None:
    import psycopg  # local import so `--help` still works without the wheel

    # psycopg 3 supports multi-statement exec via raw cursor.execute on
    # the whole text in server-side "simple query" mode. The schema +
    # seed SQL is idempotent so a partial-apply + re-run is safe.
    conn = psycopg.connect(
        **conn_kwargs,
        autocommit=True,  # DDL / SEED runs are whole-program idempotent
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
    finally:
        conn.close()


def _repo_root() -> Path:
    """Return the repo root. Tolerates Databricks ipykernel exec
    contexts where ``__file__`` is not defined (the exec() path strips
    the module's ``__file__`` attribute)."""
    try:
        return Path(__file__).resolve().parents[1]
    except NameError as exc:
        # Databricks workspace runs upload the bundle under a known prefix.
        # Fall back to cwd + a couple of likely locations.
        for candidate in (Path.cwd(), Path.cwd() / "..", Path("/Workspace/Users")):
            for probe in candidate.rglob("lakebase/schema.sql"):
                return probe.parents[1]
        raise RuntimeError(
            "Cannot locate repo root — __file__ undefined and no lakebase/"
            "schema.sql found under cwd."
        ) from exc


def main() -> None:
    conn_kwargs = _resolve_connection()
    repo_root = _repo_root()
    schema_sql = (repo_root / "lakebase" / "schema.sql").read_text(encoding="utf-8")
    seed_sql = (repo_root / "lakebase" / "seed_campaigns.sql").read_text(
        encoding="utf-8"
    )

    try:
        _run(schema_sql, conn_kwargs)
        print("[lakebase-migrate] schema applied")
        _run(seed_sql, conn_kwargs)
        print("[lakebase-migrate] Summit Mortgage seed applied")
    except Exception as exc:  # noqa: BLE001 -- operator-facing failure
        print(f"[lakebase-migrate] failed: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
