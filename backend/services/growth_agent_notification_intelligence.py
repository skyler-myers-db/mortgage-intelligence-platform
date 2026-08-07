"""Supervisor-assisted internal notifications for saved Growth Agent monitors."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from backend.agents.mortgage_growth_copilot import (
    extract_response_text,
    parse_json_object,
    prompt_hash,
    workspace_client,
)
from backend.config.settings import Settings, get_settings
from backend.schemas._validators_person_names import contains_contextual_human_name
from backend.schemas.portfolio_campaign import assert_public_campaign_text
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.supervisor_runtime import verify_supervisor_runtime

NotificationGenerationMode = Literal["supervisor", "governed_fallback"]
_UNSAFE_FRAGMENT_RE = re.compile(
    r"(?:https?://|/lead-queue|\b\d+(?:[.,]\d+)*\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"\b(?:guarantee(?:d|s)?|urgenc(?:y|ies)|urgent(?:ly)?|act now|send now|customer name|borrower name|"
    r"increase(?:d)?|decrease(?:d)?|improve(?:d)?|decline(?:d)?|convert(?:ed)?|"
    r"funded|submitted|application|response rate|conversion|performance|"
    r"record high|record low)\b)",
    re.IGNORECASE,
)


def _normalized_channel_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized))


def _channel_similarity(left: str, right: str) -> float:
    """Measure material overlap without depending on word order or punctuation."""

    left_tokens = set(_normalized_channel_text(left).split())
    right_tokens = set(_normalized_channel_text(right).split())
    if not left_tokens or not right_tokens:
        return 1.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


class _NotificationFragments(BaseModel):
    slack_context: str = Field(min_length=10, max_length=120)
    teams_summary: str = Field(min_length=10, max_length=220)
    operator_action: str = Field(min_length=10, max_length=140)

    @field_validator("slack_context", "teams_summary", "operator_action")
    @classmethod
    def _safe_fragment(cls, value: str) -> str:
        clean = " ".join(value.split())
        if _UNSAFE_FRAGMENT_RE.search(clean):
            raise ValueError("notification fragment contains a server-controlled or unsafe value")
        try:
            clean = assert_public_campaign_text(
                clean,
                field_name="notification fragment",
                max_length=220,
            )
        except ValueError as exc:
            raise ValueError(
                "notification fragment contains unsafe targeting or identity text"
            ) from exc
        if contains_contextual_human_name(clean):
            raise ValueError("notification fragment contains identity-shaped text")
        return clean.rstrip(".")

    @model_validator(mode="after")
    def _differentiate_channels(self) -> _NotificationFragments:
        if _channel_similarity(self.slack_context, self.teams_summary) >= 0.72:
            raise ValueError("Slack and Teams notification fragments must be materially different")
        return self


@dataclass(frozen=True)
class NotificationIntelligence:
    slack_context: str
    teams_summary: str
    operator_action: str
    generation_mode: NotificationGenerationMode
    generator_label: str
    strategy_summary: str


_WORKFLOW_FALLBACKS: dict[str, tuple[str, str, str]] = {
    "daily_refi_brief": (
        "The refinance-economics queue refreshed for loan-officer triage",
        "The refinance watchlist is ready for rate-spread prioritization and ownership review",
        "Review the strongest refinance economics and assign the next owner",
    ),
    "borrower_dossier_review": (
        "The top-opportunity dossier queue refreshed for evidence review",
        "The dossier watchlist is ready for evidence, offer-fit, and ownership review",
        "Review the leading dossier evidence and assign the next owner",
    ),
    "listing_watch": (
        "The listed-property purchase watch refreshed for loan-officer review",
        "The purchase watchlist is ready for listing-signal and next-home opportunity review",
        "Review the listing evidence and assign the next purchase-opportunity owner",
    ),
    "competitor_recapture_monitor": (
        "The competitor-lien watch refreshed for recapture review",
        "The recapture watchlist is ready for relationship, lien, and ownership review",
        "Review the competitor-lien evidence and assign the next recapture owner",
    ),
    "high_equity_heloc_watch": (
        "The home-equity intent watch refreshed for product-fit review",
        "The home-equity watchlist is ready for equity, propensity, and ownership review",
        "Review the equity evidence and assign the next product-fit owner",
    ),
    "branch_capacity_review": (
        "The branch-capacity watch refreshed for assignment review",
        "The capacity watchlist is ready for queue-balance and ownership review",
        "Review queue distribution and confirm the next assignment plan",
    ),
    "source_freshness_sentinel": (
        "The data-freshness watch refreshed for operations review",
        "The source-readiness watchlist is ready for freshness and dependency review",
        "Review source readiness and assign any required operations follow-up",
    ),
    "custom_segment_watch": (
        "The configured segment watch refreshed for queue review",
        "The configured segment watchlist is ready for evidence and ownership review",
        "Review the selected segment evidence and confirm the next operating step",
    ),
}

_DEFAULT_FALLBACK = (
    "The saved watchlist refreshed and is ready for review",
    "The saved watchlist is ready for evidence and ownership review",
    "Review the ranked queue and confirm the next operating step",
)


def _fallback(reason: str, *, workflow_id: str) -> NotificationIntelligence:
    slack_context, teams_summary, operator_action = _WORKFLOW_FALLBACKS.get(
        workflow_id,
        _DEFAULT_FALLBACK,
    )
    return NotificationIntelligence(
        slack_context=slack_context,
        teams_summary=teams_summary,
        operator_action=operator_action,
        generation_mode="governed_fallback",
        generator_label="Governed notification framework",
        strategy_summary=reason,
    )


def _prompt(*, monitor_name: str, workflow_id: str, repair_note: str | None) -> str:
    repair = (
        f"\nThe previous JSON failed validation: {repair_note}\nReturn corrected JSON only."
        if repair_note
        else ""
    )
    context = json.dumps({"name": monitor_name, "workflow_id": workflow_id}, sort_keys=True)
    return (
        "You are the operations-communications specialist inside a mortgage growth Supervisor. "
        "Write concise internal framing for a saved watchlist refresh. Slack should be a terse alert; "
        "Teams should be a structured operations summary. Return JSON only with slack_context, "
        "teams_summary, operator_action, and strategy_summary. Do not include counts, URLs, borrower "
        "details, emails, urgency, send instructions, or invented outcomes; the server appends the exact "
        "count and route. Keep each field to one sentence and make Slack and Teams materially different.\n"
        f"Reviewed monitor context: {context}{repair}"
    )


def recommend_notification_intelligence(
    *,
    monitor_name: str,
    workflow_id: str,
    settings: Settings | None = None,
    serving_client: Any | None = None,
) -> NotificationIntelligence:
    settings = settings or get_settings()
    try:
        client = serving_client or workspace_client()
    except Exception:  # noqa: BLE001 - internal notification degrades honestly
        return _fallback("Databricks Agent Responses readiness check failed", workflow_id=workflow_id)
    runtime, reason = verify_supervisor_runtime(client, settings)
    if runtime is None:
        return _fallback(reason or "Databricks Agent Responses unavailable", workflow_id=workflow_id)
    endpoint = runtime.endpoint
    task = runtime.task

    repair_note: str | None = None
    for attempt in range(2):
        prompt = _prompt(
            monitor_name=monitor_name,
            workflow_id=workflow_id,
            repair_note=repair_note,
        )
        try:
            response = query_serving_endpoint(
                client,
                endpoint,
                task=task,
                prompt=prompt,
                max_tokens=500,
                client_request_id=f"mip-notification-{prompt_hash(prompt)[:18]}-{attempt}",
            )
            if not serving_response_has_payload(response):
                raise ValueError("empty Supervisor response")
            parsed = parse_json_object(extract_response_text(response))
            if parsed is None:
                raise ValueError("Supervisor response was not a JSON object")
            fragments = _NotificationFragments.model_validate(parsed)
            strategy = " ".join(str(parsed.get("strategy_summary") or "").split())
            if not 10 <= len(strategy) <= 240 or _UNSAFE_FRAGMENT_RE.search(strategy):
                raise ValueError("strategy summary failed validation")
            try:
                strategy = assert_public_campaign_text(
                    strategy,
                    field_name="notification strategy",
                    max_length=240,
                )
            except ValueError as exc:
                raise ValueError("strategy summary failed validation") from exc
            return NotificationIntelligence(
                slack_context=fragments.slack_context,
                teams_summary=fragments.teams_summary,
                operator_action=fragments.operator_action,
                generation_mode="supervisor",
                generator_label="Databricks Agent Responses",
                strategy_summary=strategy.rstrip("."),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            repair_note = str(exc)[:400]
        except Exception:  # noqa: BLE001 - platform failure uses labelled fallback
            return _fallback("Databricks Agent Responses request failed", workflow_id=workflow_id)
    return _fallback("Databricks Agent Responses output failed validation", workflow_id=workflow_id)
