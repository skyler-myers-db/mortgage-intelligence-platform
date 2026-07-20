from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import Settings
from backend.main import app
from backend.schemas.portfolio import PortfolioOfferMixRow, PortfolioPreview
from backend.services.campaign_intelligence import (
    CampaignPerformanceContext,
    campaign_criteria_fingerprint,
    inspect_campaign_variant_provenance,
    recommend_campaign,
)
from tests.fixtures.supervisor_runtime import (
    GATEWAY_ENDPOINT,
    gateway_endpoint_details,
    runtime_settings,
    supervisor_endpoint_details,
    supervisor_metadata,
)

client = TestClient(app)


def _preview() -> PortfolioPreview:
    return PortfolioPreview(
        marketable_population=2_119,
        campaign_build_contact_count=2_119,
        campaign_build_eligible=True,
        high_intent_leads=1_200,
        avg_score=73,
        avg_current_lien_balance_usd=412_500,
        total_current_lien_balance_usd=874_087_500,
        avg_equity_pct=48.6,
        avg_rate_spread_bps=126.4,
        offer_mix=[
            PortfolioOfferMixRow(offer_code="refi", borrower_count=1_550),
            PortfolioOfferMixRow(offer_code="heloc", borrower_count=569),
        ],
    )


def _qualified_performance() -> CampaignPerformanceContext:
    return CampaignPerformanceContext(
        unique_leads_attempted=100,
        unique_contacts_reached=80,
        unique_application_starts=20,
        unique_applications_submitted=12,
        unique_closed_funded=3,
        observed_from=date(2026, 4, 15),
        observed_to=date(2026, 7, 13),
        snapshot_at=datetime(2026, 7, 13, 16, 0, tzinfo=UTC),
        interval_days=90,
        observation_fingerprint="a" * 64,
    )


def test_reviewed_fallback_is_labelled_and_uses_governed_cohort_metrics() -> None:
    result = recommend_campaign(
        _preview(),
        settings=Settings(mip_agent_orchestrator=False, mip_lender_name="Summit Mortgage"),
    )

    assert result.generation_mode == "reviewed_fallback"
    assert result.performance_status == "unavailable"
    assert result.evidence[0].value == "2,119 borrowers"
    assert result.holdout_pct == 10
    assert {row.source_asset for row in result.evidence} == {
        "mip.semantics.portfolio_headline_metric_view",
        "mip.gold.borrower_360",
    }
    assert result.variants[0].body != result.variants[1].body
    assert all("guarantee" not in variant.body.lower() for variant in result.variants)


def test_disabled_supervisor_reason_is_mapped_to_reviewed_public_copy() -> None:
    result = recommend_campaign(
        _preview(),
        settings=Settings(mip_agent_orchestrator=False),
        serving_client=object(),
    )

    assert result.warnings == ["Supervisor is not enabled for this deployment"]
    assert "orchestrator_disabled" not in str(result.model_dump())


@pytest.mark.parametrize("app_env", ["sandbox", "staging", "production", "customer"])
def test_non_dev_campaign_provenance_refuses_process_local_key(app_env: str) -> None:
    with pytest.raises(RuntimeError, match="requires a configured HMAC secret"):
        recommend_campaign(
            _preview(),
            settings=Settings(
                app_env=app_env,
                mip_agent_orchestrator=False,
                mip_genie_action_secret=None,
                mip_genie_action_secret_current=None,
                mip_genie_action_secret_previous=None,
            ),
        )


def test_non_dev_campaign_provenance_uses_configured_key() -> None:
    result = recommend_campaign(
        _preview(),
        settings=Settings(
            app_env="production",
            mip_agent_orchestrator=False,
            mip_genie_action_secret_current="configured-campaign-key-0123456789abcdef",
        ),
    )

    assert all(variant.provenance_token for variant in result.variants)


def test_non_dev_campaign_provenance_does_not_mint_with_previous_key_only() -> None:
    with pytest.raises(RuntimeError, match="requires a configured HMAC secret"):
        recommend_campaign(
            _preview(),
            settings=Settings(
                app_env="production",
                mip_agent_orchestrator=False,
                mip_genie_action_secret=None,
                mip_genie_action_secret_current=None,
                mip_genie_action_secret_previous="previous-key-is-verification-only-0123456789",
            ),
        )


def test_recommendation_adds_only_sample_qualified_observed_performance() -> None:
    result = recommend_campaign(
        _preview(),
        performance=_qualified_performance(),
        settings=Settings(mip_agent_orchestrator=False, mip_lender_name="Summit Mortgage"),
    )

    assert result.performance_status == "qualified"
    evidence = {row.label: row.value for row in result.evidence}
    assert evidence["Qualified performance observation"].endswith("fp aaaaaaaaaaaa")
    assert (
        evidence["Same-borrower attempted, reached, and application start"]
        == "100 attempted / 80 reached / 20 starts"
    )
    assert (
        evidence["Same-borrower application starts, submitted, and funded"]
        == "20 starts / 12 submitted / 3 funded"
    )
    proof = inspect_campaign_variant_provenance(
        result.variants[0].model_dump(),
        criteria_fingerprint=campaign_criteria_fingerprint({}),
    )
    assert proof is not None
    assert proof.performance_fingerprint == "a" * 64

    insufficient = recommend_campaign(
        _preview(),
        performance=CampaignPerformanceContext(
            unique_leads_attempted=40,
            unique_contacts_reached=29,
            unique_application_starts=20,
            unique_applications_submitted=9,
            unique_closed_funded=3,
        ),
        settings=Settings(mip_agent_orchestrator=False),
    )
    assert insufficient.performance_status == "insufficient_sample"
    assert not any(row.source_asset.startswith("mip_app.") for row in insufficient.evidence)
    insufficient_proof = inspect_campaign_variant_provenance(
        insufficient.variants[0].model_dump(),
        criteria_fingerprint=campaign_criteria_fingerprint({}),
    )
    assert insufficient_proof is not None
    assert insufficient_proof.performance_fingerprint is None

    non_monotonic = recommend_campaign(
        _preview(),
        performance=CampaignPerformanceContext(
            unique_leads_attempted=70,
            unique_contacts_reached=80,
            unique_application_starts=20,
            unique_applications_submitted=12,
            unique_closed_funded=3,
        ),
        settings=Settings(mip_agent_orchestrator=False),
    )
    assert non_monotonic.performance_status == "insufficient_sample"
    assert not any(row.source_asset.startswith("mip_app.") for row in non_monotonic.evidence)


def test_campaign_recommendation_route_returns_reviewed_strategy() -> None:
    response = client.post(
        "/api/portfolio/campaign-recommendation",
        json={"criteria": {}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generation_mode"] in {"supervisor", "reviewed_fallback"}
    assert len(payload["variants"]) == 2
    assert payload["evidence"]


class _ApiClient:
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            assert path == "/api/2.1/supervisor-agents/supervisor-123"
            return supervisor_metadata()
        assert method == "POST"
        assert path == "/serving-endpoints/responses"
        assert body is not None
        assert body["max_output_tokens"] == 900
        return {
            "output": [
                {
                    "content": [
                        {
                            "text": """{
                              "template_id": "benefit_guidance_v1",
                              "strategy_id": "controlled_message_test_v1"
                            }"""
                        }
                    ]
                }
            ]
        }


class _ServingEndpoints:
    def get(self, endpoint: str):
        if endpoint != GATEWAY_ENDPOINT:
            return supervisor_endpoint_details()
        assert endpoint == GATEWAY_ENDPOINT
        return gateway_endpoint_details()


class _PromptCaptureApiClient(_ApiClient):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        assert body is not None
        prompt = str(body["input"][0]["content"])  # type: ignore[index]
        self.prompts.append(prompt)
        return super().do(method, path, body=body)


class _RaisingApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        raise TimeoutError("serving endpoint timed out")


def test_supervisor_copy_is_validated_while_evidence_remains_server_derived() -> None:
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=_ApiClient())
    result = recommend_campaign(
        _preview(),
        settings=runtime_settings(
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=client,
    )

    assert result.generation_mode == "supervisor"
    assert result.holdout_pct == 10
    assert result.generator_label == "Databricks Agent Responses"
    assert result.strategy.startswith("Compare the reviewed benefit and guidance frames")
    assert all(variant.provenance_token for variant in result.variants)
    assert result.evidence[0].value == "2,119 borrowers"
    assert result.warnings == []


def test_supervisor_transport_failure_uses_labelled_data_backed_fallback() -> None:
    serving = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_RaisingApiClient(),
    )
    result = recommend_campaign(
        _preview(),
        performance=_qualified_performance(),
        settings=runtime_settings(
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=serving,
    )

    assert result.generation_mode == "reviewed_fallback"
    assert result.generator_label == "Reviewed campaign framework"
    assert result.warnings == ["Agent endpoint request failed"]
    assert result.performance_status == "qualified"
    assert any(row.source_asset == "mip_app.lead_outcomes" for row in result.evidence)
    assert result.evidence[0].value == "2,119 borrowers"


def test_model_prompt_omits_unqualified_performance_and_includes_complete_proof() -> None:
    api_client = _PromptCaptureApiClient()
    serving = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=api_client)
    settings = runtime_settings()
    low_quality = CampaignPerformanceContext(
        unique_leads_attempted=100,
        unique_contacts_reached=80,
        unique_application_starts=20,
        unique_applications_submitted=12,
        unique_closed_funded=3,
    )

    low_quality_result = recommend_campaign(
        _preview(),
        performance=low_quality,
        settings=settings,
        serving_client=serving,
    )
    qualified_result = recommend_campaign(
        _preview(),
        performance=_qualified_performance(),
        settings=settings,
        serving_client=serving,
    )

    assert low_quality_result.performance_status == "insufficient_sample"
    assert "qualified_observed_performance" not in api_client.prompts[0]
    assert "unique_leads_attempted" not in api_client.prompts[0]
    assert qualified_result.performance_status == "qualified"
    assert "qualified_observed_performance" in api_client.prompts[1]
    for field in (
        "observed_from",
        "observed_to",
        "snapshot_at",
        "interval_days",
        "observation_fingerprint",
    ):
        assert field in api_client.prompts[1]


class _UnsafeApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object]):
        return {
            "output": [
                {
                    "content": [
                        {
                            "text": """{
                              "audience_summary": "Women homeowners selected by propensity score.",
                              "strategy": "Create urgent conversion pressure.",
                              "holdout_pct": 10,
                              "variants": [
                                {
                                  "variant_name": "Benefit-led",
                                  "subject": "Act now to save 2 percent",
                                  "body": "Your score guarantees the lowest rate. Call today.",
                                  "hypothesis": "Pressure will increase conversion."
                                },
                                {
                                  "variant_name": "Guidance-led",
                                  "subject": "Women borrowers qualify",
                                  "body": "Public records show you are pre-approved. Reply now.",
                                  "hypothesis": "Protected targeting will convert."
                                }
                              ]
                            }"""
                        }
                    ]
                }
            ]
        }


class _InternalSummaryApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object]):
        _ = method, path, body
        token_marker = "DATABRICKS_" + "TOKEN=REDACTED"
        return {
            "output": [
                {
                    "content": [
                        {
                            "text": """{
                              "audience_summary": "Call https://workspace.internal/api/2.0/serving-endpoints/mip-supervisor with TOKEN_MARKER.",
                              "strategy": "Use a controlled message test with a review invitation.",
                              "holdout_pct": 10,
                              "variants": [
                                {
                                  "variant_name": "Benefit-led",
                                  "subject": "Review whether your mortgage options have improved",
                                  "body": "Compare available mortgage options with a licensed loan officer.",
                                  "hypothesis": "A concrete review invitation may support qualified responses."
                                },
                                {
                                  "variant_name": "Guidance-led",
                                  "subject": "A guided mortgage review",
                                  "body": "Explore current mortgage options with a licensed loan officer.",
                                  "hypothesis": "A guidance frame may support review requests."
                                }
                              ]
                            }""".replace("TOKEN_MARKER", token_marker)
                        }
                    ]
                }
            ]
        }


class _ExtraneousBorrowerCopyApiClient(_ApiClient):
    def do(self, method: str, path: str, *, body: dict[str, object] | None = None):
        if method == "GET":
            return super().do(method, path, body=body)
        return {
            "output": [
                {
                    "content": [
                        {
                            "text": """{
                              "template_id": "benefit_guidance_v1",
                              "strategy_id": "controlled_message_test_v1",
                              "subject": "This offer is for you based on your primary language",
                              "body": "Romani and intersex homeowners over 65 are approved. Reply now.",
                              "audience_summary": "Target encoded protected audiences."
                            }"""
                        }
                    ]
                }
            ]
        }


def test_unsafe_supervisor_copy_fails_closed_to_reviewed_fallback() -> None:
    serving = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_UnsafeApiClient(),
    )
    result = recommend_campaign(
        _preview(),
        settings=runtime_settings(
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=serving,
    )

    assert result.generation_mode == "reviewed_fallback"
    rendered = " ".join(
        [result.audience_summary, result.strategy]
        + [variant.subject + " " + variant.body for variant in result.variants]
    ).lower()
    assert "women" not in rendered
    assert "guarantee" not in rendered
    assert "score" not in rendered


def test_internal_supervisor_summary_is_dropped_via_reviewed_fallback() -> None:
    serving = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_InternalSummaryApiClient(),
    )
    result = recommend_campaign(
        _preview(),
        settings=runtime_settings(
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=serving,
    )

    assert result.generation_mode == "reviewed_fallback"
    rendered = " ".join([result.audience_summary, result.strategy]).lower()
    assert "dapi1234567890abcdef" not in rendered
    assert "workspace.internal" not in rendered


def test_supervisor_extra_copy_fields_fail_closed_without_copy_leak() -> None:
    serving = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_ExtraneousBorrowerCopyApiClient(),
    )
    settings = runtime_settings(mip_lender_name="Summit Mortgage")
    criteria_fingerprint = campaign_criteria_fingerprint({})

    result = recommend_campaign(
        _preview(),
        settings=settings,
        serving_client=serving,
        criteria_fingerprint=criteria_fingerprint,
    )

    rendered = " ".join(
        [result.audience_summary, result.strategy]
        + [variant.subject + " " + variant.body for variant in result.variants]
    ).lower()
    assert result.generation_mode == "reviewed_fallback"
    assert all(
        term not in rendered for term in ("romani", "intersex", "over 65", "primary language")
    )
    assert all(
        inspect_campaign_variant_provenance(
            variant.model_dump(),
            criteria_fingerprint=criteria_fingerprint,
            settings=settings,
        )
        is not None
        for variant in result.variants
    )
