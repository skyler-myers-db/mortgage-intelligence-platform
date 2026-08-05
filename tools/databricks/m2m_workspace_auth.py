"""Bind one exact workspace M2M identity after capturing admin authority."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

_AMBIENT_AUTH_KEYS = (
    "ARM_CLIENT_ID",
    "ARM_CLIENT_SECRET",
    "ARM_ENVIRONMENT",
    "ARM_TENANT_ID",
    "ARM_USE_MSI",
    "DATABRICKS_ACCOUNT_CLIENT_ID",
    "DATABRICKS_ACCOUNT_CLIENT_SECRET",
    "DATABRICKS_ACCOUNT_ID",
    "DATABRICKS_AZURE_RESOURCE_ID",
    "DATABRICKS_CLOUD",
    "DATABRICKS_CONFIG_FILE",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_DISCOVERY_URL",
    "DATABRICKS_GOOGLE_SERVICE_ACCOUNT",
    "DATABRICKS_METADATA_SERVICE_URL",
    "DATABRICKS_OIDC_TOKEN_ENV",
    "DATABRICKS_OIDC_TOKEN_FILE",
    "DATABRICKS_OIDC_TOKEN_FILEPATH",
    "DATABRICKS_PASSWORD",
    "DATABRICKS_TOKEN",
    "DATABRICKS_TOKEN_AUDIENCE",
    "DATABRICKS_USERNAME",
    "DATABRICKS_WORKSPACE_ID",
    "GOOGLE_CREDENTIALS",
    "MIP_DEPLOYER_DATABRICKS_HOST",
    "MIP_DEPLOYER_DATABRICKS_PROFILE",
    "MIP_DEPLOYER_DATABRICKS_TOKEN",
)
_SENSITIVE_ENV_MARKERS = (
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "SIGNING_KEY",
    "TOKEN",
)
_WORKSPACE_HOST_SUFFIXES = (".databricks.com", ".azuredatabricks.net")
_ACCOUNT_HOSTNAMES = {
    "accounts.azuredatabricks.net",
    "accounts.cloud.databricks.com",
    "accounts.gcp.databricks.com",
}


def _text(value: object, name: str) -> str:
    raw = getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _workspace_origin(value: str, *, label: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or not parsed.hostname
        or not parsed.hostname.endswith(_WORKSPACE_HOST_SUFFIXES)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            f"{label} verifier workspace host is not a reviewed HTTPS "
            "Databricks origin"
        )
    return value.strip().rstrip("/")


def reviewed_databricks_account_origin(value: str, *, label: str) -> str:
    """Return one canonical account-control-plane origin or fail closed."""

    try:
        origin = _workspace_origin(value, label=label)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(
            f"{label} is not a reviewed Databricks account origin"
        ) from exc
    if urlsplit(origin).hostname not in _ACCOUNT_HOSTNAMES:
        raise RuntimeError(f"{label} is not a reviewed Databricks account origin")
    return origin


def _ambient_authority_keys() -> set[str]:
    keys = set(_AMBIENT_AUTH_KEYS)
    oidc_value_env = os.environ.get("DATABRICKS_OIDC_TOKEN_ENV", "").strip()
    if oidc_value_env:
        keys.add(oidc_value_env)
    keys.update(
        key
        for key in os.environ
        if any(marker in key.upper() for marker in _SENSITIVE_ENV_MARKERS)
    )
    return keys


def bind_exact_workspace_m2m_auth(
    *,
    admin_workspace: Any,
    expected_application_id: str,
    client_id_env: str,
    client_secret_env: str,
    label: str,
) -> tuple[str, str]:
    """Replace ambient workspace auth with one explicitly named M2M identity."""

    expected = expected_application_id.strip()
    client_id = os.environ.get(client_id_env, "").strip()
    client_secret = os.environ.get(client_secret_env, "").strip()
    host = _text(getattr(admin_workspace, "config", None), "host")
    if not expected or client_id != expected or not client_secret or not host:
        raise RuntimeError(
            f"{label} verifier lacks its exact OAuth credential or workspace host"
        )
    host = _workspace_origin(host, label=label)
    for key in _ambient_authority_keys():
        os.environ.pop(key, None)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_AUTH_TYPE"] = "oauth-m2m"
    os.environ["DATABRICKS_CLIENT_ID"] = client_id
    os.environ["DATABRICKS_CLIENT_SECRET"] = client_secret
    os.environ["MIP_DISABLE_DOTENV"] = "1"
    return client_id, client_secret
