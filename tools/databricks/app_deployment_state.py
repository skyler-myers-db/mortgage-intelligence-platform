"""Read-only immutable deployment state helpers for signed App rollback."""

from __future__ import annotations

from typing import Any

from tools.databricks.app_rollback_record_contract import _text


def deployment_state(deployment: object) -> str:
    """Return the normalized Databricks App deployment state."""

    return _text(getattr(getattr(deployment, "status", None), "state", None)).split(".")[-1].upper()


def latest_succeeded(workspace: Any, *, app_name: str) -> object:
    """Return the newest succeeded deployment with an immutable identifier."""

    deployments = [
        deployment
        for deployment in workspace.apps.list_deployments(app_name)
        if deployment_state(deployment) == "SUCCEEDED"
        and _text(getattr(deployment, "deployment_id", None))
    ]
    if not deployments:
        raise RuntimeError("existing App has no succeeded deployment to preserve")
    deployments.sort(
        key=lambda item: (
            _text(getattr(item, "update_time", None)),
            _text(getattr(item, "create_time", None)),
            _text(getattr(item, "deployment_id", None)),
        )
    )
    return deployments[-1]


def active_deployment_id(workspace: Any, *, app_name: str) -> str:
    """Resolve one stable active deployment identifier or fail closed."""

    app = workspace.apps.get(app_name)
    if getattr(app, "pending_deployment", None) is not None:
        raise RuntimeError("App has a pending deployment; rollback identity is not stable")
    active = getattr(app, "active_deployment", None)
    if active is None:
        raise RuntimeError("App has no active deployment to bind")
    if deployment_state(active) == "IN_PROGRESS":
        raise RuntimeError("App active deployment is still in progress")
    deployment_id = _text(getattr(active, "deployment_id", None))
    if not deployment_id:
        raise RuntimeError("App active deployment has no immutable deployment ID")
    return deployment_id


def immutable_source(deployment: object) -> str:
    """Return a reviewed immutable workspace source path."""

    source = _text(
        getattr(getattr(deployment, "deployment_artifacts", None), "source_code_path", None)
    )
    if not source.startswith("/Workspace/Users/") or "/src/" not in source:
        raise RuntimeError("succeeded App deployment has no immutable source artifact")
    return source
