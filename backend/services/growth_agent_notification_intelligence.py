"""Supervisor-assisted internal notifications for saved Growth Agent monitors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.agents.mortgage_growth_copilot import (
    agent_task_if_ready,
    extract_response_text,
    parse_json_object,
    prompt_hash,
    workspace_client,
)
from backend.config.settings import Settings, get_settings
from backend.schemas._validators import contains_contextual_human_name
from backend.schemas.portfolio_campaign import assert_public_campaign_text
from backend.services.capability_serving_probes import (
    query_serving_endpoint,
    serving_response_has_payload,
)
from backend.services.growth_agent_composer import composition_endpoint

NotificationGenerationMode = Literal["supervisor", "governed_fallback"]
_UNSAFE_FRAGMENT_RE = re.compile(
    r"(?:https?://|/lead-queue|\b\d+(?:[.,]\d+)*\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"\b(?:guarantee|urgent|act now|send now|customer name|borrower name|"
    r"increase(?:d)?|decrease(?:d)?|improve(?:d)?|decline(?:d)?|convert(?:ed)?|"
    r"funded|submitted|application|response rate|conversion|performance|"
    r"record high|record low)\b)",
    re.IGNORECASE,
)


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
            raise ValueError("notification fragment contains unsafe targeting or identity text") from exc
        if contains_contextual_human_name(clean):
            raise ValueError("notification fragment contains identity-shaped text")
        return clean.rstrip(".")


@dataclass(frozen=True)
class NotificationIntelligence:
    slack_context: str
    teams_summary: str
    operator_action: str
    generation_mode: NotificationGenerationMode
    generator_label: str
    strategy_summary: str


def _fallback(reason: str) -> NotificationIntelligence:
    return NotificationIntelligence(
        slack_context="The saved watchlist has refreshed and is ready for review",
        teams_summary="The saved watchlist refresh completed with a new eligible population",
        operator_action="Review the ranked queue and confirm the next operating step",
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
    endpoint, reason = composition_endpoint(settings)
    if endpoint is None:
        return _fallback(reason or "Supervisor unavailable")
    try:
        client = serving_client or workspace_client()
        task = agent_task_if_ready(client, endpoint)
    except Exception:  # noqa: BLE001 - internal notification degrades honestly
        return _fallback("Supervisor readiness check failed")
    if task is None:
        return _fallback("Supervisor is not ready")

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
                generator_label="Supervisor-composed notification",
                strategy_summary=strategy.rstrip("."),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            repair_note = str(exc)[:400]
        except Exception:  # noqa: BLE001 - platform failure uses labelled fallback
            return _fallback("Supervisor request failed")
    return _fallback("Supervisor output failed validation")
