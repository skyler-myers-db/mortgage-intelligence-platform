"""ResponsesAgent boundary that delegates every product request to Supervisor.

The managed Supervisor endpoint cannot currently accept per-endpoint Unity AI
Gateway configuration. This custom Agent Model endpoint is the governed product
door: AI Gateway logs the outer Responses request, while automatic Model Serving
authentication delegates the same bounded input to the reviewed managed
Supervisor endpoint declared as an MLflow model resource.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from mlflow.models import set_model
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse


class MortgageGrowthSupervisorProxy(ResponsesAgent):
    """Return the managed Supervisor response through an Agent Model boundary."""

    # ResponsesAgent intentionally defines the one-argument Responses API shape;
    # MLflow's inherited PythonModel stub still advertises the legacy signature.
    def predict(  # type: ignore[override]
        self, request: ResponsesAgentRequest
    ) -> ResponsesAgentResponse:
        upstream = os.environ.get("MIP_UPSTREAM_SUPERVISOR_ENDPOINT", "").strip()
        if not upstream:
            raise RuntimeError("MIP_UPSTREAM_SUPERVISOR_ENDPOINT is required")
        body: dict[str, Any] = {
            "model": upstream,
            "input": [item.model_dump(mode="json", exclude_none=True) for item in request.input],
            "stream": False,
            "client_request_id": f"mip-supervisor-proxy-{uuid4().hex}",
        }
        if request.max_output_tokens is not None:
            body["max_output_tokens"] = request.max_output_tokens
        response = WorkspaceClient().api_client.do(
            "POST",
            "/serving-endpoints/responses",
            body=body,
        )
        if not isinstance(response, Mapping):
            raise RuntimeError("managed Supervisor returned an invalid Responses payload")
        output = response.get("output")
        if not isinstance(output, list) or not output:
            raise RuntimeError("managed Supervisor returned no Responses output")
        allowed = ResponsesAgentResponse.model_fields
        payload = {key: value for key, value in response.items() if key in allowed}
        payload["output"] = output
        payload["status"] = str(response.get("status") or "completed")
        payload["model"] = str(response.get("model") or upstream)
        return ResponsesAgentResponse.model_validate(payload)


set_model(MortgageGrowthSupervisorProxy())
