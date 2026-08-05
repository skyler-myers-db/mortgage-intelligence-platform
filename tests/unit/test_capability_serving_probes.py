from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.capability_serving_probes import (
    inference_log_table_names,
    query_serving_endpoint_with_proof,
)


class _ApiClient:
    def __init__(self, *, error: Exception | None = None, response: object | None = None) -> None:
        self.error = error
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def do(self, method: str, path: str, *, body: dict[str, object]) -> object:
        self.calls.append((method, path, body))
        if self.error is not None:
            raise self.error
        return self.response or {"output": [{"content": [{"text": "ready"}]}]}


def test_agent_response_failure_is_not_reissued_through_another_transport() -> None:
    api = _ApiClient(error=RuntimeError("response parse failed after submission"))
    workspace = SimpleNamespace(api_client=api)

    with pytest.raises(RuntimeError, match="parse failed"):
        query_serving_endpoint_with_proof(
            workspace,
            "agent-endpoint",
            prompt="bounded probe",
            task="agent/v1/responses",
        )

    assert [(method, path) for method, path, _body in api.calls] == [
        ("POST", "/serving-endpoints/responses")
    ]


def test_non_agent_probe_uses_one_untyped_invocation() -> None:
    api = _ApiClient()
    execution = query_serving_endpoint_with_proof(
        SimpleNamespace(api_client=api),
        "foundation-endpoint",
        prompt="bounded probe",
        task="llm/v1/chat",
        client_request_id="req-1",
    )

    assert execution.transport == "endpoint_invocation"
    assert len(api.calls) == 1
    assert api.calls[0][1] == "/serving-endpoints/foundation-endpoint/invocations"
    assert api.calls[0][2]["client_request_id"] == "req-1"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", True),
        ("queued", False),
        ("in_progress", False),
        ("incomplete", False),
        ("failed", False),
        ("error", False),
        ("cancelled", False),
        ("canceled", False),
    ],
)
def test_agent_responses_proof_requires_terminal_completed_status(
    status: str,
    expected: bool,
) -> None:
    api = _ApiClient(
        response={
            "id": "resp-1",
            "status": status,
            "output": [{"content": [{"type": "output_text", "text": "ready"}]}],
        }
    )

    execution = query_serving_endpoint_with_proof(
        SimpleNamespace(api_client=api),
        "agent-endpoint",
        prompt="bounded probe",
        task="agent/v1/responses",
    )

    assert execution.proves_agent_response is expected
    assert len(api.calls) == 1


def test_agent_responses_completed_without_output_is_not_proof() -> None:
    api = _ApiClient(response={"id": "resp-1", "status": "completed", "output": []})

    execution = query_serving_endpoint_with_proof(
        SimpleNamespace(api_client=api),
        "agent-endpoint",
        prompt="bounded probe",
        task="agent/v1/responses",
    )

    assert execution.proves_agent_response is False


def test_inference_table_discovery_escapes_literal_prefix_and_filters_decoys() -> None:
    class _SqlClient:
        def __init__(self) -> None:
            self.statement = ""
            self.parameters: dict[str, str] = {}

        def execute(self, statement: str, parameters: dict[str, str]) -> list[dict[str, str]]:
            self.statement = statement
            self.parameters = parameters
            return [
                {"table_name": "mipXagentXgatewayXllama_payload"},
                {"table_name": "mip_agent_gateway_llama_payload"},
            ]

    sql = _SqlClient()
    names = inference_log_table_names(sql, "mip.audit.mip_agent_gateway_llama")

    assert names == ["mip_agent_gateway_llama_payload"]
    assert "LIKE :prefix_like ESCAPE '\\\\'" in sql.statement
    assert sql.parameters["prefix_like"] == r"mip\_agent\_gateway\_llama%"
