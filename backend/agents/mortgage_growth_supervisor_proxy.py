"""ResponsesAgent boundary that delegates every product request to Supervisor.

The managed Supervisor endpoint cannot currently accept per-endpoint Unity AI
Gateway configuration. This custom Agent Model endpoint is the governed product
door: AI Gateway logs the outer Responses request, while a dedicated OAuth
identity delegates the same bounded input to the reviewed managed Supervisor.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from mlflow.models import set_model
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

try:
    from backend.agents.gateway_contract import (
        verified_gateway_runtime_resource_environment,
    )
    from backend.agents.reviewed_uc_function_contract import assert_reviewed_function_set
    from backend.agents.supervisor_contract import (
        supervisor_contract_document,
        supervisor_contract_hash,
    )
except ModuleNotFoundError:  # MLflow code_path places backend/ directly on sys.path.
    from agents.gateway_contract import (  # type: ignore[no-redef]
        verified_gateway_runtime_resource_environment,
    )
    from agents.reviewed_uc_function_contract import (  # type: ignore[no-redef]
        assert_reviewed_function_set,
    )
    from agents.supervisor_contract import (  # type: ignore[no-redef]
        supervisor_contract_document,
        supervisor_contract_hash,
    )

_UPSTREAM_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _payload_rows(payload: object, key: str) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        if payload.get("next_page_token"):
            raise RuntimeError(f"managed Supervisor {key} contract is unexpectedly paginated")
        rows = payload.get(key, [])
    else:
        raise RuntimeError(f"managed Supervisor {key} contract is invalid")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise RuntimeError(f"managed Supervisor {key} contract is invalid")
    return list(rows)


def _supervisor_workspace() -> WorkspaceClient:
    """Build the least-privilege caller used only for the private Supervisor."""

    proxy_client_id = _required_env("MIP_UPSTREAM_PROXY_CLIENT_ID")
    _required_env("MIP_UPSTREAM_PROXY_CREDENTIAL_ID")
    proxy_client_secret = _required_env("MIP_UPSTREAM_PROXY_CLIENT_SECRET")
    runtime_id = _required_env("MIP_UPSTREAM_SUPERVISOR_CREATOR")
    if proxy_client_id.casefold() == runtime_id.casefold():
        raise RuntimeError("Supervisor proxy caller must not be the runtime owner")
    host = _required_env("DATABRICKS_HOST")
    return WorkspaceClient(
        host=host,
        client_id=proxy_client_id,
        client_secret=proxy_client_secret,
        auth_type="oauth-m2m",
    )


def _assert_live_runtime_contract(
    supervisor_workspace: WorkspaceClient,
) -> str:
    """Re-prove the Supervisor and UC tool definitions before every inference."""

    supervisor_id = _required_env("MIP_UPSTREAM_SUPERVISOR_ID")
    upstream = _required_env("MIP_UPSTREAM_SUPERVISOR_ENDPOINT")
    runtime_id = _required_env("MIP_UPSTREAM_SUPERVISOR_CREATOR")
    catalog = _required_env("MIP_SUPERVISOR_CATALOG")
    genie_space_id = _required_env("MIP_SUPERVISOR_GENIE_SPACE_ID")
    expected_hash = _required_env("MIP_SUPERVISOR_CONTRACT_SHA256")
    if expected_hash != supervisor_contract_hash(
        genie_space_id=genie_space_id,
        catalog=catalog,
    ):
        raise RuntimeError("managed Supervisor configured contract digest is invalid")
    signed = verified_gateway_runtime_resource_environment(os.environ)
    expected_binding = {
        "supervisor_id": supervisor_id,
        "supervisor_endpoint": upstream,
        "runtime_application_id": runtime_id,
        "proxy_caller_application_id": _required_env("MIP_UPSTREAM_PROXY_CLIENT_ID"),
        "proxy_caller_credential_id": _required_env("MIP_UPSTREAM_PROXY_CREDENTIAL_ID"),
        "catalog": catalog,
        "genie_space_id": genie_space_id,
        "supervisor_contract_sha256": expected_hash,
    }
    if any(signed.get(name) != value for name, value in expected_binding.items()):
        raise RuntimeError("signed Gateway-to-Supervisor binding drifted")

    encoded_id = quote(supervisor_id, safe="")
    api = supervisor_workspace.api_client
    details = api.do("GET", f"/api/2.1/supervisor-agents/{encoded_id}")
    if not isinstance(details, Mapping):
        raise RuntimeError("managed Supervisor definition contract is invalid")
    if str(details.get("supervisor_agent_id") or "").strip() != supervisor_id:
        raise RuntimeError("managed Supervisor immutable identity drifted")
    if str(details.get("endpoint_name") or "").strip() != upstream:
        raise RuntimeError("managed Supervisor endpoint binding drifted")
    if str(details.get("creator") or "").strip() != runtime_id:
        raise RuntimeError("managed Supervisor creator drifted")

    parent = f"supervisor-agents/{encoded_id}"
    tools = _payload_rows(api.do("GET", f"/api/2.1/{parent}/tools"), "tools")
    examples = _payload_rows(api.do("GET", f"/api/2.1/{parent}/examples"), "examples")
    expected = supervisor_contract_document(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    actual_tools: list[dict[str, Any]] = []
    for tool in tools:
        tool_type = str(tool.get("tool_type") or "")
        resource = tool.get(tool_type)
        actual_tools.append(
            {
                "tool_id": str(tool.get("tool_id") or ""),
                "tool_type": tool_type,
                "description": str(tool.get("description") or ""),
                tool_type: resource,
            }
        )
    actual = {
        "description": details.get("description"),
        "instructions": details.get("instructions"),
        "tools": sorted(actual_tools, key=lambda row: row["tool_id"]),
        "examples": [dict(row) for row in examples],
    }
    expected["tools"] = sorted(expected["tools"], key=lambda row: row["tool_id"])
    if actual != expected:
        raise RuntimeError("managed Supervisor definition/tools contract drifted")
    assert_reviewed_function_set(supervisor_workspace, catalog=catalog)
    return upstream


def _decode_upstream_response(raw: object) -> Mapping[str, Any]:
    """Decode JSON or surface a bounded managed-Supervisor SSE error once."""

    if isinstance(raw, Mapping) and "contents" in raw:
        stream = raw.get("contents")
        read = getattr(stream, "read", None)
        try:
            if not callable(read):
                raise RuntimeError("managed Supervisor returned an invalid streaming payload")
            body = read()
            text = bytes(body).decode("utf-8", errors="replace")
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, Mapping):
                return decoded
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, Mapping):
                    message = str(event.get("message") or event.get("error") or "").strip()
                    code = str(event.get("error_code") or "UPSTREAM_ERROR").strip()
                    if message:
                        safe_code = (
                            code if _UPSTREAM_ERROR_CODE.fullmatch(code) else "UPSTREAM_ERROR"
                        )
                        raise RuntimeError(f"managed Supervisor request failed ({safe_code})")
            raise RuntimeError("managed Supervisor returned an unexpected streaming payload")
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    if not isinstance(raw, Mapping):
        raise RuntimeError("managed Supervisor returned an invalid Responses payload")
    return raw


class MortgageGrowthSupervisorProxy(ResponsesAgent):
    """Return the managed Supervisor response through an Agent Model boundary."""

    # ResponsesAgent intentionally defines the one-argument Responses API shape;
    # MLflow's inherited PythonModel stub still advertises the legacy signature.
    def predict(  # type: ignore[override]
        self, request: ResponsesAgentRequest
    ) -> ResponsesAgentResponse:
        supervisor_workspace = _supervisor_workspace()
        upstream = _assert_live_runtime_contract(supervisor_workspace)
        body: dict[str, Any] = {
            "model": upstream,
            "input": [item.model_dump(mode="json", exclude_none=True) for item in request.input],
            "stream": False,
            "client_request_id": f"mip-supervisor-proxy-{uuid4().hex}",
        }
        if request.max_output_tokens is not None:
            body["max_output_tokens"] = request.max_output_tokens
        custom_inputs = request.custom_inputs or {}
        databricks_options = custom_inputs.get("databricks_options")
        if (
            isinstance(databricks_options, Mapping)
            and databricks_options.get("return_trace") is True
        ):
            body["databricks_options"] = {"return_trace": True}
        raw_response = supervisor_workspace.api_client.do(
            "POST",
            "/serving-endpoints/responses",
            body=body,
            raw=True,
        )
        response = _decode_upstream_response(raw_response)
        output = response.get("output")
        if not isinstance(output, list) or not output:
            raise RuntimeError("managed Supervisor returned no Responses output")
        allowed = ResponsesAgentResponse.model_fields
        payload = {
            key: value
            for key, value in response.items()
            if key in allowed and key != "custom_outputs"
        }
        payload["output"] = output
        payload["status"] = str(response.get("status") or "completed")
        payload["model"] = str(response.get("model") or upstream)
        databricks_output = response.get("databricks_output")
        platform_trace = (
            databricks_output.get("trace") if isinstance(databricks_output, Mapping) else None
        )
        if isinstance(platform_trace, Mapping):
            payload["custom_outputs"] = {
                "upstream_databricks_output": {"trace": dict(platform_trace)}
            }
        return ResponsesAgentResponse.model_validate(payload)


set_model(MortgageGrowthSupervisorProxy())
