"""Lakebase SDK connection resolution and repository discovery."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast


def _resolve_connection(
    *,
    instance_name: str | None = None,
    database_name: str | None = None,
) -> dict:
    """Return a dict of connection kwargs for psycopg.connect.

    Prefers env-var overrides; otherwise uses the Databricks SDK with
    the ambient workspace identity to resolve the DNS + fetch a fresh
    Postgres credential.
    """
    instance_name = instance_name or os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    database_name = database_name or os.environ.get("LAKEBASE_DATABASE", "mip_app_state")
    host = os.environ.get("LAKEBASE_HOST")
    user = os.environ.get("LAKEBASE_USER")
    password = os.environ.get("LAKEBASE_PASSWORD")

    if host and user and password:
        return {
            "host": host,
            "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
            "dbname": database_name,
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
                "[lakebase-migrate] could not resolve current workspace " "identity user_name.",
                file=sys.stderr,
            )
            sys.exit(3)

        # Resolve via raw REST rather than the typed ``w.database`` service.
        # Older databricks-sdk builds (e.g. the baseline shipped with
        # serverless py_default) don't expose ``database`` as a typed
        # attribute; the underlying REST endpoints are stable, so
        # ``api_client.do`` is the portable surface.
        inst = cast(
            dict[str, Any],
            w.api_client.do("GET", f"/api/2.0/database/instances/{instance_name}"),
        )
        resolved_host = host or inst.get("read_write_dns")
        if not resolved_host:
            print(
                f"[lakebase-migrate] instance {instance_name!r} has no "
                f"read_write_dns; check provisioning state.",
                file=sys.stderr,
            )
            sys.exit(3)

        cred = cast(
            dict[str, Any],
            w.api_client.do(
                "POST",
                "/api/2.0/database/credentials",
                body={
                    "request_id": (
                        f"mip-lakebase-migrate-"
                        f"{os.environ.get('DATABRICKS_JOB_RUN_ID','local')}"
                    ),
                    "instance_names": [instance_name],
                },
            ),
        )
        cred_token = cred.get("token")
        if not cred_token:
            print(
                "[lakebase-migrate] credential response missing token.",
                file=sys.stderr,
            )
            sys.exit(3)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 -- normalize SDK failures without reflecting secrets
        print(
            "[lakebase-migrate] SDK auth/resolution failed; verify workspace "
            "authentication, Lakebase instance access, and database credential permissions.",
            file=sys.stderr,
        )
        sys.exit(3)

    return {
        "host": resolved_host,
        "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
        "dbname": database_name,
        "user": user or identity,
        "password": password or cred_token,
        "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
    }


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
