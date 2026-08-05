"""Databricks App and AI Gateway verifier role resolution."""

from __future__ import annotations

import os

from jobs.lakebase_migration_contracts import (
    _VERIFIER_OPTIONAL_APP_ENVS,
    _VERIFIER_OPTIONAL_BUNDLE_TARGETS,
)


def _resolve_app_role(
    workspace_client: object | None = None,
    *,
    app_name: str | None = None,
) -> str:
    """Resolve the one authoritative Lakebase role for the Databricks App."""
    app_name = app_name or os.environ.get("MIP_APP_NAME", "mip-app")
    if workspace_client is None:
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as exc:
            raise RuntimeError(
                "databricks-sdk is required to resolve the Databricks App role"
            ) from exc
        workspace_client = WorkspaceClient()

    try:
        app = workspace_client.apps.get(app_name)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 -- deployment must fail closed
        raise RuntimeError(
            f"Databricks Apps lookup failed for {app_name!r}: {type(exc).__name__}"
        ) from exc

    client_id = getattr(app, "service_principal_client_id", None)
    role = str(client_id).strip() if client_id is not None else ""
    if not role:
        raise RuntimeError(f"Databricks App {app_name!r} is missing service_principal_client_id")
    return role


def _resolve_ai_gateway_verifier_role(
    explicit_client_id: str | None = None,
    *,
    required: bool = False,
) -> str | None:
    """Resolve the exact verifier writer role, with an explicit remote-job gate."""

    raw_role = (
        explicit_client_id
        if explicit_client_id is not None
        else os.environ.get("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "")
    )
    role = raw_role.strip()
    if role == "00000000PLACEHOLDER" or (role.startswith("<") and role.endswith(">")):
        raise RuntimeError(
            "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID must be a real verifier client ID, "
            "not a bundle placeholder"
        )
    if role:
        return role

    if required:
        raise RuntimeError(
            "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID is required by the remote Lakebase "
            "migration command"
        )

    app_env = os.environ.get("APP_ENV", "local").strip().lower() or "local"
    bundle_target = os.environ.get("DATABRICKS_BUNDLE_TARGET", "").strip().lower()
    if (
        app_env not in _VERIFIER_OPTIONAL_APP_ENVS
        or bundle_target not in _VERIFIER_OPTIONAL_BUNDLE_TARGETS
    ):
        raise RuntimeError(
            "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID is required outside dev/test "
            f"(APP_ENV={app_env!r}, DATABRICKS_BUNDLE_TARGET={bundle_target!r})"
        )
    return None


def _raise_object_inventory_mismatch(
    object_type: str,
    *,
    actual: set[str],
    expected: set[str],
) -> None:
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    raise RuntimeError(
        f"Lakebase {object_type} inventory mismatch: " f"missing={missing}, unexpected={unexpected}"
    )
