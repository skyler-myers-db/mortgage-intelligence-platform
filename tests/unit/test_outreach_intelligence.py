from types import SimpleNamespace

from backend.config.settings import Settings
from backend.services.outreach_intelligence import (
    GovernedCampaignVariant,
    compose_intelligent_outreach,
)
from tests.fixtures import mock_population

_DISCLOSURE = SimpleNamespace(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)


def _borrower(**overrides):
    return mock_population.BORROWERS[0].model_copy(update=overrides)


def test_governed_fallback_is_explicit_and_preserves_disclosure() -> None:
    result = compose_intelligent_outreach(
        borrower=_borrower(is_competitor_lien=True),
        channel="email",
        disclosure=_DISCLOSURE,
        settings=Settings(mip_agent_orchestrator=False, mip_lender_name="Summit Mortgage"),
    )

    assert result.generation_mode == "governed_fallback"
    assert result.generator_label == "Governed message framework"
    assert _DISCLOSURE.body in result.body
    assert "Primary offer:" in result.evidence_summary[0]


def test_campaign_variant_is_the_authoritative_fallback_copy() -> None:
    variant = GovernedCampaignVariant(
        campaign_id="11111111-1111-4111-8111-111111111111",
        variant_name="Primary",
        channel="email",
        subject="Governed campaign subject",
        body="Governed campaign body. Reply to review your options.",
        generation_mode="operator",
        generator_label="Governed campaign variant",
    )

    result = compose_intelligent_outreach(
        borrower=_borrower(),
        channel="email",
        disclosure=_DISCLOSURE,
        settings=Settings(mip_agent_orchestrator=False),
        campaign_variant=variant,
    )

    assert result.generation_mode == "governed_fallback"
    assert result.subject == variant.subject
    assert variant.body in result.body
    assert _DISCLOSURE.body in result.body


def test_sms_never_calls_supervisor_and_stays_within_channel_limit() -> None:
    result = compose_intelligent_outreach(
        borrower=_borrower(),
        channel="sms",
        disclosure=_DISCLOSURE,
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=object(),
    )

    assert result.generation_mode == "governed_fallback"
    assert result.subject is None
    assert len(result.body) <= 160


class _ApiClient:
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            assert path == "/api/2.1/supervisor-agents/supervisor-id"
            return {"supervisor_agent_id": "supervisor-id", "endpoint_name": "mip-supervisor"}
        assert method == "POST"
        assert path == "/serving-endpoints/responses"
        assert body is not None
        assert body["max_output_tokens"] == 700
        prompt = str(body["input"])
        assert "borrower_id" not in prompt
        assert "current_rate" not in prompt
        return {
            "output": [
                {
                    "content": [
                        {
                            "text": """{
                              "subject": "A clearer way to review your mortgage",
                              "body": "A mortgage review can help you understand which options fit your plans and which do not. Reply if you would like a loan officer to compare the choices with you.",
                              "strategy_summary": "Lead with clarity and borrower choice, then use one low-pressure reply invitation."
                            }"""
                        }
                    ]
                }
            ]
        }


class _ServingEndpoints:
    def get(self, endpoint: str):
        assert endpoint == "mip-supervisor"
        return SimpleNamespace(
            state=SimpleNamespace(ready="READY"),
            task="agent/v1/responses",
        )


class _CampaignApiClient(_ApiClient):
    def __init__(self) -> None:
        self.prompt = ""

    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "POST" and body is not None:
            self.prompt = str(body["input"])
        return super().do(method, path, body=body)


def test_supervisor_message_is_validated_and_server_appends_disclosure() -> None:
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=_ApiClient())
    result = compose_intelligent_outreach(
        borrower=_borrower(is_competitor_lien=True, rate_spread_bps=180),
        channel="email",
        disclosure=_DISCLOSURE,
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=client,
    )

    assert result.generation_mode == "supervisor"
    assert result.subject == "A clearer way to review your mortgage"
    assert result.body.startswith("Hello,")
    assert result.body.endswith(_DISCLOSURE.body)
    assert "180" not in result.body
    assert result.strategy_summary.startswith("Lead with clarity")


def test_supervisor_receives_governed_campaign_variant_context() -> None:
    api_client = _CampaignApiClient()
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=api_client)
    variant = GovernedCampaignVariant(
        campaign_id="11111111-1111-4111-8111-111111111111",
        variant_name="Primary",
        channel="email",
        subject="Campaign equity review",
        body="Use the campaign guidance-first positioning. Reply to review.",
        generation_mode="supervisor",
        generator_label="Supervisor campaign recommendation",
    )

    result = compose_intelligent_outreach(
        borrower=_borrower(),
        channel="email",
        disclosure=_DISCLOSURE,
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
        ),
        serving_client=client,
        campaign_variant=variant,
    )

    assert result.generation_mode == "supervisor"
    assert "governed_campaign_variant" in api_client.prompt
    assert variant.body in api_client.prompt


class _UnsafeApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        assert body is not None
        response = super().do(method, path, body=body)
        response["output"][0]["content"][0]["text"] = """{
          "subject": "Act now for the lowest rate",
          "body": "You are guaranteed to save $500. Reply now.",
          "strategy_summary": "Use urgency."
        }"""
        return response


def test_unsafe_supervisor_copy_fails_closed_to_labelled_framework() -> None:
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=_UnsafeApiClient())
    result = compose_intelligent_outreach(
        borrower=_borrower(),
        channel="email",
        disclosure=_DISCLOSURE,
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=client,
    )

    assert result.generation_mode == "governed_fallback"
    assert "guaranteed" not in result.body.lower()
    assert "$500" not in result.body
