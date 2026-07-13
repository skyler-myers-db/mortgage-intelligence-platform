from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.config.settings import Settings
from backend.main import app
from backend.schemas.portfolio import PortfolioOfferMixRow, PortfolioPreview
from backend.services.campaign_intelligence import CampaignPerformanceContext, recommend_campaign

client = TestClient(app)


def _preview() -> PortfolioPreview:
    return PortfolioPreview(
        marketable_population=2_119,
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


def test_reviewed_fallback_is_labelled_and_uses_governed_cohort_metrics() -> None:
    result = recommend_campaign(
        _preview(),
        settings=Settings(mip_agent_orchestrator=False, mip_lender_name="Summit Mortgage"),
    )

    assert result.generation_mode == "reviewed_fallback"
    assert result.performance_status == "unavailable"
    assert "2,119 eligible borrowers" in result.audience_summary
    assert result.holdout_pct == 10
    assert {row.source_asset for row in result.evidence} == {
        "mip.semantics.portfolio_headline_metric_view",
        "mip.gold.borrower_360",
    }
    assert result.variants[0].body != result.variants[1].body
    assert all("guarantee" not in variant.body.lower() for variant in result.variants)


def test_recommendation_adds_only_sample_qualified_observed_performance() -> None:
    result = recommend_campaign(
        _preview(),
        performance=CampaignPerformanceContext(
            unique_leads_attempted=100,
            unique_contacts_reached=80,
            unique_application_starts=20,
            unique_applications_submitted=12,
            unique_closed_funded=3,
        ),
        settings=Settings(mip_agent_orchestrator=False, mip_lender_name="Summit Mortgage"),
    )

    assert result.performance_status == "qualified"
    evidence = {row.label: row.value for row in result.evidence}
    assert (
        evidence["Team 90-day same-borrower attempted, reached, and application start"]
        == "100 attempted / 80 reached / 20 starts"
    )
    assert (
        evidence["Team 90-day same-borrower application to submitted"]
        == "12 / 20 application starts"
    )
    assert evidence["Team 90-day same-borrower submitted to funded"] == "3 / 12 submitted"

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
    def do(self, method: str, path: str, *, body: dict[str, object]):
        assert method == "POST"
        assert path == "/serving-endpoints/responses"
        assert body["max_output_tokens"] == 900
        return {
            "output": [
                {
                    "content": [
                        {
                            "text": """{
                              "audience_summary": "A refinance-focused eligible cohort with measurable equity and rate-spread signals.",
                              "strategy": "Compare a concrete options review with a lower-pressure guidance frame and measure qualified response.",
                              "holdout_pct": 12,
                              "variants": [
                                {
                                  "variant_name": "Benefit-led",
                                  "subject": "Review whether your mortgage options have improved",
                                  "body": "A fresh mortgage review can help you compare available options and understand the tradeoffs. Would you like to schedule time with a loan officer?",
                                  "hypothesis": "A concrete options benefit will increase qualified review requests."
                                },
                                {
                                  "variant_name": "Guidance-led",
                                  "subject": "Get a clearer view of your current mortgage",
                                  "body": "Your mortgage choices can change over time. A loan officer can explain what fits now without assuming a new loan is the answer. Would a review be useful?",
                                  "hypothesis": "A no-pressure guidance frame will improve trust and response quality."
                                }
                              ]
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


def test_supervisor_copy_is_validated_while_evidence_remains_server_derived() -> None:
    client = SimpleNamespace(serving_endpoints=_ServingEndpoints(), api_client=_ApiClient())
    result = recommend_campaign(
        _preview(),
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
            mip_lender_name="Summit Mortgage",
        ),
        serving_client=client,
    )

    assert result.generation_mode == "supervisor"
    assert result.holdout_pct == 12
    assert result.generator_label == "Supervisor-generated recommendation"
    assert result.evidence[0].value == "2,119 borrowers"
    assert result.warnings == []


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


def test_unsafe_supervisor_copy_fails_closed_to_reviewed_fallback() -> None:
    serving = SimpleNamespace(
        serving_endpoints=_ServingEndpoints(),
        api_client=_UnsafeApiClient(),
    )
    result = recommend_campaign(
        _preview(),
        settings=Settings(
            mip_agent_orchestrator=True,
            mip_agent_serving_endpoint="mip-supervisor",
            mip_agent_supervisor_id="supervisor-id",
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
