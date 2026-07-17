from __future__ import annotations

import pytest

from tools.verify_app_agent_green_path import validate_green_response


def _body() -> dict[str, object]:
    return {
        "execution_mode": "agent_framework",
        "trace_kind": "agent_framework",
        "genie_trusted_assets": ["databricks.serving_endpoint.mip-growth-agent-gateway"],
        "tool_steps": [
            {
                "tool_name": "fn_build_cohort",
                "status": "completed",
                "result_hash": "sha256:abc",
            }
        ],
    }


def test_accepts_functional_app_gateway_and_tool_path() -> None:
    validate_green_response(
        _body(),
        expected_endpoint="mip-growth-agent-gateway",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("execution_mode", "deterministic", "fell back"),
        ("genie_trusted_assets", [], "expected Gateway"),
        ("tool_steps", [], "fn_build_cohort"),
    ],
)
def test_rejects_nonfunctional_or_fallback_path(
    field: str,
    value: object,
    message: str,
) -> None:
    body = _body()
    body[field] = value

    with pytest.raises(RuntimeError, match=message):
        validate_green_response(
            body,
            expected_endpoint="mip-growth-agent-gateway",
        )
