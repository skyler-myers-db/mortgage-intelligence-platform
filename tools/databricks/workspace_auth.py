"""Explicit deployment-identity binding for Databricks workspace helpers."""

from __future__ import annotations

import os
from collections.abc import Callable

from databricks.sdk import WorkspaceClient
from tools.databricks import github_oidc_auth

_HOST_ENV = "MIP_DEPLOYER_DATABRICKS_HOST"
_TOKEN_ENV = "MIP_DEPLOYER_DATABRICKS_TOKEN"
_PROFILE_ENV = "MIP_DEPLOYER_DATABRICKS_PROFILE"
_AMBIENT_PROFILE_ENV = "DATABRICKS_CONFIG_PROFILE"
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
    deployment binding exists. A bound (or, when unbound, the ambient
    ``DATABRICKS_CONFIG_PROFILE``) profile whose ``auth_type`` is
    ``github-oidc`` gets the cached strategy in ``github_oidc_auth``; every
    other PAT or profile binding is built exactly as before.
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
        client = _github_oidc_client(build, profile)
        return build(profile=profile) if client is None else client
    ambient_profile = os.environ.get(_AMBIENT_PROFILE_ENV, "").strip()
    client = _github_oidc_client(build, ambient_profile) if ambient_profile else None
    return build() if client is None else client


def _github_oidc_client(
    build: Callable[..., WorkspaceClient],
    profile: str,
) -> WorkspaceClient | None:
    """Build a ``github-oidc`` profile with the cached strategy, or return None.

    The profile's own host and client_id are passed explicitly so an exported
    ``DATABRICKS_HOST`` or app-facing ``DATABRICKS_CLIENT_ID`` can never redirect
    the token exchange (the SDK lets environment values override a profile).
    The strategy snapshots the GitHub Actions ID-token request here, before any
    ambient-credential scrub, and fails closed when it is absent.
    """

    section = github_oidc_auth.profile_section(profile)
    if section.get("auth_type", "") != github_oidc_auth.AUTH_TYPE:
        return None
    host = section.get("host", "").rstrip("/")
    client_id = section.get("client_id", "")
    if not host or not client_id:
        raise RuntimeError(
            f"github-oidc deployer profile '{profile}' requires host and client_id"
        )
    return build(
        profile=profile,
        host=host,
        client_id=client_id,
        auth_type=github_oidc_auth.AUTH_TYPE,
        credentials_strategy=github_oidc_auth.CachedGitHubOidcCredentials(),
    )
