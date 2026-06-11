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


# ---------------------------------------------------------------------------
# 2026-06-11 audit P1-3 (Lakebase half): app-role grants used to live only in
# docs/security/GRANTS.md as a manual psql runbook — a packaging bug on fresh
# workspaces. After schema + seed, we grant the app's Postgres role the same
# matrix GRANTS.md documents. Role discovery is defensive: depending on the
# Databricks Apps database-binding version the provisioned role is named
# after the app, the SP client id, or the SP numeric id — we grant to every
# candidate that exists in pg_roles. action_audit stays append-only (REVOKE
# UPDATE/DELETE). Missing roles are a WARNING, not a failure: on the very
# first deploy ordering the binding may not have provisioned the role yet,
# and the next deploy converges.
# ---------------------------------------------------------------------------
_APP_ROLE_GRANT_TEMPLATES = (
    'GRANT USAGE ON SCHEMA mip_app TO {role}',
    'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mip_app TO {role}',
    'REVOKE UPDATE, DELETE ON TABLE mip_app.action_audit FROM {role}',
)


def _candidate_app_roles() -> list[str]:
    """Return plausible Postgres role names for the app identity."""
    app_name = os.environ.get("MIP_APP_NAME", "mip-app")
    candidates = [app_name]
    try:
        from databricks.sdk import WorkspaceClient

        app = WorkspaceClient().apps.get(app_name)
        for attr in ("service_principal_client_id", "service_principal_name"):
            value = getattr(app, attr, None)
            if value:
                candidates.append(str(value))
        numeric_id = getattr(app, "service_principal_id", None)
        if numeric_id:
            candidates.append(str(numeric_id))
    except Exception as exc:  # noqa: BLE001 -- grants are best-effort here
        print(f"[lakebase-migrate] app SP lookup skipped: {type(exc).__name__}")
    # Preserve order, drop dups/empties.
    seen: set[str] = set()
    ordered = []
    for cand in candidates:
        if cand and cand not in seen:
            seen.add(cand)
            ordered.append(cand)
    return ordered


def _apply_app_role_grants(conn_kwargs: dict) -> None:
    import psycopg
    from psycopg import sql as psql

    candidates = _candidate_app_roles()
    conn = psycopg.connect(**conn_kwargs, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                (candidates,),
            )
            present = [row[0] for row in cur.fetchall()]
            if not present:
                print(
                    "[lakebase-migrate] WARNING: no app role found in pg_roles "
                    f"(candidates: {candidates}); app-role grants skipped. "
                    "Re-run the deploy (or apply docs/security/GRANTS.md "
                    "§Lakebase) once the app's database binding has "
                    "provisioned its role."
                )
                return
            for role in present:
                for template in _APP_ROLE_GRANT_TEMPLATES:
                    statement = template.format(
                        role=psql.Identifier(role).as_string(cur)
                    )
                    cur.execute(statement)
                print(f"[lakebase-migrate] app-role grants applied to {role!r}")
    finally:
        conn.close()


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

    # Grants are applied after (and never instead of) schema + seed; a
    # grant failure must not mask a successful migration, so it exits 0
    # with a loud warning rather than failing the job.
    try:
        _apply_app_role_grants(conn_kwargs)
    except Exception as exc:  # noqa: BLE001 -- best-effort, converges next run
        print(
            "[lakebase-migrate] WARNING: app-role grants failed "
            f"({type(exc).__name__}: {exc}); schema+seed are applied. "
            "See docs/security/GRANTS.md §Lakebase.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
