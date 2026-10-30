"""Databricks Apps entrypoint: ``python -m backend.runtime``.

Startup invariant: the process refuses to start when live warehouse
credentials are absent. A clear, operator-targeted message explains
which env vars are missing and how to provide them. The app runs on
real Unity Catalog data in every environment and fails visibly before
serving a single request when a required credential is missing.

Lender identity invariant: a customer lender is a digest-pinned record
(``MIP_LENDER_IDENTITY_JSON`` + ``MIP_LENDER_IDENTITY_SHA256``,
``backend/schemas/lender_identity_record.py``). When either is set, the
record must verify against its reviewed digest before settings load, or
the process refuses to start with one line that names no value. With
neither set, this check does nothing and settings validate the
code-registry identity exactly as before.
"""
from __future__ import annotations

import os
import sys

import uvicorn

from backend.schemas.lender_identity_record import (
    LenderIdentityRefused,
    pinned_identity_configured,
    reviewed_lender_identity_from_env,
)


def _preflight_lender_identity() -> None:
    """Refuse a customer-class boot whose pinned lender record does not verify."""

    if not pinned_identity_configured(os.environ):
        return
    try:
        identity = reviewed_lender_identity_from_env(os.environ)
    except ValueError as exc:
        # Every pinned-path refusal is a fixed sentence without a value.
        reason = str(exc) if isinstance(exc, LenderIdentityRefused) else "the record did not verify"
        print(f"[mip-runtime] refusing to start: lender identity: {reason}", file=sys.stderr)
        sys.exit(1)
    assert identity.digest is not None
    print(
        f"[mip-runtime] lender identity: digest-pinned record {identity.digest[:12]} verified",
        file=sys.stderr,
    )


def _preflight_credentials() -> None:
    """Validate warehouse credentials before uvicorn binds a port.

    Running this at startup (not on first request) means an operator
    sees the misconfiguration in the container log within seconds,
    rather than as a 500 at request time.
    """
    # Imported here, after the lender identity check, so a refused record
    # stops the boot with its one-line reason, not a settings traceback.
    from backend.config.settings import settings

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
    _preflight_lender_identity()
    _preflight_credentials()
    port = int(os.getenv("DATABRICKS_APP_PORT") or os.getenv("UVICORN_PORT") or "8000")
    host = os.getenv("UVICORN_HOST", "0.0.0.0")
    uvicorn.run("backend.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
