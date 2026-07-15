import json
from types import SimpleNamespace

import pytest

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


def test_notification_intelligence_fallback_is_workflow_specific() -> None:
    refi = recommend_notification_intelligence(
        monitor_name="Daily Refi Watch",
        workflow_id="daily_refi_brief",
        settings=Settings(mip_agent_orchestrator=False),
    )
    listing = recommend_notification_intelligence(
        monitor_name="Listing Watch",
        workflow_id="listing_watch",
        settings=Settings(mip_agent_orchestrator=False),
    )

    assert refi.slack_context != listing.slack_context
    assert refi.teams_summary != listing.teams_summary
    assert refi.operator_action != listing.operator_action
    assert "refinance" in refi.teams_summary.lower()
    assert "purchase" in listing.teams_summary.lower()


class _ApiClient:
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            assert path == "/api/2.1/supervisor-agents/supervisor-id"
            return {"supervisor_agent_id": "supervisor-id", "endpoint_name": "mip-supervisor"}
        assert method == "POST"
        assert path == "/serving-endpoints/responses"
        assert body is not None
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


def test_notification_intelligence_uses_validated_agent_response_fragments() -> None:
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
    assert result.generator_label == "Databricks Agent Responses"
    assert result.slack_context != result.teams_summary


class _DuplicateChannelApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        response = super().do(method, path, body=body)
        response["output"][0]["content"][0]["text"] = """{
          "slack_context": "The focused queue is ready for team review.",
          "teams_summary": "  THE focused queue is ready for team review!  ",
          "operator_action": "Review priority distribution and assign the next owner",
          "strategy_summary": "Use channel specific framing for the operating review"
        }"""
        return response


def test_notification_intelligence_rejects_normalized_duplicate_channel_content() -> None:
    client = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_DuplicateChannelApiClient(),
    )
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
    assert result.generator_label == "Governed notification framework"
    assert result.slack_context != result.teams_summary
    assert "focused queue is ready for team review" not in result.slack_context.lower()


class _NearDuplicateChannelApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        response = super().do(method, path, body=body)
        response["output"][0]["content"][0]["text"] = """{
          "slack_context": "The focused queue is ready for team review",
          "teams_summary": "The focused queue is ready for team review today",
          "operator_action": "Review priority distribution and assign the next owner",
          "strategy_summary": "Use channel specific framing for the operating review"
        }"""
        return response


def test_notification_intelligence_rejects_materially_similar_channel_content() -> None:
    client = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_NearDuplicateChannelApiClient(),
    )
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
    assert result.slack_context != result.teams_summary


class _UnsafeApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        assert body is not None
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


class _SingleUnsafeFragmentApiClient(_ApiClient):
    def __init__(self, field: str, unsafe_value: str) -> None:
        self.field = field
        self.unsafe_value = unsafe_value

    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        response = super().do(method, path, body=body)
        fragments = {
            "slack_context": "A focused queue is ready for an operating review",
            "teams_summary": "The watchlist is ready for a coordinated ownership review",
            "operator_action": "Review priority distribution and assign the next owner",
            "strategy_summary": "Slack provides attention while Teams provides operating context",
        }
        fragments[self.field] = self.unsafe_value
        response["output"][0]["content"][0]["text"] = json.dumps(fragments)
        return response


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("slack_context", "A guaranteed lower rate is ready for queue review"),
        ("teams_summary", "Review this queue with urgency before team handoff"),
        ("operator_action", "Review the queue urgently and assign the next owner"),
        ("strategy_summary", "Use guaranteed outcomes in the operating summary"),
    ],
)
def test_notification_intelligence_rejects_each_pressure_phrase_independently(
    field: str,
    unsafe_value: str,
) -> None:
    client = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_SingleUnsafeFragmentApiClient(field, unsafe_value),
    )

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
    assert (
        unsafe_value.lower()
        not in " ".join(
            (
                result.slack_context,
                result.teams_summary,
                result.operator_action,
                result.strategy_summary,
            )
        ).lower()
    )


class _UnsafeIdentityApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        assert body is not None
        response = super().do(method, path, body=body)
        response["output"][0]["content"][0]["text"] = """{
          "slack_context": "Contact john smith for the operating review",
          "teams_summary": "Senior citizens are the priority audience for this review",
          "operator_action": "Review the queue and assign the next owner",
          "strategy_summary": "Prioritize source of income for the operating review"
        }"""
        return response


def test_notification_intelligence_fails_closed_on_names_and_protected_targeting() -> None:
    client = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_UnsafeIdentityApiClient(),
    )
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
    rendered = " ".join(
        (result.slack_context, result.teams_summary, result.strategy_summary)
    ).lower()
    assert "john smith" not in rendered
    assert "senior citizen" not in rendered
    assert "source of income" not in rendered
