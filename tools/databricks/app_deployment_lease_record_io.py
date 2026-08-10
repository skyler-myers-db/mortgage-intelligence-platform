"""Bounded read of one signed App-deployment-lease record.

Split out of ``app_deployment_lease`` when adding the bounded/retried
transport pushed that module past the size gate (2026-08-10). The verifier
is injected so the signing-key policy stays in the lease module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from databricks.sdk.errors import NotFound
from databricks.sdk.errors.platform import ResourceDoesNotExist
from tools.databricks import app_deployment_lease_support as lease_support
from tools.databricks.probe_deadlines import bounded_workspace_read


def read_record(
    workspace: Any,
    *,
    path: str,
    app_name: str,
    verify: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    """Download, verify, and path-bind one lease record, or None if absent."""

    try:
        # Bounded, retried download — the streaming read stalls on held-open
        # responses (2026-08-10 capture); see probe_deadlines.
        encoded = bounded_workspace_read(workspace, path)
    except (NotFound, ResourceDoesNotExist):
        return None
    record = verify(
        lease_support.json_without_duplicate_keys(
            encoded,
            artifact="App deployment lease",
        )
    )
    if record.get("app_name") != app_name.strip():
        raise RuntimeError("App deployment lease path binding is invalid")
    return record
