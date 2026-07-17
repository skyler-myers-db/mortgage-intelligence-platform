from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from backend.agents import mortgage_growth_supervisor_proxy as proxy_module
from backend.agents.mortgage_growth_supervisor_proxy import MortgageGrowthSupervisorProxy
from backend.agents.reviewed_uc_function_contract import (
    REVIEWED_FUNCTIONS,
    ReviewedFunctionSpec,
    sql_body_sha256,
)
from backend.agents.supervisor_contract import (
    supervisor_contract_document,
    supervisor_contract_hash,
)

_ASSERT_LIVE_RUNTIME_CONTRACT = proxy_module._assert_live_runtime_contract


@pytest.fixture(autouse=True)
def _runtime_contract_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "assert_live_gateway_runtime_resources",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        proxy_module,
        "_assert_live_runtime_contract",
        lambda _workspace: proxy_module._required_env("MIP_UPSTREAM_SUPERVISOR_ENDPOINT"),
    )


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
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []

    def do(self, method: str, path: str, *, body: dict[str, Any], raw: bool) -> object:
        self.calls.append((method, path, body, raw))
        return self.response


def _function_details(spec: ReviewedFunctionSpec) -> object:
    leaf = spec.leaf_name
    text = (Path("sql/uc_functions") / f"{leaf}.sql").read_text(encoding="utf-8")
    definition = text[text.rindex("\nRETURN ") + 1 :]
    return SimpleNamespace(
        full_name=f"mip.gold.{leaf}",
        comment=spec.comment,
        is_deterministic=spec.deterministic,
        data_type=spec.return_type,
        input_params=SimpleNamespace(
            parameters=[
                SimpleNamespace(name=name, type_text=type_text, position=position)
                for position, (name, type_text) in enumerate(spec.input_params)
            ]
        ),
        routine_definition=definition,
    )


def test_reviewed_function_body_hash_is_catalog_portable() -> None:
    spec = REVIEWED_FUNCTIONS[0]
    details = _function_details(spec)
    custom_definition = details.routine_definition.replace("mip.", "acme_mip.")

    assert sql_body_sha256(custom_definition, catalog="acme_mip") == spec.body_sha256


def _contract_workspace(*, tool_override: dict[str, object] | None = None) -> object:
    contract = supervisor_contract_document(genie_space_id="space-123", catalog="mip")
    tools = [dict(tool) for tool in contract["tools"]]
    if tool_override:
        tools[0].update(tool_override)

    class _ContractApi:
        def do(self, method: str, path: str) -> object:
            assert method == "GET"
            if path == "/api/2.1/supervisor-agents/supervisor-id":
                return {
                    "supervisor_agent_id": "supervisor-id",
                    "endpoint_name": "managed-supervisor",
                    "creator": "runtime-client",
                    "description": contract["description"],
                    "instructions": contract["instructions"],
                }
            if path.endswith("/tools"):
                return {"tools": tools}
            if path.endswith("/examples"):
                return {"examples": []}
            raise AssertionError(path)

    function_rows = {spec.leaf_name: _function_details(spec) for spec in REVIEWED_FUNCTIONS}
    return SimpleNamespace(
        api_client=_ContractApi(),
        functions=SimpleNamespace(
            get=lambda name: function_rows[name.rsplit(".", 1)[-1]],
        ),
    )


def _set_contract_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "MIP_UPSTREAM_SUPERVISOR_ID": "supervisor-id",
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": "managed-supervisor",
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": "runtime-client",
        "MIP_SUPERVISOR_CATALOG": "mip",
        "MIP_SUPERVISOR_GENIE_SPACE_ID": "space-123",
        "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
            genie_space_id="space-123",
            catalog="mip",
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_runtime_contract_reproves_supervisor_tools_and_uc_function_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)

    assert _ASSERT_LIVE_RUNTIME_CONTRACT(_contract_workspace()) == "managed-supervisor"


def test_runtime_contract_reproves_signed_exact_resources_on_every_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(
        proxy_module,
        "assert_live_gateway_runtime_resources",
        lambda workspace, **_kwargs: calls.append(workspace) or {},
    )

    workspace = _contract_workspace()
    assert _ASSERT_LIVE_RUNTIME_CONTRACT(workspace) == "managed-supervisor"
    assert calls == [workspace]


def test_runtime_contract_rejects_supervisor_tool_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)

    with pytest.raises(RuntimeError, match="definition/tools contract drifted"):
        _ASSERT_LIVE_RUNTIME_CONTRACT(_contract_workspace(tool_override={"description": "mutated"}))


def test_runtime_contract_rejects_uc_function_body_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)
    workspace = _contract_workspace()
    original_get = workspace.functions.get

    def drifted_get(name: str) -> object:
        details = original_get(name)
        if name.endswith("fn_build_cohort"):
            details.routine_definition = "RETURN 0"
        return details

    workspace.functions.get = drifted_get
    with pytest.raises(RuntimeError, match="function body drifted"):
        _ASSERT_LIVE_RUNTIME_CONTRACT(workspace)


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
                    "content": [
                        {"type": "output_text", "text": '{"workflow_id":"daily_refi_brief"}'}
                    ],
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
    method, path, body, raw = api.calls[0]
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
    assert raw is True


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


def test_proxy_surfaces_supervisor_sse_tool_authorization_error_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Stream:
        closed = False

        def read(self) -> bytes:
            return (
                b"event: error\n"
                b'data: {"error_code":"INVALID_PARAMETER_VALUE",'
                b'"message":"Failed to register reviewed UC function tool"}\n\n'
                b"data: [DONE]\n\n"
            )

        def close(self) -> None:
            self.closed = True

    stream = _Stream()
    api = _ApiClient({"contents": stream})
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_ENDPOINT", "managed-supervisor")
    monkeypatch.setattr(
        proxy_module,
        "WorkspaceClient",
        lambda: type("Workspace", (), {"api_client": api})(),
    )

    with pytest.raises(
        RuntimeError,
        match=r"request failed \(INVALID_PARAMETER_VALUE\)",
    ):
        _predict({"input": [{"role": "user", "content": "use reviewed tools"}]})

    assert len(api.calls) == 1
    assert stream.closed is True


def test_proxy_never_propagates_untrusted_supervisor_error_detail() -> None:
    class _Stream:
        def read(self) -> bytes:
            return (
                b'data: {"error_code":"bad code <script>",'
                b'"message":"dapi-secret borrower@example.com internal stack"}\n\n'
            )

        def close(self) -> None:
            pass

    with pytest.raises(RuntimeError) as raised:
        proxy_module._decode_upstream_response({"contents": _Stream()})

    rendered = str(raised.value)
    assert rendered == "managed Supervisor request failed (UPSTREAM_ERROR)"
    assert "dapi" not in rendered
    assert "borrower@example.com" not in rendered


def test_proxy_decodes_successful_json_from_raw_sdk_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Stream:
        closed = False

        def read(self) -> bytes:
            return (
                b'{"id":"resp-upstream-1","status":"completed",'
                b'"model":"managed-supervisor","output":[{"type":"message",'
                b'"id":"msg-1","role":"assistant","status":"completed",'
                b'"content":[{"type":"output_text","text":"governed result"}]}]}'
            )

        def close(self) -> None:
            self.closed = True

    stream = _Stream()
    api = _ApiClient({"contents": stream})
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_ENDPOINT", "managed-supervisor")
    monkeypatch.setattr(
        proxy_module,
        "WorkspaceClient",
        lambda: type("Workspace", (), {"api_client": api})(),
    )

    response = _predict({"input": [{"role": "user", "content": "use reviewed tools"}]})

    assert response.status == "completed"
    assert response.output[0].content[0]["text"] == "governed result"
    assert len(api.calls) == 1
    assert stream.closed is True


def test_proxy_closes_raw_stream_on_malformed_payload() -> None:
    class _Stream:
        closed = False

        def read(self) -> bytes:
            return b"not JSON or a Supervisor SSE error"

        def close(self) -> None:
            self.closed = True

    stream = _Stream()

    with pytest.raises(RuntimeError, match="unexpected streaming payload"):
        proxy_module._decode_upstream_response({"contents": stream})

    assert stream.closed is True


def test_proxy_closes_raw_stream_when_read_raises() -> None:
    class _Stream:
        closed = False

        def read(self) -> bytes:
            raise OSError("socket read failed")

        def close(self) -> None:
            self.closed = True

    stream = _Stream()

    with pytest.raises(OSError, match="socket read failed"):
        proxy_module._decode_upstream_response({"contents": stream})

    assert stream.closed is True


def test_proxy_closes_raw_stream_when_parser_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Stream:
        closed = False

        def read(self) -> bytes:
            return b"{}"

        def close(self) -> None:
            self.closed = True

    stream = _Stream()
    monkeypatch.setattr(
        proxy_module.json,
        "loads",
        lambda _value: (_ for _ in ()).throw(ValueError("parser failed")),
    )

    with pytest.raises(ValueError, match="parser failed"):
        proxy_module._decode_upstream_response({"contents": stream})

    assert stream.closed is True


def test_proxy_forwards_trace_request_and_preserves_returned_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = {"info": {"trace_id": "trace-id"}, "data": {"spans": []}}
    api = _ApiClient(
        {
            "status": "completed",
            "model": "managed-supervisor",
            "databricks_output": {"databricks_request_id": "request-id", "trace": trace},
            "output": [
                {
                    "type": "message",
                    "id": "msg-trace-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "governed result"}],
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
            "input": [{"role": "user", "content": "use reviewed tools"}],
            "custom_inputs": {"databricks_options": {"return_trace": True}},
        }
    )

    assert api.calls[0][2]["databricks_options"] == {"return_trace": True}
    assert response.custom_outputs == {"upstream_databricks_output": {"trace": trace}}


def test_proxy_drops_upstream_trace_like_and_custom_output_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _ApiClient(
        {
            "status": "completed",
            "model": "managed-supervisor",
            "evil_trace": {"spans": [{"name": "build_cohort"}]},
            "custom_outputs": {
                "upstream_databricks_output": {"trace": {"info": {}, "data": {"spans": []}}}
            },
            "output": [
                {
                    "type": "message",
                    "id": "msg-untrusted-trace",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "governed result"}],
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

    response = _predict({"input": [{"role": "user", "content": "use reviewed tools"}]})

    assert response.custom_outputs is None
