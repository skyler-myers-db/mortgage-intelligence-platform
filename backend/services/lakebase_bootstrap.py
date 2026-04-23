"""One-shot runtime bootstrap for Lakebase schema drift.

The mip_lakebase_migrate Databricks Job is the canonical owner of
``lakebase/schema.sql`` -- it runs on bundle deploy and applies every
``CREATE ... IF NOT EXISTS`` / ``ALTER ... IF NOT EXISTS`` idempotently.
But we ship small, targeted DDLs (e.g. R5-01 idempotency key) between
deploys too, and the first HTTP path that depends on the new column
would otherwise 500 until the SE remembered to re-run the job.

This module runs those small, retry-safe DDLs once per process on the
first call to :func:`ensure_approval_idempotency_column`. Failure is
non-fatal -- we log a warning and let the call path fall through; the
downstream INSERT will either succeed (column already present) or fail
with a clear psycopg error the operator can diagnose.

Design rules:

* **Idempotent DDL only.** Every statement uses IF NOT EXISTS / partial
  unique index shape so a re-run on an already-migrated database is a
  no-op.
* **Per-process memoized.** A module-level flag guards a second
  execution; the bootstrap runs on first approve/reject per process.
* **Never silently papers over a deeper outage.** If the DDL itself
  raises ``LakebaseError`` we log and move on -- the calling INSERT
  will surface the real failure as 503.
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit

log = logging.getLogger(__name__)


# R5-01 DDL -- matches sql/ddl/lakebase_add_request_id.sql. Keep the two
# in sync; the SE-facing standalone file exists so operators can apply
# the migration without re-running the whole schema.sql.
_APPROVAL_REQUEST_ID_DDL: tuple[str, ...] = (
    "ALTER TABLE mip_app.approvals ADD COLUMN IF NOT EXISTS request_id TEXT",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_request_id "
        "ON mip_app.approvals (request_id) WHERE request_id IS NOT NULL"
    ),
)


_LOCK = Lock()
_APPROVAL_REQUEST_ID_BOOTSTRAPPED: bool = False


def ensure_approval_idempotency_column(client: LakebaseClient) -> None:
    """Apply the R5-01 ``request_id`` DDL once per process.

    Safe to call on every approve/reject -- the internal flag makes the
    second and every subsequent call a pure no-op (no Lakebase round-
    trip). Failures are logged at WARNING and swallowed: the caller's
    INSERT is the next thing to run and will report any real outage.
    """
    global _APPROVAL_REQUEST_ID_BOOTSTRAPPED
    if _APPROVAL_REQUEST_ID_BOOTSTRAPPED:
        return
    with _LOCK:
        if _APPROVAL_REQUEST_ID_BOOTSTRAPPED:
            return
        try:
            for stmt in _APPROVAL_REQUEST_ID_DDL:
                client.execute(stmt)
            emit(
                log,
                "lakebase_bootstrap_applied",
                migration="r5_01_approvals_request_id",
                statements=len(_APPROVAL_REQUEST_ID_DDL),
            )
        except LakebaseError as exc:
            # Not fatal. If the column is actually missing, the caller's
            # INSERT will raise a second LakebaseError and surface as
            # 503; the operator sees both log lines together.
            log.warning(
                "lakebase_bootstrap failed: migration=r5_01_approvals_request_id "
                "exc=%s",
                type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001 -- bootstrap must never crash request path
            log.warning(
                "lakebase_bootstrap unexpected failure: migration=r5_01_approvals_request_id "
                "exc=%s",
                type(exc).__name__,
            )
        # Flag is flipped on BOTH success and failure. A second try per
        # process wouldn't fix a persistent outage; the Databricks Job
        # is the real remediation path. Operators see the WARNING.
        _APPROVAL_REQUEST_ID_BOOTSTRAPPED = True


def _reset_bootstrap_for_tests() -> None:
    """Test helper -- clear the per-process flag between tests."""
    global _APPROVAL_REQUEST_ID_BOOTSTRAPPED
    _APPROVAL_REQUEST_ID_BOOTSTRAPPED = False


def _bootstrap_state_for_tests() -> dict[str, Any]:
    """Test helper -- read the current bootstrap flag."""
    return {"request_id_bootstrapped": _APPROVAL_REQUEST_ID_BOOTSTRAPPED}
