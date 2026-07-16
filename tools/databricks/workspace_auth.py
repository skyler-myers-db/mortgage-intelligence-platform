"""Explicit deployment-identity binding for Databricks workspace helpers."""

from __future__ import annotations

import os
from collections.abc import Callable

from databricks.sdk import WorkspaceClient

_HOST_ENV = "MIP_DEPLOYER_DATABRICKS_HOST"
_TOKEN_ENV = "MIP_DEPLOYER_DATABRICKS_TOKEN"
_PROFILE_ENV = "MIP_DEPLOYER_DATABRICKS_PROFILE"
APP_FACING_WORKSPACE_AUTH_ENV = frozenset(
    {"DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"}
)


def strip_app_facing_workspace_auth(env: dict[str, str]) -> None:
    """Remove runtime App OAuth keys from a deployment-side child env."""

    for name in APP_FACING_WORKSPACE_AUTH_ENV:
        env.pop(name, None)


def deployment_workspace_client(
    *,
    factory: Callable[..., WorkspaceClient] | None = None,
) -> WorkspaceClient:
    """Build a deployer client without consulting app-facing OAuth variables.

    ``scripts/deploy.sh`` binds either a PAT pair or a reviewed CLI profile to
    dedicated variables before it loads runtime M2M credentials. Other callers
    retain the Databricks SDK's normal default-auth behavior when no dedicated
    deployment binding exists.
    """

    build = factory or WorkspaceClient
    host = os.environ.get(_HOST_ENV, "").strip()
    token = os.environ.get(_TOKEN_ENV, "").strip()
    profile = os.environ.get(_PROFILE_ENV, "").strip()
    if token or host:
        if not token or not host:
            raise RuntimeError("Deployer Databricks PAT binding requires both host and token")
        if profile:
            raise RuntimeError("Deployer Databricks auth cannot bind both PAT and profile")
        return build(host=host, token=token, auth_type="pat")
    if profile:
        return build(profile=profile)
    return build()
