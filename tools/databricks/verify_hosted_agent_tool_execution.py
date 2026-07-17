#!/usr/bin/env python3
"""Prove the green Gateway executes the reviewed hosted cohort tool."""

from __future__ import annotations

import argparse
from uuid import uuid4

from backend.services.capability_serving_probes import (
    query_serving_endpoint_with_proof,
)
from tools.databricks.ai_gateway_tool_trace import (
    TOOL_PROBE_PROMPT,
    response_proves_build_cohort_tool,
    warm_endpoint_with_cold_start_patience,
)
from tools.databricks.verify_ai_gateway_exact_proof import _workspace_client


def verify_hosted_tool_execution(
    workspace: object,
    *,
    endpoint: str,
    expected_count: int,
    catalog: str,
) -> None:
    if not endpoint.strip() or expected_count < 0 or not catalog.strip():
        raise ValueError("endpoint, catalog, and a non-negative expected count are required")
    details = workspace.serving_endpoints.get(endpoint)  # type: ignore[attr-defined]
    task = str(getattr(details, "task", None) or "")
    warm_endpoint_with_cold_start_patience(
        workspace,
        endpoint,
        task=task,
        prompt="Hosted agent tool warmup check.",
    )
    execution = query_serving_endpoint_with_proof(
        workspace,
        endpoint,
        task=task,
        prompt=TOOL_PROBE_PROMPT,
        client_request_id=f"mip-hosted-tool-cutover-{uuid4().hex}",
        max_tokens=128,
        return_trace=True,
    )
    if not execution.proves_agent_response:
        raise RuntimeError("green Gateway did not return a terminal Agent Responses payload")
    if not response_proves_build_cohort_tool(
        execution.response,
        expected_count=expected_count,
        expected_tool_name=f"{catalog}__gold__fn_build_cohort",
    ):
        raise RuntimeError("green Gateway did not prove exact hosted build_cohort execution")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--catalog", default="mip")
    args = parser.parse_args(argv)
    verify_hosted_tool_execution(
        _workspace_client(),
        endpoint=args.endpoint,
        expected_count=args.expected_count,
        catalog=args.catalog,
    )
    print("green Gateway exact hosted build_cohort execution: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
