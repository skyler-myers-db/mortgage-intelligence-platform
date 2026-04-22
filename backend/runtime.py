"""Databricks Apps entrypoint: ``python -m backend.runtime``.

Slice-4 invariant: the process refuses to start when live warehouse
credentials are absent. A clear, operator-targeted message explains
which env vars are missing and how to provide them. There is no
MIP_MOCK_MODE fallback; the app runs on real Unity Catalog data or
it fails visibly before serving a single request.
"""
from __future__ import annotations

import os
import sys

import uvicorn

from backend.config.settings import settings


def _preflight_credentials() -> None:
    """Validate warehouse credentials before uvicorn binds a port.

    Running this at startup (not on first request) means an operator
    sees the misconfiguration in the container log within seconds,
    rather than as a 500 at request time.
    """
    # Diagnostic: on Databricks Apps the runtime injects env vars whose
    # names may differ from what the backend reads. Dump presence (not
    # values) of every credential-shaped var so operator triage can
    # see which names the host set without touching any secret string.
    _interesting = [
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
        "DATABRICKS_SQL_WAREHOUSE_ID",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "GENIE_SPACE_ID",
        "DATABRICKS_GENIE_SPACE_ID",
        "PGHOST",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
        "PGPORT",
        "LAKEBASE_HOST",
        "LAKEBASE_USER",
        "LAKEBASE_PASSWORD",
        "LAKEBASE_DATABASE",
        "DATABRICKS_APP_PORT",
    ]
    presence = {k: ("set" if os.environ.get(k) else "unset") for k in _interesting}
    print(f"[mip-runtime] env-presence: {presence}", file=sys.stderr)

    try:
        settings.require_databricks_creds()
    except RuntimeError as exc:
        # Avoid a full traceback -- operators want the one-line guide,
        # not the stack -- and exit with a non-zero code so the
        # Databricks App runtime flags the deployment as failed.
        print(f"[mip-runtime] {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    _preflight_credentials()
    port = int(os.getenv("DATABRICKS_APP_PORT") or os.getenv("UVICORN_PORT") or "8000")
    host = os.getenv("UVICORN_HOST", "0.0.0.0")
    uvicorn.run("backend.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
