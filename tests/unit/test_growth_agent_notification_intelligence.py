from types import SimpleNamespace

from backend.config.settings import Settings
from backend.services.growth_agent_notification_intelligence import (
    recommend_notification_intelligence,
)


def test_notification_intelligence_fallback_is_explicit() -> None:
    result = recommend_notification_intelligence(
        monitor_name="Daily Refi Watch",
        workflow_id="daily_refi_brief",
        settings=Settings(mip_agent_orchestrator=False),
    )

    assert result.generation_mode == "governed_fallback"
    assert result.generator_label == "Governed notification framework"
    assert result.slack_context != result.teams_summary


class _ApiClient:
    def do(self, method: str, path: str, *, body: dict[str, object]):
        assert method == "POST"
        assert path == "/serving-endpoints/responses"
        assert body["max_output_tokens"] == 500
        return {
            "output": [
                {
                    "content": [
                        {
                            "text": """{
                              "slack_context": "A focused queue is ready for an immediate operating review",
                              "teams_summary": "The watchlist refresh highlights a focused opportunity set for coordinated team review",
                              "operator_action": "Review priority distribution and assign the next owner",
                              "strategy_summary": "Slack emphasizes attention while Teams supplies operating context and ownership"
                            }"""
                        }
                    ]
                }
            ]
        }


class _ServingEndpoints:
    def get(self, endpoint: str):
        assert endpoint == "mip-supervisor"
        return SimpleNamespace(state=SimpleNamespace(ready="READY"), task="agent/v1/responses")


def test_notification_intelligence_uses_validated_supervisor_fragments() -> None:
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=_ApiClient())
    result = recommend_notification_intelligence(
        monitor_name="Daily Refi Watch",
        workflow_id="daily_refi_brief",
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
        ),
        serving_client=client,
    )

    assert result.generation_mode == "supervisor"
    assert result.generator_label == "Supervisor-composed notification"
    assert result.slack_context != result.teams_summary


class _UnsafeApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object]):
        response = super().do(method, path, body=body)
        response["output"][0]["content"][0]["text"] = """{
          "slack_context": "Act now on 999 borrowers at /lead-queue",
          "teams_summary": "Invented guaranteed outcome for the team",
          "operator_action": "Send now",
          "strategy_summary": "Use urgency"
        }"""
        return response


def test_notification_intelligence_rejects_model_controlled_counts_routes_and_pressure() -> None:
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=_UnsafeApiClient())
    result = recommend_notification_intelligence(
        monitor_name="Daily Refi Watch",
        workflow_id="daily_refi_brief",
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
        ),
        serving_client=client,
    )

    assert result.generation_mode == "governed_fallback"
    assert "999" not in result.slack_context
    assert "/lead-queue" not in result.slack_context
