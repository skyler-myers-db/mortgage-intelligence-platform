from __future__ import annotations

import mlflow.pyfunc
import pytest
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from tools.databricks.mlflow_responses_packaging import (
    responses_agent_packaging_validation,
)


class _LiveOnlyAgent(ResponsesAgent):
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:  # type: ignore[override]
        del request
        raise RuntimeError("live Gateway resources do not exist during packaging")


def _input_example() -> dict[str, object]:
    return {
        "input": [{"role": "user", "content": "validate the Responses schema"}],
        "max_output_tokens": 32,
    }


def test_real_mlflow_helper_packages_without_invoking_live_predict(tmp_path) -> None:
    original = mlflow.pyfunc._save_model_responses_agent_helper

    with responses_agent_packaging_validation():
        mlflow.pyfunc.save_model(
            path=tmp_path / "model",
            python_model=_LiveOnlyAgent(),
            input_example=_input_example(),
            pip_requirements=[],
        )

    assert mlflow.pyfunc._save_model_responses_agent_helper is original
    loaded = mlflow.pyfunc.load_model(tmp_path / "model")
    with pytest.raises(RuntimeError, match="live Gateway resources"):
        loaded.predict(_input_example())


def test_packaging_context_restores_helper_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    original = mlflow.pyfunc._save_model_responses_agent_helper

    def changed_helper(*_args: object) -> None:
        return None

    monkeypatch.setattr(mlflow.pyfunc, "_save_model_responses_agent_helper", changed_helper)
    with (
        pytest.raises(RuntimeError, match="packaging contract changed"),
        responses_agent_packaging_validation(),
    ):
        pass

    assert mlflow.pyfunc._save_model_responses_agent_helper is changed_helper
    monkeypatch.setattr(mlflow.pyfunc, "_save_model_responses_agent_helper", original)
