"""Provision the credential-versioned secret used by the Supervisor proxy.

The one-shot OAuth secret is read only from the process environment and written
directly to a deployment-scoped Databricks secret scope. The served endpoint
receives a secret reference; neither this tool nor the deployment environment
file emits the credential value.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_proxy_secret_scope import (
    MARKER_KEY,
    assert_owned_agent_proxy_scope,
    ensure_owned_agent_proxy_scope,
    validated_scope_binding,
)
from tools.databricks.agentic_env_file import merge_agentic_env_values

CLIENT_ID_ENV = "DATABRICKS_AGENT_PROXY_CLIENT_ID"
CLIENT_SECRET_ENV = "DATABRICKS_AGENT_PROXY_CLIENT_SECRET"
CREDENTIAL_ID_ENV = "DATABRICKS_AGENT_PROXY_CREDENTIAL_ID"
SECRET_REFERENCE_ENV = "MIP_AGENT_PROXY_SECRET_REFERENCE"
_SCOPE_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_CREDENTIAL_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


def credential_key(credential_id: str) -> str:
    value = credential_id.strip()
    if _CREDENTIAL_ID_RE.fullmatch(value) is None:
        raise ValueError("agent-proxy credential ID is invalid")
    return f"oauth-client-secret-{value}"


def secret_reference(*, scope: str, credential_id: str) -> str:
    scope_name = scope.strip()
    if _SCOPE_RE.fullmatch(scope_name) is None:
        raise ValueError("agent-proxy secret scope is invalid")
    return f"{{{{secrets/{scope_name}/{credential_key(credential_id)}}}}}"


def _field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def provision_agent_proxy_secret(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    runtime_application_id: str,
    proxy_application_id: str,
    credential_id: str,
    client_secret: str,
) -> str:
    """Write one versioned credential and prove its isolated scope ACL."""

    binding = validated_scope_binding(
        app_name=app_name,
        scope=scope,
        runtime_application_id=runtime_application_id,
        proxy_application_id=proxy_application_id,
    )
    secret_value = client_secret.strip()
    reference = secret_reference(scope=binding.scope, credential_id=credential_id)
    if len(secret_value) < 32 or any(char.isspace() for char in secret_value):
        raise ValueError("agent-proxy OAuth client secret is invalid")

    ensure_owned_agent_proxy_scope(
        workspace,
        binding=binding,
    )
    workspace.secrets.put_secret(
        scope=binding.scope,
        key=credential_key(credential_id),
        string_value=secret_value,
    )
    keys = assert_owned_agent_proxy_scope(workspace, binding=binding)
    if credential_key(credential_id) not in keys:
        raise RuntimeError("agent-proxy secret key postflight failed")
    return reference


def retire_signed_blue_agent_proxy_secrets(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    runtime_application_id: str,
    proxy_application_id: str,
    retained_credential_id: str,
    retired_credential_ids: tuple[str, ...],
) -> None:
    """Delete only explicit signed-blue secret versions after green retirement."""

    binding = validated_scope_binding(
        app_name=app_name,
        scope=scope,
        runtime_application_id=runtime_application_id,
        proxy_application_id=proxy_application_id,
    )
    retained_key = credential_key(retained_credential_id)
    retired_keys = {
        credential_key(value)
        for value in retired_credential_ids
        if value.strip() != retained_credential_id.strip()
    }
    keys = assert_owned_agent_proxy_scope(workspace, binding=binding)
    if retained_key not in keys:
        raise RuntimeError("active agent-proxy credential key is absent")
    reviewed_keys = {retained_key, *retired_keys, MARKER_KEY}
    if keys - reviewed_keys:
        raise RuntimeError(
            "agent-proxy secret scope contains an untracked credential version; "
            "explicit security reconciliation is required"
        )
    for key in sorted(retired_keys.intersection(keys)):
        workspace.secrets.delete_secret(scope=binding.scope, key=key)
    final = assert_owned_agent_proxy_scope(workspace, binding=binding)
    if final != {MARKER_KEY, retained_key}:
        raise RuntimeError("signed-blue agent-proxy secret retirement postflight failed")


def assert_exact_agent_proxy_secret_inventory(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    runtime_application_id: str,
    proxy_application_id: str,
    retained_credential_id: str,
) -> None:
    """Prove that only the marker and retained proxy secret remain."""

    binding = validated_scope_binding(
        app_name=app_name,
        scope=scope,
        runtime_application_id=runtime_application_id,
        proxy_application_id=proxy_application_id,
    )
    retained_key = credential_key(retained_credential_id)
    if assert_owned_agent_proxy_scope(workspace, binding=binding) != {
        MARKER_KEY,
        retained_key,
    }:
        raise RuntimeError("agent-proxy secret inventory is not exactly retired")


def retire_signed_blue_agent_proxy_credentials(
    workspace: Any,
    *,
    proxy_application_id: str,
    retained_credential_id: str,
    retired_credential_ids: tuple[str, ...],
) -> None:
    """Revoke only explicit signed-blue OAuth credentials, preserving every other ID."""

    proxy_id = proxy_application_id.strip()
    retained_id = retained_credential_id.strip()
    if not proxy_id or _CREDENTIAL_ID_RE.fullmatch(retained_id) is None:
        raise ValueError("agent-proxy identity or retained credential ID is invalid")
    escaped = proxy_id.replace("\\", "\\\\").replace('"', '\\"')
    principals = [
        item
        for item in workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if _field(item, "application_id") == proxy_id
    ]
    if len(principals) != 1:
        raise RuntimeError("expected exactly one agent-proxy service principal")
    principal_id = _field(principals[0], "id")
    if not principal_id:
        raise RuntimeError("agent-proxy service principal has no immutable SCIM id")

    retired_ids = {
        value.strip()
        for value in retired_credential_ids
        if value.strip() and value.strip() != retained_id
    }
    if any(_CREDENTIAL_ID_RE.fullmatch(value) is None for value in retired_ids):
        raise ValueError("signed-blue agent-proxy credential ID is invalid")
    credentials = list(workspace.service_principal_secrets_proxy.list(principal_id))
    credential_ids = [_field(item, "id") for item in credentials]
    if (
        any(not value for value in credential_ids)
        or len(credential_ids) != len(set(credential_ids))
        or retained_id not in credential_ids
    ):
        raise RuntimeError("agent-proxy OAuth credential inventory is invalid")
    untracked_ids = set(credential_ids) - {retained_id} - retired_ids
    if untracked_ids:
        raise RuntimeError(
            "agent-proxy has an untracked OAuth credential; explicit security "
            "reconciliation is required"
        )
    for credential_id in sorted(retired_ids.intersection(credential_ids)):
        workspace.service_principal_secrets_proxy.delete(
            principal_id,
            credential_id,
        )
    final_ids = [
        _field(item, "id")
        for item in workspace.service_principal_secrets_proxy.list(principal_id)
    ]
    if (
        any(not value for value in final_ids)
        or len(final_ids) != len(set(final_ids))
        or set(final_ids) != {retained_id}
    ):
        raise RuntimeError("signed-blue agent-proxy OAuth retirement postflight failed")


def assert_exact_agent_proxy_oauth_inventory(
    workspace: Any,
    *,
    proxy_application_id: str,
    retained_credential_id: str,
) -> None:
    """Prove that the proxy identity has exactly one retained OAuth credential."""

    proxy_id = proxy_application_id.strip()
    retained_id = retained_credential_id.strip()
    if not proxy_id or _CREDENTIAL_ID_RE.fullmatch(retained_id) is None:
        raise ValueError("agent-proxy identity or retained credential ID is invalid")
    escaped = proxy_id.replace("\\", "\\\\").replace('"', '\\"')
    principals = [
        item
        for item in workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if _field(item, "application_id") == proxy_id
    ]
    if len(principals) != 1:
        raise RuntimeError("expected exactly one agent-proxy service principal")
    principal_id = _field(principals[0], "id")
    if not principal_id:
        raise RuntimeError("agent-proxy service principal has no immutable SCIM id")
    credential_ids = [
        _field(item, "id")
        for item in workspace.service_principal_secrets_proxy.list(principal_id)
    ]
    if (
        any(not value for value in credential_ids)
        or len(credential_ids) != len(set(credential_ids))
        or set(credential_ids) != {retained_id}
    ):
        raise RuntimeError("agent-proxy OAuth inventory is not exactly retired")


def cleanup_signed_blue_agent_proxy(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    rollback_scope: str,
    runtime_application_id: str,
    proxy_application_id: str,
    retained_credential_id: str,
    retired_credential_ids: tuple[str, ...],
) -> None:
    """Authorize from signed state, retire providers, then clear the journal."""

    from tools.databricks.app_deployment_rollback import (
        assert_proxy_credential_retirement,
        complete_proxy_credential_retirement,
    )

    binding = validated_scope_binding(
        app_name=app_name,
        scope=scope,
        runtime_application_id=runtime_application_id,
        proxy_application_id=proxy_application_id,
    )
    keys = assert_owned_agent_proxy_scope(workspace, binding=binding)
    if credential_key(retained_credential_id) not in keys:
        raise RuntimeError("active agent-proxy credential key is absent")
    assert_proxy_credential_retirement(
        workspace,
        app_name=app_name,
        scope=rollback_scope,
        proxy_application_id=proxy_application_id,
        retained_credential_id=retained_credential_id,
        retired_credential_ids=retired_credential_ids,
    )
    retire_signed_blue_agent_proxy_credentials(
        workspace,
        proxy_application_id=proxy_application_id,
        retained_credential_id=retained_credential_id,
        retired_credential_ids=retired_credential_ids,
    )
    retire_signed_blue_agent_proxy_secrets(
        workspace,
        app_name=app_name,
        scope=scope,
        runtime_application_id=runtime_application_id,
        proxy_application_id=proxy_application_id,
        retained_credential_id=retained_credential_id,
        retired_credential_ids=retired_credential_ids,
    )

    def _assert_provider_cleanup() -> None:
        assert_exact_agent_proxy_oauth_inventory(
            workspace,
            proxy_application_id=proxy_application_id,
            retained_credential_id=retained_credential_id,
        )
        assert_exact_agent_proxy_secret_inventory(
            workspace,
            app_name=app_name,
            scope=scope,
            runtime_application_id=runtime_application_id,
            proxy_application_id=proxy_application_id,
            retained_credential_id=retained_credential_id,
        )

    complete_proxy_credential_retirement(
        workspace,
        app_name=app_name,
        scope=rollback_scope,
        proxy_application_id=proxy_application_id,
        retained_credential_id=retained_credential_id,
        retired_credential_ids=retired_credential_ids,
        assert_provider_cleanup=_assert_provider_cleanup,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--runtime-application-id", required=True)
    parser.add_argument("--out-env", type=Path)
    parser.add_argument("--cleanup-signed-blue", action="store_true")
    parser.add_argument("--signed-blue-credential-id", action="append", default=[])
    parser.add_argument("--rollback-scope")
    args = parser.parse_args(argv)
    proxy_application_id = os.environ.get(CLIENT_ID_ENV, "")
    credential_id = os.environ.get(CREDENTIAL_ID_ENV, "")
    client_secret = os.environ.get(CLIENT_SECRET_ENV, "")
    if not proxy_application_id or not credential_id:
        parser.error(f"{CLIENT_ID_ENV} and {CREDENTIAL_ID_ENV} are required")
    workspace = WorkspaceClient()
    retired_ids = tuple(args.signed_blue_credential_id)
    if args.cleanup_signed_blue:
        if not args.rollback_scope:
            parser.error("--rollback-scope is required with --cleanup-signed-blue")
        cleanup_signed_blue_agent_proxy(
            workspace,
            app_name=args.app_name,
            scope=args.scope,
            rollback_scope=args.rollback_scope,
            runtime_application_id=args.runtime_application_id,
            proxy_application_id=proxy_application_id,
            retained_credential_id=credential_id,
            retired_credential_ids=retired_ids,
        )
        print("[agent-proxy] explicit signed-blue credential versions retired")
        return 0
    if args.out_env is None:
        parser.error("--out-env is required unless --cleanup-signed-blue is set")
    if retired_ids:
        parser.error("--signed-blue-credential-id requires --cleanup-signed-blue")
    if not client_secret:
        parser.error(f"{CLIENT_SECRET_ENV} is required")
    reference = provision_agent_proxy_secret(
        workspace,
        app_name=args.app_name,
        scope=args.scope,
        runtime_application_id=args.runtime_application_id,
        proxy_application_id=proxy_application_id,
        credential_id=credential_id,
        client_secret=client_secret,
    )
    merge_agentic_env_values(
        args.out_env,
        {SECRET_REFERENCE_ENV: reference},
    )
    print("[agent-proxy] credential-versioned secret reference provisioned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
