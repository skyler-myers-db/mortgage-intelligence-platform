#!/usr/bin/env python3
"""Fail unless authenticated deployed health matches the exact release contract."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

# Direct ``python tools/<script>.py`` execution puts ``tools/`` first on
# sys.path, where the local ``tools/databricks`` package can shadow the
# installed ``databricks`` SDK namespace. Keep both direct and module
# invocation safe because release workflows have historically used both.
_TOOLS_DIR = str(Path(__file__).resolve().parent)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
while _TOOLS_DIR in sys.path:
    sys.path.remove(_TOOLS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from databricks.sdk import WorkspaceClient  # noqa: E402

from tools.databricks.app_health_contract import (  # noqa: E402
    APP_HEALTH_READY_INTERVAL_S,
    APP_HEALTH_READY_TIMEOUT_S,
    AppHealthNotReadyError,
    active_app_deployment_pin,
    assert_active_app_deployment_pin,
    wait_for_authenticated_app_health,
)


def verified_signed_last_good_contract(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
) -> Any:
    """Resolve rollback proof lazily so non-rollback probes stay lightweight."""

    from tools.databricks.app_deployment_rollback import (
        verified_signed_last_good_contract as resolve,
    )

    return resolve(workspace, app_name=app_name, scope=scope)


def verify(
    *,
    workspace: Any,
    app_name: str,
    base_url: str,
    bearer_token: str,
    git_sha: str,
    gateway_binding_sha256: str,
    expected_deployment_lease_id: str | None = None,
    expected_deployment_id: str | None = None,
    client: Any | None = None,
    health_timeout_s: float = APP_HEALTH_READY_TIMEOUT_S,
    health_interval_s: float = APP_HEALTH_READY_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    active_pin = active_app_deployment_pin(
        workspace,
        app_name=app_name,
        expected_lease_id=expected_deployment_lease_id,
    )
    if (
        expected_deployment_id is not None
        and active_pin.deployment_id != expected_deployment_id.strip()
    ):
        raise RuntimeError("active App deployment does not match the signed deployment contract")

    def assert_pin() -> None:
        assert_active_app_deployment_pin(
            workspace,
            app_name=app_name,
            expected=active_pin,
        )

    def report_retry(attempt: int, error: AppHealthNotReadyError, delay_s: float) -> None:
        print(
            "[deploy-contract] App health is transiently unavailable "
            f"(attempt {attempt}: {error}); retrying in {delay_s:g}s",
            file=sys.stderr,
        )

    body = wait_for_authenticated_app_health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
        timeout_s=health_timeout_s,
        interval_s=health_interval_s,
        client=client,
        on_retry=report_retry,
        assert_pinned=assert_pin,
        sleep=sleep,
        monotonic=monotonic,
    )
    if body.get("git_sha") != git_sha:
        raise RuntimeError("deployed App git SHA does not match the expected source commit")
    actual_binding = body.get("agent_gateway_binding_sha256")
    if actual_binding != gateway_binding_sha256:
        raise RuntimeError(
            "deployed App Gateway binding does not match the source-bound live resource contract"
        )
    health_lease_id = body.get("deployment_lease_id")
    if not isinstance(health_lease_id, str) or not health_lease_id.strip():
        raise RuntimeError("deployed App health does not expose its deployment lease")
    health_lease_id = health_lease_id.strip()
    try:
        UUID(health_lease_id)
    except ValueError as exc:
        raise RuntimeError("deployed App health lease is not a valid UUID") from exc
    if active_pin.lease_id is not None and health_lease_id != active_pin.lease_id:
        raise RuntimeError(
            "deployed App health lease does not match the active Databricks App deployment"
        )
    if (
        expected_deployment_lease_id is not None
        and health_lease_id != expected_deployment_lease_id.strip()
    ):
        raise RuntimeError("deployed App lease does not match the expected deployment lease")
    assert_pin()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--app-name", default="mip-app")
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--gateway-binding-sha256", required=True)
    authority = parser.add_mutually_exclusive_group(required=True)
    authority.add_argument("--deployment-lease-id")
    authority.add_argument("--rollback-scope")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        parser.error(f"{args.token_env} is empty")
    workspace = WorkspaceClient()
    expected_lease_id = args.deployment_lease_id
    expected_deployment_id = None
    if args.rollback_scope:
        signed = verified_signed_last_good_contract(
            workspace,
            app_name=args.app_name,
            scope=args.rollback_scope,
        )
        if signed.git_sha != args.git_sha:
            raise RuntimeError("signed last-good App SHA does not match the requested release SHA")
        if signed.gateway_binding_sha256 != args.gateway_binding_sha256:
            raise RuntimeError(
                "signed last-good Gateway binding does not match the requested contract"
            )
        expected_lease_id = signed.deployment_lease_id
        expected_deployment_id = signed.deployment_id
    verify(
        workspace=workspace,
        app_name=args.app_name,
        base_url=args.base_url,
        bearer_token=token,
        git_sha=args.git_sha,
        gateway_binding_sha256=args.gateway_binding_sha256,
        expected_deployment_lease_id=expected_lease_id,
        expected_deployment_id=expected_deployment_id,
    )
    print("[deploy-contract] authenticated App SHA, Gateway binding, and lease match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
