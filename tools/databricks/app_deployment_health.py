"""Pinned, bounded authenticated health proof for App deployment lifecycle work."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from tools.databricks.app_health_contract import (
    APP_HEALTH_READY_INTERVAL_S,
    APP_HEALTH_READY_TIMEOUT_S,
    ActiveAppDeploymentPin,
    assert_active_app_deployment_pin,
    wait_for_authenticated_app_health,
)


def health(
    workspace: Any,
    *,
    app_name: str,
    base_url: str,
    bearer_token: str,
    expected_pin: ActiveAppDeploymentPin,
) -> tuple[str, str | None, str]:
    body = wait_for_authenticated_app_health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
        timeout_s=APP_HEALTH_READY_TIMEOUT_S,
        interval_s=APP_HEALTH_READY_INTERVAL_S,
        assert_pinned=lambda: assert_active_app_deployment_pin(
            workspace,
            app_name=app_name,
            expected=expected_pin,
        ),
    )
    git_sha = str(body.get("git_sha") or "").strip()
    binding = body.get("agent_gateway_binding_sha256")
    lease_id = str(body.get("deployment_lease_id") or "").strip()
    if len(git_sha) != 40:
        raise RuntimeError("App health did not expose an exact deployment SHA")
    if binding is not None and (not isinstance(binding, str) or len(binding) != 64):
        raise RuntimeError("App health exposed an invalid Gateway binding")
    try:
        UUID(lease_id)
    except ValueError as exc:
        raise RuntimeError("App health did not expose a valid deployment lease") from exc
    assert_active_app_deployment_pin(
        workspace,
        app_name=app_name,
        expected=expected_pin,
    )
    return git_sha, binding, lease_id
