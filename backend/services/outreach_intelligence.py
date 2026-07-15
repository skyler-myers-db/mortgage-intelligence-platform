"""Supervisor-assisted borrower outreach with deterministic safety rails.

The Supervisor may improve the message strategy and prose for email and direct
mail. It never chooses the offer, receives raw borrower identity, supplies a
financial figure, changes the tenant disclosure, or bypasses human approval.
SMS stays on the reviewed deterministic framework because the disclosure and
160-character limit leave too little room for a useful model rewrite.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from backend.agents.mortgage_growth_copilot import (
    extract_response_text,
    parse_json_object,
    prompt_hash,
    workspace_client,
)
from backend.config.settings import Settings, get_settings
from backend.schemas.portfolio import CampaignRecommendationVariant
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.outreach_copy import (
    _TRIGGER_TERM_RE,
    _borrower_offer_phrase,
    _compose_outreach_body,
    _personalization_hook,
    _safe_offer_code,
)
from backend.services.supervisor_runtime import verify_supervisor_runtime

OutreachGenerationMode = Literal["supervisor", "governed_fallback"]

_UNSUPPORTED_CLAIM_RE = re.compile(
    r"\b(?:guarantee(?:d)?|pre[- ]?approved|lowest|best rate|save(?:s|d|ing)?\s+"
    r"(?:money|hundreds|thousands)|act now|limited time|expires? today|no credit check)\b",
    re.IGNORECASE,
)
_CTA_RE = re.compile(
    r"\b(?:reply|schedule|talk|speak|call|review|compare|learn more)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class IntelligentOutreachDraft:
    subject: str | None
    body: str
    generation_mode: OutreachGenerationMode
    generator_label: str
    strategy_summary: str
    evidence_summary: list[str]
    evidence_assets: list[str]


@dataclass(frozen=True)
class GovernedCampaignVariant:
    """Exact persisted campaign copy selected for this borrower draft."""

    campaign_id: str
    variant_name: str
    channel: str
    subject: str | None
    body: str
    generation_mode: str
    generator_label: str
    treatment_fingerprint: str


def _relationship_label(borrower: Any) -> str:
    if bool(getattr(borrower, "is_current_customer", False)):
        return "current customer"
    if bool(getattr(borrower, "is_former_customer", False)):
        return "former customer"
    if bool(getattr(borrower, "is_competitor_lien", False)):
        return "borrower whose current mortgage is held elsewhere"
    return "prospective borrower"


def _evidence_summary(borrower: Any) -> list[str]:
    code = _safe_offer_code(getattr(borrower, "recommended_offer_code", None))
    rows = [
        f"Primary offer: {_borrower_offer_phrase(code)}",
        f"Relationship: {_relationship_label(borrower)}",
    ]
    hook = _personalization_hook(borrower)
    if hook:
        rows.append(f"Message signal: {hook}")
    return rows


def _evidence_assets() -> list[str]:
    return [
        "mip.gold.borrower_360",
        "mip.gold.evidence_events",
        "mip.gold.fn_next_best_offer",
    ]


def _fallback(
    *,
    borrower: Any,
    channel: str,
    disclosure: Any,
    warning: str,
    campaign_variant: GovernedCampaignVariant | None = None,
) -> IntelligentOutreachDraft:
    if campaign_variant is None:
        subject, body = _compose_outreach_body(
            borrower=borrower,
            channel=channel,
            disclosure=disclosure,
        )
        generator_label = "Governed message framework"
        strategy_summary = (
            "Uses the selected offer, borrower relationship, and strongest qualitative signal with "
            f"one low-friction response path. {warning}"
        )
    else:
        if campaign_variant.channel != channel:
            raise ValueError("campaign variant channel does not match the outreach channel")
        variant_body = campaign_variant.body.strip()
        disclosure_body = str(disclosure.body).strip()
        if not variant_body or not disclosure_body:
            raise ValueError("campaign variant and disclosure copy must be nonblank")
        subject = None if channel == "sms" else (campaign_variant.subject or "").strip() or None
        if channel in {"email", "direct_mail"} and subject is None:
            raise ValueError("campaign variant subject is required for this outreach channel")
        if channel == "email":
            body = f"Hello,\n\n{variant_body}\n\n{disclosure_body}"
        elif channel == "direct_mail":
            body = f"{variant_body}\n\n{disclosure_body}"
        else:
            body = f"{variant_body} {disclosure_body}"
            if len(body) > 160:
                raise ValueError("campaign SMS variant plus disclosure exceeds 160 characters")
        generator_label = campaign_variant.generator_label
        strategy_summary = (
            f"Uses governed campaign variant {campaign_variant.variant_name} as the authoritative "
            f"message framework. {warning}"
        )
    return IntelligentOutreachDraft(
        subject=subject,
        body=body,
        generation_mode="governed_fallback",
        generator_label=generator_label,
        strategy_summary=strategy_summary,
        evidence_summary=_evidence_summary(borrower),
        evidence_assets=_evidence_assets(),
    )


def _prompt(
    *,
    borrower: Any,
    channel: str,
    lender_name: str,
    repair_note: str | None,
    campaign_variant: GovernedCampaignVariant | None,
) -> str:
    code = _safe_offer_code(getattr(borrower, "recommended_offer_code", None))
    facts: dict[str, Any] = {
        "channel": channel,
        "lender_label": lender_name,
        "primary_offer": _borrower_offer_phrase(code),
        "relationship": _relationship_label(borrower),
        "qualitative_signal": _personalization_hook(borrower) or "no specific signal safe for copy",
    }
    if campaign_variant is not None:
        facts["governed_campaign_variant"] = {
            "variant_name": campaign_variant.variant_name,
            "subject": campaign_variant.subject,
            "body": campaign_variant.body,
        }
    repair = (
        f"\nThe previous JSON failed validation: {repair_note}\nReturn corrected JSON only."
        if repair_note
        else ""
    )
    return (
        "You are the borrower-communications specialist inside a governed mortgage Supervisor. "
        "Write a concise, natural message that helps the borrower understand the potential relevance "
        "and benefit of a review without assuming that a new loan is right for them. Use only the "
        "qualitative facts below. Do not include a name, address, account detail, balance, percentage, "
        "rate, savings amount, urgency, scarcity, guarantee, pre-approval, protected trait, disclosure, "
        "signature, greeting placeholder, or invented fact. Avoid mortgage-industry jargon. Use one "
        "specific benefit and one autonomy-supportive call to action. Return JSON only with subject, "
        "body, and strategy_summary. The body must stand alone without a greeting and must not mention "
        "the targeting signal or internal scoring. When governed_campaign_variant is present, preserve "
        "its core positioning and use it as the authoritative message context. strategy_summary explains "
        "why the approach fits this "
        f"audience in one sentence.\nGoverned qualitative facts:\n{json.dumps(facts, sort_keys=True)}"
        f"{repair}"
    )


def _validate_model_copy(parsed: dict[str, Any]) -> tuple[str, str, str]:
    # Reuse the campaign-copy PII/name/placeholder validation contract. The
    # variant label is internal and fixed; no model-controlled label is stored.
    validated = CampaignRecommendationVariant.model_validate(
        {
            "variant_name": "Benefit-led",
            "subject": parsed.get("subject"),
            "body": parsed.get("body"),
            "hypothesis": parsed.get("strategy_summary"),
        }
    )
    combined = f"{validated.subject}\n{validated.body}"
    if _TRIGGER_TERM_RE.search(combined):
        raise ValueError("copy contained a numeric financial trigger term")
    if _UNSUPPORTED_CLAIM_RE.search(combined):
        raise ValueError("copy contained an unsupported or high-pressure claim")
    if not _CTA_RE.search(validated.body):
        raise ValueError("copy did not include a review-oriented call to action")
    return validated.subject, validated.body, validated.hypothesis


def compose_intelligent_outreach(
    *,
    borrower: Any,
    channel: str,
    disclosure: Any,
    settings: Settings | None = None,
    serving_client: Any | None = None,
    campaign_variant: GovernedCampaignVariant | None = None,
) -> IntelligentOutreachDraft:
    """Return Supervisor-optimized copy or an explicitly labelled fallback."""

    settings = settings or get_settings()
    if channel == "sms":
        return _fallback(
            borrower=borrower,
            channel=channel,
            disclosure=disclosure,
            warning="SMS uses the reviewed 160-character framework.",
            campaign_variant=campaign_variant,
        )

    try:
        client = serving_client or workspace_client()
    except Exception:  # noqa: BLE001 - model failure must not break review
        return _fallback(
            borrower=borrower,
            channel=channel,
            disclosure=disclosure,
            warning="Supervisor readiness check failed.",
            campaign_variant=campaign_variant,
        )
    runtime, reason = verify_supervisor_runtime(client, settings)
    if runtime is None:
        return _fallback(
            borrower=borrower,
            channel=channel,
            disclosure=disclosure,
            warning=f"Supervisor unavailable ({reason or 'not configured'}).",
            campaign_variant=campaign_variant,
        )
    endpoint = runtime.endpoint
    task = runtime.task

    repair_note: str | None = None
    lender_name = (settings.mip_lender_name or "configured lender").strip() or "configured lender"
    for attempt in range(2):
        prompt = _prompt(
            borrower=borrower,
            channel=channel,
            lender_name=lender_name,
            repair_note=repair_note,
            campaign_variant=campaign_variant,
        )
        try:
            response = query_serving_endpoint(
                client,
                endpoint,
                task=task,
                prompt=prompt,
                max_tokens=700,
                client_request_id=f"mip-outreach-{prompt_hash(prompt)[:18]}-{attempt}",
            )
            if not serving_response_has_payload(response):
                raise ValueError("empty Supervisor response")
            parsed = parse_json_object(extract_response_text(response))
            if parsed is None:
                raise ValueError("Supervisor response was not a JSON object")
            subject, model_body, strategy = _validate_model_copy(parsed)
            body = (
                f"Hello,\n\n{model_body}\n\n{disclosure.body}"
                if channel == "email"
                else f"{model_body}\n\n{disclosure.body}"
            )
            return IntelligentOutreachDraft(
                subject=subject,
                body=body,
                generation_mode="supervisor",
                generator_label="Supervisor-optimized message",
                strategy_summary=strategy,
                evidence_summary=_evidence_summary(borrower),
                evidence_assets=_evidence_assets(),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            repair_note = str(exc)[:500]
        except Exception:  # noqa: BLE001 - platform failure uses honest fallback
            return _fallback(
                borrower=borrower,
                channel=channel,
                disclosure=disclosure,
                warning="Supervisor request failed.",
                campaign_variant=campaign_variant,
            )

    return _fallback(
        borrower=borrower,
        channel=channel,
        disclosure=disclosure,
        warning="Supervisor output failed validation.",
        campaign_variant=campaign_variant,
    )
