from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.capability_serving_probes import query_serving_endpoint_with_proof


class _ApiClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def do(self, method: str, path: str, *, body: dict[str, object]) -> object:
        self.calls.append((method, path, body))
        if self.error is not None:
            raise self.error
        return {"output": [{"content": [{"text": "ready"}]}]}


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
