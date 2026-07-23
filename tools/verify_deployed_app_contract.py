#!/usr/bin/env python3
"""Fail unless authenticated deployed health matches the exact release contract."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
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

from tools.databricks.app_health_contract import authenticated_app_health  # noqa: E402

_DEPLOYMENT_LEASE_ENV = "MIP_APP_DEPLOYMENT_LEASE_ID"


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _deployment_id(value: object) -> str:
    return str(_field(value, "deployment_id") or "").strip()


def _active_deployment_lease_id(workspace: Any, *, app_name: str) -> str:
    apps = workspace.apps
    app = apps.get(app_name)
    active_id = _deployment_id(_field(app, "active_deployment"))
    if not active_id:
        raise RuntimeError("Databricks App has no exact active deployment identity")
    get_deployment = getattr(apps, "get_deployment", None)
    if not callable(get_deployment):
        raise RuntimeError("Databricks Apps client cannot read the active deployment")
    deployment = get_deployment(app_name, active_id)
    if _deployment_id(deployment) != active_id:
        raise RuntimeError("Databricks App returned a different active deployment")
    raw_env_vars = _field(deployment, "env_vars")
    if raw_env_vars is not None and not isinstance(raw_env_vars, list):
        raise RuntimeError("active Databricks App deployment environment is invalid")
    env_vars = raw_env_vars or []
    matching = [
        item
        for item in env_vars
        if str(_field(item, "name") or "") == _DEPLOYMENT_LEASE_ENV
    ]
    if len(matching) != 1:
        raise RuntimeError(
            f"active Databricks App deployment must contain exactly one {_DEPLOYMENT_LEASE_ENV}"
        )
    lease_id = str(_field(matching[0], "value") or "").strip()
    if _field(matching[0], "value_from") is not None:
        raise RuntimeError("active Databricks App deployment lease must be a literal value")
    try:
        UUID(lease_id)
    except ValueError as exc:
        raise RuntimeError("active Databricks App deployment lease is invalid") from exc
    if _deployment_id(_field(apps.get(app_name), "active_deployment")) != active_id:
        raise RuntimeError("Databricks App active deployment changed during lease verification")
    return lease_id


def verify(
    *,
    workspace: Any,
    app_name: str,
    base_url: str,
    bearer_token: str,
    git_sha: str,
    gateway_binding_sha256: str,
    expected_deployment_lease_id: str | None = None,
    client: Any | None = None,
) -> None:
    body = authenticated_app_health(
        workspace,
        app_name=app_name,
        base_url=base_url,
        bearer_token=bearer_token,
        client=client,
    )
    if body.get("git_sha") != git_sha:
        raise RuntimeError(
            f"deployed app git_sha is {body.get('git_sha')!r}, expected {git_sha!r}"
        )
    actual_binding = body.get("agent_gateway_binding_sha256")
    if actual_binding != gateway_binding_sha256:
        raise RuntimeError(
            "deployed App Gateway binding does not match the source-bound live resource contract"
        )
    health_lease_id = body.get("deployment_lease_id")
    if not isinstance(health_lease_id, str) or not health_lease_id.strip():
        raise RuntimeError("deployed App health does not expose its deployment lease")
    health_lease_id = health_lease_id.strip()
    active_lease_id = _active_deployment_lease_id(workspace, app_name=app_name)
    if health_lease_id != active_lease_id:
        raise RuntimeError(
            "deployed App health lease does not match the active Databricks App deployment"
        )
    if (
        expected_deployment_lease_id is not None
        and health_lease_id != expected_deployment_lease_id.strip()
    ):
        raise RuntimeError("deployed App lease does not match the expected deployment lease")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--app-name", default="mip-app")
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--gateway-binding-sha256", required=True)
    parser.add_argument("--deployment-lease-id")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        parser.error(f"{args.token_env} is empty")
    verify(
        workspace=WorkspaceClient(),
        app_name=args.app_name,
        base_url=args.base_url,
        bearer_token=token,
        git_sha=args.git_sha,
        gateway_binding_sha256=args.gateway_binding_sha256,
        expected_deployment_lease_id=args.deployment_lease_id,
    )
    print("[deploy-contract] authenticated App SHA, Gateway binding, and lease match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
