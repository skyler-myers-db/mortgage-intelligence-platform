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
from backend.agents.supervisor_contract import supervisor_contract_hash

_ASSERT_LIVE_RUNTIME_CONTRACT = proxy_module._assert_live_runtime_contract
_SUPERVISOR_WORKSPACE = proxy_module._supervisor_workspace


@pytest.fixture(autouse=True)
def _runtime_contract_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proxy_module,
        "verified_gateway_runtime_resource_environment",
        lambda _environment: {
            "supervisor_id": "supervisor-id",
            "supervisor_endpoint": "managed-supervisor",
            "runtime_application_id": "runtime-client",
            "proxy_caller_application_id": "proxy-client",
            "proxy_caller_credential_id": "proxy-credential",
            "catalog": "mip",
            "genie_space_id": "space-123",
            "supervisor_contract_sha256": supervisor_contract_hash(
                genie_space_id="space-123",
                catalog="mip",
            ),
        },
    )
    monkeypatch.setattr(
        proxy_module,
        "_assert_live_runtime_contract",
        lambda: proxy_module._required_env(
            "MIP_UPSTREAM_SUPERVISOR_ENDPOINT"
        ),
    )
    monkeypatch.setattr(
        proxy_module,
        "_supervisor_workspace",
        lambda: proxy_module.WorkspaceClient(),
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


def _set_contract_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "MIP_UPSTREAM_SUPERVISOR_ID": "supervisor-id",
        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": "managed-supervisor",
        "MIP_UPSTREAM_SUPERVISOR_CREATOR": "runtime-client",
        "MIP_UPSTREAM_PROXY_CLIENT_ID": "proxy-client",
        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": "proxy-credential",
        "MIP_SUPERVISOR_CATALOG": "mip",
        "MIP_SUPERVISOR_GENIE_SPACE_ID": "space-123",
        "MIP_SUPERVISOR_CONTRACT_SHA256": supervisor_contract_hash(
            genie_space_id="space-123",
            catalog="mip",
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_runtime_contract_authenticates_deployment_signed_exact_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)

    assert _ASSERT_LIVE_RUNTIME_CONTRACT() == "managed-supervisor"


def test_runtime_contract_authenticates_signed_exact_binding_on_every_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(
        proxy_module,
        "verified_gateway_runtime_resource_environment",
        lambda environment: calls.append(environment)
        or {
            "supervisor_id": "supervisor-id",
            "supervisor_endpoint": "managed-supervisor",
            "runtime_application_id": "runtime-client",
            "proxy_caller_application_id": "proxy-client",
            "proxy_caller_credential_id": "proxy-credential",
            "catalog": "mip",
            "genie_space_id": "space-123",
            "supervisor_contract_sha256": supervisor_contract_hash(
                genie_space_id="space-123",
                catalog="mip",
            ),
        },
    )

    assert _ASSERT_LIVE_RUNTIME_CONTRACT() == "managed-supervisor"
    assert calls == [proxy_module.os.environ]


def test_runtime_contract_rejects_signed_proxy_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)
    monkeypatch.setattr(
        proxy_module,
        "verified_gateway_runtime_resource_environment",
        lambda _environment: {
            "supervisor_id": "supervisor-id",
            "supervisor_endpoint": "managed-supervisor",
            "runtime_application_id": "runtime-client",
            "proxy_caller_application_id": "different-proxy",
            "proxy_caller_credential_id": "proxy-credential",
            "catalog": "mip",
            "genie_space_id": "space-123",
            "supervisor_contract_sha256": supervisor_contract_hash(
                genie_space_id="space-123",
                catalog="mip",
            ),
        },
    )

    with pytest.raises(RuntimeError, match="signed Gateway-to-Supervisor binding drifted"):
        _ASSERT_LIVE_RUNTIME_CONTRACT()


def test_runtime_contract_rejects_source_contract_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_contract_env(monkeypatch)
    monkeypatch.setenv("MIP_SUPERVISOR_CONTRACT_SHA256", "0" * 64)

    with pytest.raises(RuntimeError, match="configured contract digest is invalid"):
        _ASSERT_LIVE_RUNTIME_CONTRACT()


def test_supervisor_workspace_uses_only_the_dedicated_proxy_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str]] = []
    expected = SimpleNamespace(api_client=object())

    def workspace_client(**kwargs: str) -> object:
        calls.append(kwargs)
        return expected

    monkeypatch.setenv("MIP_UPSTREAM_PROXY_CLIENT_ID", "proxy-client")
    monkeypatch.setenv("MIP_UPSTREAM_PROXY_CREDENTIAL_ID", "proxy-credential")
    monkeypatch.setenv("MIP_UPSTREAM_PROXY_CLIENT_SECRET", "proxy-secret-value")
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_CREATOR", "runtime-client")
    monkeypatch.setattr(proxy_module, "WorkspaceClient", workspace_client)

    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    assert _SUPERVISOR_WORKSPACE() is expected
    assert calls == [
        {
            "host": "https://workspace.example",
            "client_id": "proxy-client",
            "client_secret": "proxy-secret-value",
            "auth_type": "oauth-m2m",
        }
    ]


@pytest.mark.parametrize("proxy_client_id", ("runtime-client", "RUNTIME-CLIENT"))
def test_supervisor_workspace_rejects_runtime_owner_reuse(
    monkeypatch: pytest.MonkeyPatch,
    proxy_client_id: str,
) -> None:
    monkeypatch.setenv("MIP_UPSTREAM_PROXY_CLIENT_ID", proxy_client_id)
    monkeypatch.setenv("MIP_UPSTREAM_PROXY_CREDENTIAL_ID", "proxy-credential")
    monkeypatch.setenv("MIP_UPSTREAM_PROXY_CLIENT_SECRET", "proxy-secret-value")
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_CREATOR", "runtime-client")

    with pytest.raises(RuntimeError, match="must not be the runtime owner"):
        _SUPERVISOR_WORKSPACE()


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


@pytest.mark.parametrize("status", (None, "failed", "in_progress", "incomplete"))
def test_proxy_rejects_nonterminal_or_missing_upstream_status(
    monkeypatch: pytest.MonkeyPatch,
    status: str | None,
) -> None:
    response: dict[str, object] = {
        "output": [
            {
                "type": "message",
                "id": "msg-1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "ready"}],
            }
        ]
    }
    if status is not None:
        response["status"] = status
    monkeypatch.setenv("MIP_UPSTREAM_SUPERVISOR_ENDPOINT", "managed-supervisor")
    monkeypatch.setattr(
        proxy_module,
        "WorkspaceClient",
        lambda: type("Workspace", (), {"api_client": _ApiClient(response)})(),
    )

    with pytest.raises(RuntimeError, match="non-terminal response"):
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
