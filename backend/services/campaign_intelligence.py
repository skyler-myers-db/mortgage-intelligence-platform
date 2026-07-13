"""Evidence-grounded campaign recommendations for Portfolio Builder.

The Supervisor may propose message strategy and copy, but it never supplies
metrics, source citations, eligibility rules, or approval policy. Those remain
server-derived from the exact governed cohort. A reviewed deterministic
fallback is labelled explicitly when the Supervisor is unavailable or its
output fails validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from backend.agents.mortgage_growth_copilot import (
    agent_task_if_ready,
    extract_response_text,
    parse_json_object,
    prompt_hash,
    workspace_client,
)
from backend.config.settings import Settings, get_settings
from backend.schemas.portfolio import (
    CampaignRecommendationEvidence,
    CampaignRecommendationResponse,
    CampaignRecommendationVariant,
    PortfolioPreview,
)
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.growth_agent_composer import composition_endpoint
from backend.services.scoring import NBO_PRODUCT_LABELS, offer_display_label

_OFFER_AUDIENCE: dict[str, str] = {
    "purchase": "borrowers whose current property and listing signals support a next-home conversation",
    "refi_plus_heloc": "borrowers with refinance economics and usable home equity",
    "heloc": "borrowers whose equity and HELOC propensity support an equity-access review",
    "refi": "borrowers whose current lien economics support a refinance review",
    "cash_out": "borrowers with substantial modeled equity for a cash-out review",
    "investor": "multi-property borrowers whose financing needs may span more than one property",
    "retention": "existing or former customers with a timely retention review signal",
    "nurture": "borrowers who should receive education rather than a product-specific claim",
}


@dataclass(frozen=True)
class CampaignPerformanceContext:
    unique_leads_attempted: int = 0
    unique_contacts_reached: int = 0
    unique_application_starts: int = 0
    unique_applications_submitted: int = 0
    unique_closed_funded: int = 0

    @property
    def is_monotonic(self) -> bool:
        return (
            self.unique_leads_attempted
            >= self.unique_contacts_reached
            >= self.unique_application_starts
            >= self.unique_applications_submitted
            >= self.unique_closed_funded
            >= 0
        )

    @property
    def reach_rate(self) -> float | None:
        if self.unique_leads_attempted < 30 or not self.is_monotonic:
            return None
        return self.unique_contacts_reached / self.unique_leads_attempted

    @property
    def application_start_rate(self) -> float | None:
        if self.unique_contacts_reached < 30 or not self.is_monotonic:
            return None
        return self.unique_application_starts / self.unique_contacts_reached

    @property
    def submission_rate(self) -> float | None:
        if self.unique_application_starts < 10 or not self.is_monotonic:
            return None
        return self.unique_applications_submitted / self.unique_application_starts

    @property
    def close_rate(self) -> float | None:
        if self.unique_applications_submitted < 10 or not self.is_monotonic:
            return None
        return self.unique_closed_funded / self.unique_applications_submitted

    @property
    def is_qualified(self) -> bool:
        return (
            self.reach_rate is not None
            and self.application_start_rate is not None
            and self.submission_rate is not None
            and self.close_rate is not None
        )


def _performance_status(
    performance: CampaignPerformanceContext | None,
) -> Literal["qualified", "insufficient_sample", "unavailable"]:
    if performance is None:
        return "unavailable"
    return "qualified" if performance.is_qualified else "insufficient_sample"


def _dominant_offer(preview: PortfolioPreview) -> tuple[str, int]:
    ranked = sorted(preview.offer_mix, key=lambda row: (-row.borrower_count, row.offer_code))
    if not ranked or ranked[0].borrower_count <= 0:
        return "nurture", preview.marketable_population
    return ranked[0].offer_code, ranked[0].borrower_count


def _evidence(
    preview: PortfolioPreview,
    performance: CampaignPerformanceContext | None,
) -> list[CampaignRecommendationEvidence]:
    rows = [
        CampaignRecommendationEvidence(
            label="Eligible cohort",
            value=f"{preview.marketable_population:,} borrowers",
            source_asset="mip.semantics.portfolio_headline_metric_view",
        )
    ]
    if preview.avg_current_lien_balance_usd is not None:
        rows.append(
            CampaignRecommendationEvidence(
                label="Average current lien balance",
                value=f"${preview.avg_current_lien_balance_usd:,}",
                source_asset="mip.gold.borrower_360",
            )
        )
    if preview.avg_equity_pct is not None:
        rows.append(
            CampaignRecommendationEvidence(
                label="Average modeled equity",
                value=f"{preview.avg_equity_pct:.1f}%",
                source_asset="mip.gold.borrower_360",
            )
        )
    if preview.avg_rate_spread_bps is not None:
        rows.append(
            CampaignRecommendationEvidence(
                label="Average rate spread",
                value=f"{preview.avg_rate_spread_bps:.1f} bps",
                source_asset="mip.gold.borrower_360",
            )
        )
    offer_code, offer_count = _dominant_offer(preview)
    rows.append(
        CampaignRecommendationEvidence(
            label="Largest primary-offer path",
            value=f"{offer_display_label(offer_code, NBO_PRODUCT_LABELS[offer_code])}: {offer_count:,}",
            source_asset="mip.gold.borrower_360",
        )
    )
    if performance is not None and performance.is_qualified:
        rows.append(
            CampaignRecommendationEvidence(
                label="Team 90-day same-borrower attempted, reached, and application start",
                value=(
                    f"{performance.unique_leads_attempted:,} attempted / "
                    f"{performance.unique_contacts_reached:,} reached / "
                    f"{performance.unique_application_starts:,} starts"
                ),
                source_asset="mip_app.call_dispositions",
            )
        )
        rows.append(
            CampaignRecommendationEvidence(
                label="Team 90-day same-borrower application to submitted",
                value=(
                    f"{performance.unique_applications_submitted:,} / "
                    f"{performance.unique_application_starts:,} application starts"
                ),
                source_asset="mip_app.lead_outcomes",
            )
        )
        rows.append(
            CampaignRecommendationEvidence(
                label="Team 90-day same-borrower submitted to funded",
                value=(
                    f"{performance.unique_closed_funded:,} / "
                    f"{performance.unique_applications_submitted:,} submitted"
                ),
                source_asset="mip_app.lead_outcomes",
            )
        )
    return rows


def _fallback(
    preview: PortfolioPreview,
    *,
    lender_name: str,
    performance: CampaignPerformanceContext | None,
    warning: str,
) -> CampaignRecommendationResponse:
    offer_code, offer_count = _dominant_offer(preview)
    offer_label = offer_display_label(offer_code, NBO_PRODUCT_LABELS[offer_code]).lower()
    audience = _OFFER_AUDIENCE[offer_code]
    lender = lender_name.strip() or "your lender"
    return CampaignRecommendationResponse(
        generation_mode="reviewed_fallback",
        generator_label="Reviewed campaign framework",
        performance_status=_performance_status(performance),
        audience_summary=(
            f"{preview.marketable_population:,} eligible borrowers, including {offer_count:,} {audience}."
        ),
        strategy=(
            "Test a concrete benefit-led explanation against a guidance-led review. Keep one call to "
            "action, avoid unverified savings claims, and reserve a randomized holdout for measurement."
        ),
        variants=[
            CampaignRecommendationVariant(
                variant_name="Benefit-led",
                subject=f"See whether your mortgage options have improved with {lender}",
                body=(
                    f"A {offer_label} review can help you compare your current mortgage with other available options. "
                    f"A {lender} loan officer can explain the tradeoffs in plain language. "
                    "Would you like to schedule a review?"
                ),
                hypothesis=(
                    "A specific potential benefit and a low-friction review invitation will earn more "
                    "qualified responses than a generic rate message."
                ),
            ),
            CampaignRecommendationVariant(
                variant_name="Guidance-led",
                subject="A clearer way to review your current mortgage",
                body=(
                    f"Mortgage choices can change as your balance, equity, and goals change. A {lender} "
                    f"loan officer can walk through whether a {offer_label} fits your situation, with no "
                    "assumption that changing your loan is the right answer. Would a review be useful?"
                ),
                hypothesis=(
                    "Plain-language guidance and an explicit no-pressure frame will improve trust and "
                    "response quality for borrowers who are not ready for a product-led message."
                ),
            ),
        ],
        holdout_pct=10,
        evidence=_evidence(preview, performance),
        warnings=[warning],
    )


def _prompt(
    preview: PortfolioPreview,
    *,
    lender_name: str,
    performance: CampaignPerformanceContext | None,
    repair_note: str | None,
) -> str:
    offer_code, offer_count = _dominant_offer(preview)
    payload = {
        "eligible_borrowers": preview.marketable_population,
        "refi_economics_borrowers": preview.high_intent_leads,
        "average_current_lien_balance_usd": preview.avg_current_lien_balance_usd,
        "average_modeled_equity_pct": preview.avg_equity_pct,
        "average_rate_spread_bps": preview.avg_rate_spread_bps,
        "dominant_offer_code": offer_code,
        "dominant_offer_borrowers": offer_count,
        "lender_label": lender_name,
        "team_90d_unique_leads_attempted": (
            performance.unique_leads_attempted if performance is not None else None
        ),
        "team_90d_unique_contacts_reached": (
            performance.unique_contacts_reached if performance is not None else None
        ),
        "team_90d_unique_application_starts": (
            performance.unique_application_starts if performance is not None else None
        ),
        "team_90d_unique_applications_submitted": (
            performance.unique_applications_submitted if performance is not None else None
        ),
        "team_90d_unique_closed_funded": (
            performance.unique_closed_funded if performance is not None else None
        ),
        "team_90d_performance_qualified": bool(performance and performance.is_qualified),
    }
    repair = f"\nYour previous JSON failed validation: {repair_note}\nReturn corrected JSON only." if repair_note else ""
    return (
        "You are the campaign-strategy specialist inside a governed mortgage growth Supervisor. "
        "Use only the aggregate cohort facts below. Create two genuinely distinct email tests: one "
        "benefit-led and one guidance-led. Use plain language, one low-friction call to action, and no "
        "guaranteed savings, quoted rates, false urgency, protected traits, personal data, placeholders, "
        "or unsupported claims. Observed performance is strategy context only: never expose cohort counts "
        "or performance metrics in the borrower-facing subject or body. The hypotheses must say what "
        "behavior each variant tests. Return JSON "
        "only with keys audience_summary, strategy, holdout_pct, variants. variants must contain exactly "
        "two objects with variant_name (Benefit-led or Guidance-led), subject, body, hypothesis. Set "
        "holdout_pct between 5 and 30.\nAggregate cohort facts:\n"
        f"{json.dumps(payload, sort_keys=True)}{repair}"
    )


def recommend_campaign(
    preview: PortfolioPreview,
    *,
    performance: CampaignPerformanceContext | None = None,
    settings: Settings | None = None,
    serving_client: Any | None = None,
) -> CampaignRecommendationResponse:
    settings = settings or get_settings()
    lender_name = settings.mip_lender_name
    endpoint, reason = composition_endpoint(settings)
    if endpoint is None:
        return _fallback(
            preview,
            lender_name=lender_name,
            performance=performance,
            warning=reason or "Supervisor unavailable",
        )

    try:
        client = serving_client or workspace_client()
        task = agent_task_if_ready(client, endpoint)
    except Exception:  # noqa: BLE001 - recommendation degrades honestly
        return _fallback(
            preview,
            lender_name=lender_name,
            performance=performance,
            warning="Supervisor readiness check failed",
        )
    if task is None:
        return _fallback(
            preview,
            lender_name=lender_name,
            performance=performance,
            warning="Supervisor is not ready",
        )

    evidence = _evidence(preview, performance)
    repair_note: str | None = None
    for attempt in range(2):
        prompt = _prompt(
            preview,
            lender_name=lender_name,
            performance=performance,
            repair_note=repair_note,
        )
        try:
            response = query_serving_endpoint(
                client,
                endpoint,
                task=task,
                prompt=prompt,
                max_tokens=900,
                client_request_id=f"mip-campaign-{prompt_hash(prompt)[:18]}-{attempt}",
            )
            if not serving_response_has_payload(response):
                raise ValueError("empty Supervisor response")
            parsed = parse_json_object(extract_response_text(response))
            if parsed is None:
                raise ValueError("Supervisor response was not a JSON object")
            candidate = CampaignRecommendationResponse.model_validate(
                {
                    "generation_mode": "supervisor",
                    "generator_label": "Supervisor-generated recommendation",
                    "performance_status": _performance_status(performance),
                    "audience_summary": parsed.get("audience_summary"),
                    "strategy": parsed.get("strategy"),
                    "variants": parsed.get("variants"),
                    "holdout_pct": parsed.get("holdout_pct", 10),
                    "evidence": [item.model_dump() for item in evidence],
                    "warnings": [],
                }
            )
            return candidate
        except (ValidationError, ValueError, TypeError) as exc:
            repair_note = str(exc)[:500]
        except Exception:  # noqa: BLE001 - network/platform failure uses labelled fallback
            return _fallback(
                preview,
                lender_name=lender_name,
                performance=performance,
                warning="Supervisor request failed",
            )
    return _fallback(
        preview,
        lender_name=lender_name,
        performance=performance,
        warning="Supervisor output failed validation",
    )
