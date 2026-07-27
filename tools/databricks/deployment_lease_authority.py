"""Resolve the signed App deployment lease used at remote mutation boundaries."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from tools.databricks import app_deployment_lease


def held_assertion_from_env(
    workspace: Any,
    *,
    operation: str,
) -> Callable[[], None]:
    """Return and initially verify the exact deployment lease from the environment."""

    app_name = os.environ.get("MIP_APP_NAME", "").strip()
    lease_id = os.environ.get("MIP_APP_DEPLOYMENT_LEASE_ID", "").strip()
    source_git_sha = os.environ.get("MIP_DEPLOYMENT_SOURCE_GIT_SHA", "").strip()
    if not app_name or not lease_id or not source_git_sha:
        raise RuntimeError(
            f"{operation} requires the exact signed App deployment lease"
        )
    assertion = app_deployment_lease.held_assertion(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )
    assertion()
    return assertion
