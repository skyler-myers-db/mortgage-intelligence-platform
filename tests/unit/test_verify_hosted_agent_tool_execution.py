from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.services.capability_serving_probes import ServingEndpointExecution
from tools.databricks import verify_hosted_agent_tool_execution as verifier


def _trace_response(
    *,
    count: int = 42,
    tool_name: str = "mip__gold__fn_build_cohort",
) -> dict[str, object]:
    return {
        "status": "completed",
        "custom_outputs": {
            "upstream_databricks_output": {
                "trace": {
                    "info": {"trace_id": "trace-id"},
                    "data": {
                        "spans": [
                            {
                                "trace_id": "AAAAAAAAAAAAAAAAAAAAAQ==",
                                "span_id": "AAAAAAAAAAE=",
                                "parent_span_id": None,
                                "name": tool_name,
                                "start_time_unix_nano": 1,
                                "end_time_unix_nano": 2,
                                "events": [],
                                "status": {"code": "STATUS_CODE_OK", "message": ""},
                                "attributes": {
                                    "mlflow.traceRequestId": "tr-hosted-tool-test",
                                    "mlflow.spanType": json.dumps("TOOL"),
                                    "mlflow.spanInputs": json.dumps(
                                        {
                                            "segment_codes": ["itm"],
                                            "segment_mode": "any",
                                            "states": ["CA"],
                                        }
                                    ),
                                    "mlflow.spanOutputs": json.dumps(
                                        {
                                            "result": json.dumps(
                                                {
                                                    "is_truncated": False,
                                                    "columns": ["output"],
                                                    "rows": [[count]],
                                                }
                                            )
                                        }
                                    ),
                                },
                                "links": [],
                            }
                        ]
                    },
                }
            }
        },
        "output": [{"content": [{"text": "governed result"}]}],
    }


def test_hosted_tool_cutover_probe_requires_exact_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _endpoint: SimpleNamespace(task="agent/v1/responses")
        )
    )
    monkeypatch.setattr(
        verifier,
        "warm_endpoint_with_cold_start_patience",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        verifier,
        "query_serving_endpoint_with_proof",
        lambda *_a, **_kw: ServingEndpointExecution(
            endpoint="gateway",
            task="agent/v1/responses",
            transport="responses_api",
            response=_trace_response(),
            client_request_id="request-id",
        ),
    )

    verifier.verify_hosted_tool_execution(
        workspace,
        endpoint="gateway",
        expected_count=42,
        catalog="mip",
    )


def test_hosted_tool_cutover_probe_rejects_wrong_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _endpoint: SimpleNamespace(task="agent/v1/responses")
        )
    )
    monkeypatch.setattr(
        verifier,
        "warm_endpoint_with_cold_start_patience",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        verifier,
        "query_serving_endpoint_with_proof",
        lambda *_a, **_kw: ServingEndpointExecution(
            endpoint="gateway",
            task="agent/v1/responses",
            transport="responses_api",
            response=_trace_response(count=41),
            client_request_id="request-id",
        ),
    )

    with pytest.raises(RuntimeError, match="exact hosted"):
        verifier.verify_hosted_tool_execution(
            workspace,
            endpoint="gateway",
            expected_count=42,
            catalog="mip",
        )


def test_hosted_tool_cutover_probe_binds_configured_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            get=lambda _endpoint: SimpleNamespace(task="agent/v1/responses")
        )
    )
    monkeypatch.setattr(
        verifier,
        "warm_endpoint_with_cold_start_patience",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        verifier,
        "query_serving_endpoint_with_proof",
        lambda *_a, **_kw: ServingEndpointExecution(
            endpoint="gateway",
            task="agent/v1/responses",
            transport="responses_api",
            response=_trace_response(tool_name="customer__gold__fn_build_cohort"),
            client_request_id="request-id",
        ),
    )

    verifier.verify_hosted_tool_execution(
        workspace,
        endpoint="gateway",
        expected_count=42,
        catalog="customer",
    )
