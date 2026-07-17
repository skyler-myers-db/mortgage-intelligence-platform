#!/usr/bin/env python3
"""Exercise negative authorization probes under the agent-runtime identity."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

import requests

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import PermissionDenied
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout
from tools.databricks.agent_runtime_access import assert_current_runtime_identity

_DENIAL_MARKERS = (
    "403",
    "forbidden",
    "permission_denied",
    "permission denied",
    "not authorized",
    "does not have permission",
    "insufficient privileges",
)


def _is_denied(value: object) -> bool:
    return isinstance(value, PermissionDenied) or any(
        marker in str(value).casefold() for marker in _DENIAL_MARKERS
    )


def _expect_denied(label: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except Exception as exc:  # noqa: BLE001 - classify the platform denial
        if _is_denied(exc):
            return
        raise RuntimeError(f"{label} was inconclusive: {type(exc).__name__}: {exc}") from exc
    raise RuntimeError(f"{label} unexpectedly succeeded")


def _verify_app_http_denial(
    workspace: Any,
    *,
    app_url: str,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    response = http_get(
        f"{app_url.rstrip('/')}/api/v1/health",
        headers=dict(workspace.config.authenticate()),
        allow_redirects=False,
        timeout=30,
    )
    if response.status_code not in {401, 403}:
        raise RuntimeError(
            "agent-runtime Databricks App denial probe unexpectedly returned "
            f"status={response.status_code}"
        )


def _verify_warehouse_denial(workspace: Any, *, warehouse_id: str) -> None:
    try:
        response = workspace.statement_execution.execute_statement(
            statement="SELECT 1",
            warehouse_id=warehouse_id,
            wait_timeout="10s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
        )
    except Exception as exc:  # noqa: BLE001 - classify the platform denial
        if _is_denied(exc):
            return
        raise RuntimeError(
            f"agent-runtime SQL warehouse denial was inconclusive: {type(exc).__name__}: {exc}"
        ) from exc
    status = getattr(response, "status", None)
    state_value = getattr(getattr(status, "state", None), "value", getattr(status, "state", ""))
    state = str(state_value or "").split(".")[-1].upper()
    error = getattr(status, "error", None)
    if state in {"FAILED", "CANCELED", "CLOSED"} and _is_denied(error):
        return
    if state == "SUCCEEDED":
        raise RuntimeError("agent-runtime unexpectedly executed SQL on the deployment warehouse")
    raise RuntimeError(
        f"agent-runtime SQL warehouse denial was inconclusive: state={state or 'UNKNOWN'}"
    )


def verify_boundary(
    workspace: Any,
    *,
    expected_application_id: str,
    app_name: str,
    app_url: str,
    protected_service_principal_id: str,
    warehouse_id: str,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    """Prove runtime identity plus App, admin, and warehouse denials."""

    assert_current_runtime_identity(workspace, application_id=expected_application_id)
    _expect_denied(
        "workspace App permission-administration probe",
        lambda: workspace.apps.get_permissions(app_name),
    )
    _verify_app_http_denial(workspace, app_url=app_url, http_get=http_get)
    protected_id = protected_service_principal_id.strip()
    if not protected_id:
        raise RuntimeError("protected App service-principal immutable id is required")
    _expect_denied(
        "service-principal secret-listing probe",
        lambda: list(
            workspace.service_principal_secrets_proxy.list(
                protected_id,
                page_size=1,
            )
        ),
    )
    _verify_warehouse_denial(workspace, warehouse_id=warehouse_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-application-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--protected-service-principal-id", required=True)
    parser.add_argument("--warehouse-id", required=True)
    args = parser.parse_args(argv)
    verify_boundary(
        WorkspaceClient(),
        expected_application_id=args.expected_application_id,
        app_name=args.app_name,
        app_url=args.app_url,
        protected_service_principal_id=args.protected_service_principal_id,
        warehouse_id=args.warehouse_id,
    )
    print("agent-runtime effective negative authorization boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
