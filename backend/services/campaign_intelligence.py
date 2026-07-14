"""Evidence-grounded campaign recommendations for Portfolio Builder.

The configured Agent Responses endpoint may propose message strategy and copy, but it never supplies
metrics, source citations, eligibility rules, or approval policy. Those remain
server-derived from the exact governed cohort. A reviewed deterministic
fallback is labelled explicitly when the endpoint is unavailable or its
output fails validation.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import ValidationError

from backend.agents.mortgage_growth_copilot import (
    extract_response_text,
    parse_json_object,
    prompt_hash,
    workspace_client,
)
from backend.config.runtime_secret_policy import (
    require_distinct_rotation_secrets,
    require_strong_runtime_secret,
    runtime_secret_text,
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
from backend.services.scoring import NBO_PRODUCT_LABELS, offer_display_label
from backend.services.supervisor_runtime import verify_supervisor_runtime

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

_CAMPAIGN_PROVENANCE_VERSION = 2
_CAMPAIGN_PROVENANCE_TTL_S = 60 * 60
_LOCAL_TEST_PROVENANCE_SECRET = b"mip-local-test-campaign-provenance-v1"
_LOCAL_TEST_PROVENANCE_APP_ENVS = frozenset({"local", "test"})
_PLACEHOLDER_PROVENANCE_SECRETS = frozenset(
    {
        "redacted",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "example",
        "your-secret",
        "your_secret",
    }
)
_GENERATOR_LABELS: dict[str, str] = {
    "supervisor": "Agent endpoint-generated recommendation",
    "reviewed_fallback": "Reviewed campaign framework",
}


@dataclass(frozen=True)
class CampaignPerformanceContext:
    unique_leads_attempted: int = 0
    unique_contacts_reached: int = 0
    unique_application_starts: int = 0
    unique_applications_submitted: int = 0
    unique_closed_funded: int = 0
    observed_from: date | None = None
    observed_to: date | None = None
    snapshot_at: datetime | None = None
    interval_days: int | None = None
    observation_fingerprint: str | None = None

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
            and self.has_observation_proof
        )

    @property
    def has_observation_proof(self) -> bool:
        if (
            self.observed_from is None
            or self.observed_to is None
            or self.snapshot_at is None
            or self.interval_days is None
            or self.observation_fingerprint is None
        ):
            return False
        if self.snapshot_at.tzinfo is None or self.observed_to < self.observed_from:
            return False
        expected_interval = (self.observed_to - self.observed_from).days + 1
        return self.interval_days == expected_interval and bool(
            re.fullmatch(r"[0-9a-f]{64}", self.observation_fingerprint)
        )

    def prompt_payload(self) -> dict[str, object]:
        if not self.is_qualified:
            raise ValueError("campaign performance is not qualified for model context")
        assert self.observed_from is not None
        assert self.observed_to is not None
        assert self.snapshot_at is not None
        assert self.interval_days is not None
        assert self.observation_fingerprint is not None
        return {
            "observed_from": self.observed_from.isoformat(),
            "observed_to": self.observed_to.isoformat(),
            "snapshot_at": self.snapshot_at.astimezone(UTC).isoformat(),
            "interval_days": self.interval_days,
            "observation_fingerprint": self.observation_fingerprint,
            "unique_leads_attempted": self.unique_leads_attempted,
            "unique_contacts_reached": self.unique_contacts_reached,
            "unique_application_starts": self.unique_application_starts,
            "unique_applications_submitted": self.unique_applications_submitted,
            "unique_closed_funded": self.unique_closed_funded,
        }


@dataclass(frozen=True)
class CampaignVariantProvenanceProof:
    """Non-secret forensic facts recovered from a verified provenance token."""

    generation_mode: Literal["supervisor", "reviewed_fallback"]
    generator_label: str
    key_id: str
    issued_at: int
    expires_at: int
    copy_hash: str
    criteria_fingerprint: str
    performance_fingerprint: str | None
    token_digest: str


def campaign_performance_fingerprint(
    *,
    observed_from: date,
    observed_to: date,
    visible_lo_emails: Iterable[str] | None,
    counts: Mapping[str, object],
) -> str:
    """Return a non-PII server fingerprint for one bounded observation."""

    material = {
        "observed_from": observed_from.isoformat(),
        "observed_to": observed_to.isoformat(),
        "visible_scope_hash": hashlib.sha256(
            (
                "\n".join(sorted(email.strip().lower() for email in (visible_lo_emails or ())))
                if visible_lo_emails is not None
                else "all_visible_loan_officers"
            ).encode("utf-8")
        ).hexdigest(),
        "counts": {
            key: int(str(counts[key]))
            for key in (
                "unique_leads_attempted",
                "unique_contacts_reached",
                "unique_application_starts",
                "unique_applications_submitted",
                "unique_closed_funded",
            )
        },
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def campaign_criteria_fingerprint(criteria: object) -> str:
    """Canonical fingerprint used to prevent provenance-token transplant."""

    model_dump = getattr(criteria, "model_dump", None)
    value = model_dump(mode="json", exclude_none=True) if callable(model_dump) else criteria
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _configured_secret(
    value: object,
    *,
    name: str,
    require_strong: bool,
) -> bytes | None:
    configured = runtime_secret_text(
        value,
        extra_placeholders=_PLACEHOLDER_PROVENANCE_SECRETS,
    )
    if configured is None:
        return None
    if require_strong:
        configured = require_strong_runtime_secret(
            configured,
            name=name,
            extra_placeholders=_PLACEHOLDER_PROVENANCE_SECRETS,
        )
    return configured.encode("utf-8")


def _campaign_provenance_keys(
    settings: Settings,
    *,
    for_issuance: bool = False,
) -> list[bytes]:
    app_env = (settings.app_env or "").strip().lower()
    require_strong = app_env not in _LOCAL_TEST_PROVENANCE_APP_ENVS
    try:
        current = _configured_secret(
            settings.mip_genie_action_secret_current,
            name="MIP_GENIE_ACTION_SECRET_CURRENT",
            require_strong=require_strong,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if current is None:
        if app_env not in _LOCAL_TEST_PROVENANCE_APP_ENVS:
            raise RuntimeError(
                "campaign provenance requires a configured HMAC secret in "
                "MIP_GENIE_ACTION_SECRET_CURRENT outside local/test"
            )
        current = (
            _configured_secret(
                settings.mip_genie_action_secret,
                name="MIP_GENIE_ACTION_SECRET",
                require_strong=False,
            )
            or _LOCAL_TEST_PROVENANCE_SECRET
        )

    keys = [current]
    if not for_issuance:
        try:
            previous = _configured_secret(
                settings.mip_genie_action_secret_previous,
                name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
                require_strong=require_strong,
            )
            require_distinct_rotation_secrets(
                current.decode("utf-8"),
                previous.decode("utf-8") if previous is not None else None,
                current_name="MIP_GENIE_ACTION_SECRET_CURRENT",
                previous_name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if previous is not None:
            keys.append(previous)
    return keys


def _campaign_provenance_keyring(
    settings: Settings,
    *,
    for_issuance: bool = False,
) -> list[tuple[str, bytes]]:
    keys = _campaign_provenance_keys(settings, for_issuance=for_issuance)
    current_kid = (settings.mip_genie_action_secret_kid or "").strip() or "v1"
    keyring = [(current_kid, keys[0])]
    if len(keys) > 1:
        previous_kid = (settings.mip_genie_action_secret_previous_kid or "").strip() or "previous"
        keyring.append((previous_kid, keys[1]))
    return keyring


def _campaign_copy_hash(subject: object, body: object) -> str:
    material = json.dumps(
        {"body": str(body or "").strip(), "subject": str(subject or "").strip()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _encode_provenance_claims(claims: dict[str, object], settings: Settings) -> str:
    body = (
        base64.urlsafe_b64encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = hmac.new(
        _campaign_provenance_keys(settings, for_issuance=True)[0],
        body.encode("ascii"),
        hashlib.sha256,
    )
    return f"{body}.{signature.hexdigest()}"


def issue_campaign_variant_provenance(
    *,
    generation_mode: Literal["supervisor", "reviewed_fallback"],
    generator_label: str,
    subject: str,
    body: str,
    criteria_fingerprint: str,
    performance_fingerprint: str | None = None,
    settings: Settings | None = None,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    active_settings = settings or get_settings()
    key_id = _campaign_provenance_keyring(active_settings, for_issuance=True)[0][0]
    return _encode_provenance_claims(
        {
            "copy_hash": _campaign_copy_hash(subject, body),
            "criteria_fingerprint": criteria_fingerprint,
            "exp": issued_at + _CAMPAIGN_PROVENANCE_TTL_S,
            "generation_mode": generation_mode,
            "generator_label": generator_label,
            "iat": issued_at,
            "kid": key_id,
            "performance_fingerprint": performance_fingerprint or "none",
            "v": _CAMPAIGN_PROVENANCE_VERSION,
        },
        active_settings,
    )


def inspect_campaign_variant_provenance(
    variant: Mapping[str, object],
    *,
    criteria_fingerprint: str,
    settings: Settings | None = None,
    now: int | None = None,
) -> CampaignVariantProvenanceProof | None:
    """Verify a token and return durable, non-secret proof attributes."""

    token = str(variant.get("provenance_token") or "").strip()
    if not token:
        return None
    try:
        body, supplied_signature = token.split(".", 1)
        padded = body + "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        return None
    if not isinstance(claims, dict):
        return None
    active_settings = settings or get_settings()
    claimed_kid = str(claims.get("kid") or "").strip()
    matched_kid: str | None = None
    for key_id, key in _campaign_provenance_keyring(active_settings):
        if claimed_kid and claimed_kid != key_id:
            continue
        expected = hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(supplied_signature, expected):
            matched_kid = key_id
            break
    if matched_kid is None:
        return None
    current_time = int(time.time()) if now is None else now
    try:
        issued_at = int(str(claims.get("iat")))
        expires_at = int(str(claims.get("exp")))
    except (TypeError, ValueError):
        return None
    mode = str(claims.get("generation_mode") or "")
    label = str(claims.get("generator_label") or "")
    copy_hash = str(claims.get("copy_hash") or "")
    signed_criteria = str(claims.get("criteria_fingerprint") or "")
    signed_performance = str(claims.get("performance_fingerprint") or "")
    if (
        claims.get("v") != _CAMPAIGN_PROVENANCE_VERSION
        or issued_at > current_time + 60
        or expires_at < current_time
        or expires_at - issued_at != _CAMPAIGN_PROVENANCE_TTL_S
        or mode not in _GENERATOR_LABELS
        or label != _GENERATOR_LABELS[mode]
        or signed_criteria != criteria_fingerprint
        or (
            signed_performance != "none"
            and re.fullmatch(r"[0-9a-f]{64}", signed_performance) is None
        )
        or copy_hash != _campaign_copy_hash(variant.get("subject"), variant.get("body"))
    ):
        return None
    return CampaignVariantProvenanceProof(
        generation_mode=mode,  # type: ignore[arg-type]
        generator_label=label,
        key_id=matched_kid,
        issued_at=issued_at,
        expires_at=expires_at,
        copy_hash=copy_hash,
        criteria_fingerprint=signed_criteria,
        performance_fingerprint=(None if signed_performance == "none" else signed_performance),
        token_digest=hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )


def verify_campaign_variant_provenance(
    variant: Mapping[str, object],
    *,
    criteria_fingerprint: str,
    settings: Settings | None = None,
    now: int | None = None,
) -> tuple[Literal["supervisor", "reviewed_fallback"], str] | None:
    proof = inspect_campaign_variant_provenance(
        variant,
        criteria_fingerprint=criteria_fingerprint,
        settings=settings,
        now=now,
    )
    if proof is None:
        return None
    return proof.generation_mode, proof.generator_label


def _bind_recommendation_provenance(
    response: CampaignRecommendationResponse,
    *,
    criteria_fingerprint: str,
    performance: CampaignPerformanceContext | None,
    settings: Settings,
) -> CampaignRecommendationResponse:
    performance_fingerprint = (
        performance.observation_fingerprint
        if performance is not None and performance.is_qualified
        else None
    )
    variants = [
        variant.model_copy(
            update={
                "provenance_token": issue_campaign_variant_provenance(
                    generation_mode=response.generation_mode,
                    generator_label=response.generator_label,
                    subject=variant.subject,
                    body=variant.body,
                    criteria_fingerprint=criteria_fingerprint,
                    performance_fingerprint=performance_fingerprint,
                    settings=settings,
                )
            }
        )
        for variant in response.variants
    ]
    return response.model_copy(update={"variants": variants})


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
        assert performance.observed_from is not None
        assert performance.observed_to is not None
        assert performance.snapshot_at is not None
        assert performance.interval_days is not None
        assert performance.observation_fingerprint is not None
        rows.append(
            CampaignRecommendationEvidence(
                label="Qualified performance observation",
                value=(
                    f"{performance.observed_from.isoformat()} to {performance.observed_to.isoformat()} / "
                    f"{performance.interval_days} days / "
                    f"snapshot {performance.snapshot_at.astimezone(UTC).date().isoformat()} / "
                    f"fp {performance.observation_fingerprint[:12]}"
                ),
                source_asset="mip_app.call_dispositions",
            )
        )
        rows.append(
            CampaignRecommendationEvidence(
                label="Same-borrower attempted, reached, and application start",
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
                label="Same-borrower application starts, submitted, and funded",
                value=(
                    f"{performance.unique_application_starts:,} starts / "
                    f"{performance.unique_applications_submitted:,} submitted / "
                    f"{performance.unique_closed_funded:,} funded"
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
    criteria_fingerprint: str,
    settings: Settings,
) -> CampaignRecommendationResponse:
    offer_code, _offer_count = _dominant_offer(preview)
    offer_label = offer_display_label(offer_code, NBO_PRODUCT_LABELS[offer_code]).lower()
    audience = _OFFER_AUDIENCE[offer_code]
    lender = lender_name.strip() or "your lender"
    response = CampaignRecommendationResponse(
        generation_mode="reviewed_fallback",
        generator_label=_GENERATOR_LABELS["reviewed_fallback"],
        performance_status=_performance_status(performance),
        audience_summary=(
            f"The selected audience is led by {audience} and is ready for a controlled message test."
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
    return _bind_recommendation_provenance(
        response,
        criteria_fingerprint=criteria_fingerprint,
        performance=performance,
        settings=settings,
    )


def _prompt(
    preview: PortfolioPreview,
    *,
    lender_name: str,
    performance: CampaignPerformanceContext | None,
    repair_note: str | None,
) -> str:
    offer_code, offer_count = _dominant_offer(preview)
    payload: dict[str, object] = {
        "eligible_borrowers": preview.marketable_population,
        "refi_economics_borrowers": preview.high_intent_leads,
        "average_current_lien_balance_usd": preview.avg_current_lien_balance_usd,
        "average_modeled_equity_pct": preview.avg_equity_pct,
        "average_rate_spread_bps": preview.avg_rate_spread_bps,
        "dominant_offer_code": offer_code,
        "dominant_offer_borrowers": offer_count,
        "lender_label": lender_name,
    }
    if performance is not None and performance.is_qualified:
        payload["qualified_observed_performance"] = performance.prompt_payload()
    repair = (
        f"\nYour previous JSON failed validation: {repair_note}\nReturn corrected JSON only."
        if repair_note
        else ""
    )
    return (
        "You are the campaign-strategy specialist inside a governed mortgage growth workflow. "
        "Use only the aggregate cohort facts below. Create two genuinely distinct email tests: one "
        "benefit-led and one guidance-led. Use plain language, one low-friction call to action, and no "
        "guaranteed savings, quoted rates, false urgency, protected traits, personal data, placeholders, "
        "or unsupported claims. Observed performance is strategy context only. Do not put any numeric "
        "claim, count, percentage, rate, currency amount, or performance metric in audience_summary, "
        "strategy, or borrower-facing copy; exact figures are rendered separately from governed evidence. "
        "The hypotheses must say what "
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
    criteria_fingerprint: str | None = None,
) -> CampaignRecommendationResponse:
    settings = settings or get_settings()
    criteria_fingerprint = criteria_fingerprint or campaign_criteria_fingerprint({})
    lender_name = settings.mip_lender_name
    try:
        client = serving_client or workspace_client()
    except Exception:  # noqa: BLE001 - recommendation degrades honestly
        return _fallback(
            preview,
            lender_name=lender_name,
            performance=performance,
            warning="Agent endpoint readiness check failed",
            criteria_fingerprint=criteria_fingerprint,
            settings=settings,
        )
    runtime, reason = verify_supervisor_runtime(client, settings)
    if runtime is None:
        return _fallback(
            preview,
            lender_name=lender_name,
            performance=performance,
            warning=reason or "Supervisor unavailable",
            criteria_fingerprint=criteria_fingerprint,
            settings=settings,
        )
    endpoint = runtime.endpoint
    task = runtime.task

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
                raise ValueError("empty Agent Responses output")
            parsed = parse_json_object(extract_response_text(response))
            if parsed is None:
                raise ValueError("Agent Responses output was not a JSON object")
            candidate = CampaignRecommendationResponse.model_validate(
                {
                    "generation_mode": "supervisor",
                    "generator_label": _GENERATOR_LABELS["supervisor"],
                    "performance_status": _performance_status(performance),
                    "audience_summary": parsed.get("audience_summary"),
                    "strategy": parsed.get("strategy"),
                    "variants": parsed.get("variants"),
                    "holdout_pct": parsed.get("holdout_pct", 10),
                    "evidence": [item.model_dump() for item in evidence],
                    "warnings": [],
                }
            )
            return _bind_recommendation_provenance(
                candidate,
                criteria_fingerprint=criteria_fingerprint,
                performance=performance,
                settings=settings,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            repair_note = str(exc)[:500]
        except Exception:  # noqa: BLE001 - network/platform failure uses labelled fallback
            return _fallback(
                preview,
                lender_name=lender_name,
                performance=performance,
                warning="Agent endpoint request failed",
                criteria_fingerprint=criteria_fingerprint,
                settings=settings,
            )
    return _fallback(
        preview,
        lender_name=lender_name,
        performance=performance,
        warning="Agent endpoint output failed validation",
        criteria_fingerprint=criteria_fingerprint,
        settings=settings,
    )
