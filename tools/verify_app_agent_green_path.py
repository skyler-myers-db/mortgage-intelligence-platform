#!/usr/bin/env python3
"""Prove the deployed App reaches the green Gateway and reviewed planner/data path."""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections.abc import Callable, Mapping
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

from tools.databricks.app_health_contract import (  # noqa: E402
    active_app_deployment_pin,
    assert_active_app_deployment_pin,
    canonical_workspace_app_url,
)

_GREEN_PROBE_TIMEOUT_S = 300.0
_GREEN_PROBE_INTERVAL_S = 5.0
_GREEN_REQUEST_TIMEOUT_S = 120.0
_TRANSIENT_STATUS_CODES = frozenset({502, 503})
_TRANSIENT_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)


class _GreenPathNotReadyError(RuntimeError):
    pass


def validate_green_response(body: object, *, expected_endpoint: str) -> None:
    if not isinstance(body, Mapping):
        raise RuntimeError("green App agent probe returned a non-object payload")
    if body.get("execution_mode") != "agent_framework" or body.get("trace_kind") != (
        "agent_framework"
    ):
        raise RuntimeError("green App agent probe fell back before reaching Agent Responses")
    trusted_assets = body.get("genie_trusted_assets")
    if not isinstance(trusted_assets, list) or not any(
        str(asset) == f"databricks.serving_endpoint.{expected_endpoint}" for asset in trusted_assets
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
    expected_deployment_lease_id: str,
    client: Any | None = None,
    timeout_s: float = _GREEN_PROBE_TIMEOUT_S,
    interval_s: float = _GREEN_PROBE_INTERVAL_S,
    request_timeout_s: float = _GREEN_REQUEST_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    if not math.isfinite(timeout_s) or not 0 <= timeout_s <= _GREEN_PROBE_TIMEOUT_S:
        raise ValueError(
            "green App agent probe timeout must be finite and between "
            f"0 and {_GREEN_PROBE_TIMEOUT_S:g} seconds"
        )
    if not math.isfinite(interval_s) or interval_s <= 0 or interval_s > _GREEN_PROBE_TIMEOUT_S:
        raise ValueError("green App agent probe interval is invalid")
    if (
        not math.isfinite(request_timeout_s)
        or request_timeout_s <= 0
        or request_timeout_s > _GREEN_REQUEST_TIMEOUT_S
    ):
        raise ValueError("green App agent request timeout is invalid")

    active_pin = active_app_deployment_pin(
        workspace,
        app_name=app_name,
        expected_lease_id=expected_deployment_lease_id,
    )

    def assert_pin() -> None:
        assert_active_app_deployment_pin(
            workspace,
            app_name=app_name,
            expected=active_pin,
        )

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
    client = client or httpx.Client(
        timeout=request_timeout_s,
        follow_redirects=False,
    )
    deadline = monotonic() + timeout_s
    attempts = 0
    last_error: _GreenPathNotReadyError | None = None
    try:
        while True:
            remaining_s = deadline - monotonic()
            if attempts > 0 and remaining_s <= 0:
                raise RuntimeError(
                    "green App agent probe did not become ready "
                    f"within {timeout_s:g}s after {attempts} attempt(s)"
                ) from last_error
            attempts += 1
            assert_pin()
            if (
                canonical_workspace_app_url(
                    workspace,
                    app_name=app_name,
                    base_url=base_url,
                )
                != canonical_url
            ):
                raise RuntimeError("workspace App URL changed during green-path proof")
            request_kwargs: dict[str, Any] = {
                "headers": headers,
                "json": payload,
            }
            if owns_client:
                request_kwargs["timeout"] = max(
                    0.001,
                    min(request_timeout_s, max(0.0, remaining_s)),
                )
            try:
                response = client.post(
                    f"{canonical_url}/api/growth-agent/agent/run",
                    **request_kwargs,
                )
            except _TRANSIENT_TRANSPORT_ERRORS as exc:
                transient = _GreenPathNotReadyError(
                    "green App agent probe request failed transiently"
                )
                transient.__cause__ = exc
            except httpx.TransportError as exc:
                raise RuntimeError("green App agent probe request failed permanently") from exc
            else:
                if response.status_code in _TRANSIENT_STATUS_CODES:
                    transient = _GreenPathNotReadyError(
                        f"green App agent probe returned HTTP {response.status_code}"
                    )
                elif response.status_code != 200:
                    raise RuntimeError(
                        f"green App agent probe returned HTTP {response.status_code}"
                    )
                else:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise RuntimeError("green App agent probe returned malformed JSON") from exc
                    validate_green_response(body, expected_endpoint=expected_endpoint)
                    assert_pin()
                    return
            last_error = transient
            assert_pin()
            remaining_s = deadline - monotonic()
            if remaining_s <= 0:
                raise RuntimeError(
                    "green App agent probe did not become ready "
                    f"within {timeout_s:g}s after {attempts} attempt(s)"
                ) from transient
            delay_s = min(interval_s, remaining_s)
            print(
                "[green-path] App endpoint is transiently unavailable "
                f"(attempt {attempts}: {transient}); retrying in {delay_s:g}s",
                file=sys.stderr,
            )
            sleep(delay_s)
    finally:
        if owns_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--app-name", default="mip-app")
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--expected-endpoint", required=True)
    parser.add_argument("--deployment-lease-id", required=True)
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
        expected_deployment_lease_id=args.deployment_lease_id,
    )
    print("[green-path] App Agent Responses and reviewed planner/data path: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
