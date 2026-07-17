"""Package a ResponsesAgent without calling not-yet-created live resources.

MLflow validates every ResponsesAgent artifact by invoking ``predict`` while
the artifact is being saved.  A Gateway proxy cannot perform its ordinary
runtime proof at that point: its registered model, serving endpoint, and
signed endpoint binding do not exist yet.  This deployment-only context keeps
MLflow's request, response, signature, metadata, and load-context validation,
but substitutes a fixed response for that single packaging-time call.

The logged proxy source is not modified and contains no runtime bypass.  The
context patches MLflow only in the provisioning process and fails closed if
the pinned helper contract changes.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import mlflow.pyfunc
from mlflow.types.responses import ResponsesAgentResponse
from mlflow.types.responses_helpers import OutputItem

_HELPER_PARAMETERS = (
    "python_model",
    "mlflow_model",
    "signature",
    "input_example",
    "artifacts",
    "model_config",
)
_PATCH_LOCK = threading.RLock()


def _packaging_response() -> ResponsesAgentResponse:
    return ResponsesAgentResponse(
        id="mip-gateway-packaging-validation",
        model="mip-gateway-packaging-validation",
        status="completed",
        output=[
            OutputItem.model_validate(
                {
                    "type": "message",
                    "id": "mip-gateway-packaging-validation-message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "packaging schema validation only",
                            "annotations": [],
                        }
                    ],
                }
            )
        ],
    )


@contextmanager
def responses_agent_packaging_validation() -> Iterator[None]:
    """Keep MLflow schema validation while suppressing its cyclic live call."""

    with _PATCH_LOCK:
        pyfunc_module: Any = mlflow.pyfunc
        original = getattr(pyfunc_module, "_save_model_responses_agent_helper", None)
        if not callable(original) or tuple(inspect.signature(original).parameters) != (
            _HELPER_PARAMETERS
        ):
            raise RuntimeError("pinned MLflow ResponsesAgent packaging contract changed")

        def packaging_helper(
            python_model: Any,
            mlflow_model: Any,
            signature: Any,
            input_example: Any,
            artifacts: Any,
            model_config: Any,
        ) -> Any:
            predict = python_model.predict
            python_model.predict = lambda _request: _packaging_response()
            try:
                return original(
                    python_model,
                    mlflow_model,
                    signature,
                    input_example,
                    artifacts,
                    model_config,
                )
            finally:
                python_model.predict = predict

        pyfunc_module._save_model_responses_agent_helper = packaging_helper
        try:
            yield
        finally:
            pyfunc_module._save_model_responses_agent_helper = original
