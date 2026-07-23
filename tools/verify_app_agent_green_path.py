#!/usr/bin/env python3
"""Prove the deployed App reaches the green Gateway and reviewed planner/data path."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

# Direct ``python tools/<script>.py`` execution puts ``tools/`` first on
# sys.path, where the local ``tools/databricks`` package can shadow the
# installed ``databricks`` SDK namespace. Release probes must behave the same
# under direct and ``python -m`` invocation.
_TOOLS_DIR = str(Path(__file__).resolve().parent)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
while _TOOLS_DIR in sys.path:
    sys.path.remove(_TOOLS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import httpx  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

from tools.databricks.app_health_contract import canonical_workspace_app_url  # noqa: E402


def validate_green_response(body: object, *, expected_endpoint: str) -> None:
    if not isinstance(body, Mapping):
        raise RuntimeError("green App agent probe returned a non-object payload")
    if body.get("execution_mode") != "agent_framework" or body.get("trace_kind") != (
        "agent_framework"
    ):
        raise RuntimeError("green App agent probe fell back before reaching Agent Responses")
    trusted_assets = body.get("genie_trusted_assets")
    if not isinstance(trusted_assets, list) or not any(
        str(asset) == f"databricks.serving_endpoint.{expected_endpoint}"
        for asset in trusted_assets
    ):
        raise RuntimeError("green App agent probe did not cite the expected Gateway endpoint")
    steps = body.get("tool_steps")
    if not isinstance(steps, list) or not any(
        isinstance(step, Mapping)
        and step.get("tool_name") == "fn_build_cohort"
        and step.get("status") == "completed"
        and bool(str(step.get("result_hash") or "").strip())
        for step in steps
    ):
        raise RuntimeError(
            "green App agent probe did not report the reviewed fn_build_cohort planner/data step"
        )


def verify(
    *,
    workspace: Any,
    app_name: str,
    base_url: str,
    bearer_token: str,
    expected_endpoint: str,
    client: Any | None = None,
) -> None:
    canonical_url = canonical_workspace_app_url(
        workspace,
        app_name=app_name,
        base_url=base_url,
    )
    payload = {
        "prompt": "Find prime refinance opportunities for branch review.",
        "request_id": str(uuid4()),
        "save_monitor": False,
    }
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=180, follow_redirects=False)
    try:
        response = client.post(
            f"{canonical_url}/api/growth-agent/agent/run",
            headers=headers,
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"green App agent probe returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        validate_green_response(response.json(), expected_endpoint=expected_endpoint)
    finally:
        if owns_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--app-name", default="mip-app")
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--expected-endpoint", required=True)
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        parser.error(f"{args.token_env} is empty")
    verify(
        workspace=WorkspaceClient(),
        app_name=args.app_name,
        base_url=args.base_url,
        bearer_token=token,
        expected_endpoint=args.expected_endpoint,
    )
    print("[green-path] App Agent Responses and reviewed planner/data path: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
