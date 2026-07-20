"""Non-live schema checks for the Lakebase live campaign fixtures."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

import pytest
from pydantic import ValidationError

from backend.schemas.portfolio import PortfolioCreateRequest
from tests.integration import test_lakebase_concurrency_live as concurrency_live
from tests.integration import test_lakebase_idempotency_live as idempotency_live
from tests.integration.test_lakebase_concurrency_live import (
    _reviewed_campaign_create_payload as build_concurrency_campaign,
)
from tests.integration.test_lakebase_idempotency_live import (
    _reviewed_campaign_create_payload as build_idempotency_campaign,
)

_BENEFIT_PROVENANCE_TOKEN = "benefit-server-provenance-token-0000000000000001"
_GUIDANCE_PROVENANCE_TOKEN = "guidance-server-provenance-token-000000000000001"


def _recommendation_response() -> dict[str, object]:
    return {
        "generation_mode": "reviewed_fallback",
        "generator_label": "Reviewed campaign framework",
        "performance_status": "insufficient_sample",
        "audience_summary": (
            "The selected audience is led by borrowers whose current lien economics support a "
            "refinance review and is ready for a controlled message test."
        ),
        "strategy": "Compare benefit-led and guidance-led framing with a clear review invitation.",
        "variants": [
            {
                "variant_name": "Benefit-led",
                "subject": "Summit Mortgage options review",
                "body": "Compare current mortgage options with a licensed loan officer.",
                "hypothesis": "Benefit framing may support a review request.",
                "provenance_token": _BENEFIT_PROVENANCE_TOKEN,
            },
            {
                "variant_name": "Guidance-led",
                "subject": "A guided mortgage review",
                "body": "Explore current mortgage options with a licensed loan officer.",
                "hypothesis": "Guidance framing may support a review request.",
                "provenance_token": _GUIDANCE_PROVENANCE_TOKEN,
            },
        ],
        "holdout_pct": 10,
        "evidence": [
            {
                "label": "Eligible population",
                "value": "Reviewed cohort",
                "source_asset": "mip.gold.borrower_360",
            }
        ],
        "warnings": [],
    }


def test_old_operator_campaign_fixture_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reviewed server template"):
        PortfolioCreateRequest(
            name="Live Lakebase approval contract",
            criteria={"states": ["IL"]},
            message_variants=[
                {
                    "variant_name": "Approval proof",
                    "channel": "email",
                    "subject": "Review your mortgage options",
                    "body": "Reply to review your mortgage options with our team.",
                    "weight_pct": 100,
                    "generation_mode": "operator",
                }
            ],
        )


@pytest.mark.parametrize(
    ("builder", "name"),
    [
        (build_concurrency_campaign, "Live Lakebase concurrency contract"),
        (build_idempotency_campaign, "Live Lakebase approval contract"),
    ],
)
def test_live_fixture_projects_exact_server_provenance_into_valid_create_payload(
    builder: Callable[..., tuple[dict[str, object], object]],
    name: str,
) -> None:
    recommendation = _recommendation_response()
    payload, _validated_recommendation = builder(
        name=name,
        criteria={"states": ["IL"]},
        raw_recommendation=recommendation,
    )

    validated = PortfolioCreateRequest.model_validate(payload)
    variants = validated.message_variants
    assert [variant["variant_name"] for variant in variants] == [
        "Benefit-led",
        "Guidance-led",
    ]
    assert {variant["generation_mode"] for variant in variants} == {
        recommendation["generation_mode"]
    }
    assert {variant["generator_label"] for variant in variants} == {
        recommendation["generator_label"]
    }
    assert [variant["provenance_token"] for variant in variants] == [
        _BENEFIT_PROVENANCE_TOKEN,
        _GUIDANCE_PROVENANCE_TOKEN,
    ]
    assert [variant["weight_pct"] for variant in variants] == [45.0, 45.0]
    assert validated.holdout == {"method": "hash_modulo", "size_pct": 10.0}
    assert validated.household_dedup.model_dump() == {
        "enabled": True,
        "dedupe_unit": "household",
        "primary_contact_strategy": "highest_opportunity_eligible",
    }


@pytest.mark.parametrize(
    "builder",
    [build_concurrency_campaign, build_idempotency_campaign],
)
def test_live_fixture_refuses_recommendation_without_per_variant_server_proof(
    builder: Callable[..., tuple[dict[str, object], object]],
) -> None:
    recommendation = _recommendation_response()
    variants = recommendation["variants"]
    assert isinstance(variants, list)
    assert isinstance(variants[0], dict)
    variants[0]["provenance_token"] = None

    with pytest.raises(AssertionError, match="omitted the server-issued provenance token"):
        builder(
            name="Live Lakebase proof contract",
            criteria={"states": ["IL"]},
            raw_recommendation=recommendation,
        )


@pytest.mark.parametrize(
    "live_module",
    [concurrency_live, idempotency_live],
    ids=["concurrency", "idempotency"],
)
def test_approval_fixture_uses_public_governed_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    live_module: ModuleType,
) -> None:
    campaign_id = "campaign-live-fixture-contract"
    approver_token = "test-approver-token"
    calls: list[tuple[str, str, str | None, str | None, str | None]] = []

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        **kwargs: object,
    ) -> tuple[int, object]:
        target_status = str(payload.get("status")) if payload is not None else None
        expected_status = str(payload.get("expected_status")) if payload is not None else None
        token = kwargs.get("token")
        calls.append(
            (method, path, target_status, expected_status, str(token) if token else None)
        )
        if method == "PATCH":
            return 200, {"campaign_id": campaign_id, "status": target_status}
        return 200, {"campaign_id": campaign_id, "status": "approved"}

    monkeypatch.setattr(live_module, "ADMIN_TOKEN", approver_token)
    monkeypatch.setattr(live_module, "_request", request)

    live_module._approve_campaign_for_outreach(campaign_id)

    assert calls == [
        ("PATCH", f"/api/campaigns/{campaign_id}", "pending_review", "draft", None),
        (
            "PATCH",
            f"/api/campaigns/{campaign_id}",
            "approved",
            "pending_review",
            approver_token,
        ),
        ("GET", f"/api/campaigns/{campaign_id}", None, None, None),
    ]


def test_concurrency_approval_fixture_approves_campaign_before_drafting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "campaign-concurrency-approval-fixture"
    events: list[str] = []
    monkeypatch.setattr(
        concurrency_live,
        "_create_campaign",
        lambda: (campaign_id, "Benefit-led", "email", ["B-0000000000001"]),
    )

    def approve(actual_campaign_id: str) -> None:
        assert actual_campaign_id == campaign_id
        events.append("approved")

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        assert events == ["approved"]
        assert (method, path) == ("POST", "/api/outreach/draft")
        assert payload is not None and payload.get("campaign_id") == campaign_id
        events.append("drafted")
        return 200, {"campaign_id": campaign_id}

    monkeypatch.setattr(concurrency_live, "_approve_campaign_for_outreach", approve)
    monkeypatch.setattr(concurrency_live, "_request", request)

    returned_campaign_id, draft = concurrency_live._campaign_draft()

    assert returned_campaign_id == campaign_id
    assert draft == {"campaign_id": campaign_id}
    assert events == ["approved", "drafted"]


def test_idempotency_approval_fixture_approves_campaign_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "campaign-idempotency-approval-fixture"
    events: list[str] = []
    recommendation = _recommendation_response()
    variants = recommendation["variants"]
    assert isinstance(variants, list) and isinstance(variants[0], dict)
    expected_variant = variants[0]

    monkeypatch.setattr(
        idempotency_live,
        "_tiny_reviewed_campaign_criteria",
        lambda: ({"states": ["IL"]}, ["B-0000000000001"]),
    )

    def approve(actual_campaign_id: str) -> None:
        assert actual_campaign_id == campaign_id
        events.append("approved")

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        assert not events
        if path == "/api/portfolio/campaign-recommendation":
            return 200, recommendation
        if path == "/api/portfolio/create":
            return 200, {"campaign_id": campaign_id}
        assert (method, path) == ("GET", f"/api/campaigns/{campaign_id}")
        return 200, {
            "campaign_id": campaign_id,
            "message_variants": [
                {
                    **expected_variant,
                    "channel": "email",
                    "generation_mode": recommendation["generation_mode"],
                    "generator_label": recommendation["generator_label"],
                    "copy_verified_at_creation": True,
                }
            ],
        }

    monkeypatch.setattr(idempotency_live, "_approve_campaign_for_outreach", approve)
    monkeypatch.setattr(idempotency_live, "_request", request)

    result = idempotency_live._create_email_campaign_variant()

    assert result == (campaign_id, "Benefit-led", "email", ["B-0000000000001"])
    assert events == ["approved"]
