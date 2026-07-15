from __future__ import annotations

from typing import Any

import pytest
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from backend.agents import mortgage_growth_supervisor_proxy as proxy_module
from backend.agents.mortgage_growth_supervisor_proxy import MortgageGrowthSupervisorProxy


def _predict(payload: dict[str, Any]) -> ResponsesAgentResponse:
    """Exercise agent logic without MLflow's trace-export decorator."""

    traced = MortgageGrowthSupervisorProxy.predict
    pyfunc_wrapper = traced.__wrapped__
    raw_predict = pyfunc_wrapper.__wrapped__
    request = ResponsesAgentRequest.model_validate(payload)
    return raw_predict(MortgageGrowthSupervisorProxy(), request)


class _ApiClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def do(self, method: str, path: str, *, body: dict[str, Any]) -> object:
        self.calls.append((method, path, body))
        return self.response


def test_proxy_delegates_the_same_responses_input_to_managed_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _ApiClient(
        {
            "id": "resp-upstream-1",
            "status": "completed",
            "model": "managed-supervisor",
            "output": [
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": '{"workflow_id":"daily_refi_brief"}'}],
                }
            ],
        }
    )
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_ENDPOINT", "managed-supervisor")
    monkeypatch.setattr(
        proxy_module,
        "WorkspaceClient",
        lambda: type("Workspace", (), {"api_client": api})(),
    )

    response = _predict(
        {
            "input": [{"role": "user", "content": "Choose one reviewed workflow."}],
            "max_output_tokens": 64,
        }
    )

    assert response.status == "completed"
    assert response.model == "managed-supervisor"
    method, path, body = api.calls[0]
    assert (method, path) == ("POST", "/serving-endpoints/responses")
    assert body["model"] == "managed-supervisor"
    assert body["input"] == [
        {
            "role": "user",
            "content": "Choose one reviewed workflow.",
            "type": "message",
        }
    ]
    assert body["max_output_tokens"] == 64
    assert str(body["client_request_id"]).startswith("mip-supervisor-proxy-")


@pytest.mark.parametrize("response", [None, {}, {"output": []}])
def test_proxy_fails_closed_on_invalid_upstream_response(
    monkeypatch: pytest.MonkeyPatch,
    response: object,
) -> None:
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_ENDPOINT", "managed-supervisor")
    monkeypatch.setattr(
        proxy_module,
        "WorkspaceClient",
        lambda: type("Workspace", (), {"api_client": _ApiClient(response)})(),
    )

    with pytest.raises(RuntimeError, match="managed Supervisor returned"):
        _predict({"input": [{"role": "user", "content": "x"}]})
