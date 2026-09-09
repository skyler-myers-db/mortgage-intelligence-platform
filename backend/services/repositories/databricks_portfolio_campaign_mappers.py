"""Campaign row mappers for the Databricks portfolio repository: the public
variant projection, governed name/JSON projections, and CampaignSummary shaping."""

from __future__ import annotations

import json
import logging
import math
from typing import Any, cast

from backend.schemas.portfolio import (
    CampaignPublicJsonField,
    CampaignSummary,
    project_public_campaign_json_field,
    project_public_campaign_name,
)
from backend.schemas.portfolio_campaign import (
    assert_borrower_campaign_copy,
    assert_public_campaign_json,
    assert_public_campaign_text,
)
from backend.services.campaign_intelligence import (
    campaign_criteria_fingerprint,
    durable_campaign_variant_copy_verified,
)
from backend.services.observability import emit
from backend.services.repositories.databricks_portfolio_predicates import (
    coerce_utc_datetime,
    household_dedup_config_from_value,
    household_dedup_summary_from_value,
    json_value,
)

# Bound by explicit name rather than ``__name__``: these projections emitted
# structured records under the ``...databricks_portfolio`` logger before the
# split, and importing ``log`` from that module would be an import cycle
# (databricks_portfolio imports this module for its re-exports).
log = logging.getLogger("backend.services.repositories.databricks_portfolio")

_NORMALIZED_CAMPAIGN_VARIANTS_SQL = """
(
  SELECT jsonb_agg(
    jsonb_build_object(
      'variant_name', variant.variant_name,
      'channel', variant.channel,
      'subject', variant.subject,
      'body', variant.body,
      'weight_pct', variant.weight_pct,
      'generation_mode', variant.generation_mode,
      'generator_label', variant.generator_label,
      'provenance_key_id', variant.provenance_key_id,
      'provenance_issued_at', variant.provenance_issued_at,
      'provenance_expires_at', variant.provenance_expires_at,
      'provenance_copy_hash', variant.provenance_copy_hash,
      'provenance_criteria_fingerprint', variant.provenance_criteria_fingerprint,
      'provenance_performance_fingerprint', variant.provenance_performance_fingerprint,
      'provenance_token_digest', variant.provenance_token_digest
    )
    ORDER BY variant.variant_name, variant.channel
  )
  FROM mip_app.campaign_message_variants AS variant
  WHERE variant.campaign_id = {campaign_id_ref}
) AS normalized_message_variants
"""

_PUBLIC_CAMPAIGN_VARIANT_FIELDS = frozenset(
    {
        "variant_name",
        "channel",
        "subject",
        "body",
        "weight_pct",
        "generation_mode",
        "generator_label",
        "copy_verified_at_creation",
    }
)


def _public_campaign_variant(
    variant: dict[str, Any],
    *,
    criteria_fingerprint: str,
    allow_durable_verification: bool,
) -> dict[str, object] | None:
    try:
        if not allow_durable_verification:
            for key, item in variant.items():
                if key not in _PUBLIC_CAMPAIGN_VARIANT_FIELDS:
                    assert_public_campaign_json(
                        item,
                        field_name="legacy campaign variant metadata",
                    )
        if not isinstance(variant.get("variant_name"), str):
            raise ValueError("campaign variant_name must be text")
        variant_name = assert_public_campaign_text(
            variant.get("variant_name"), field_name="variant_name", max_length=64
        )
        if not variant_name:
            raise ValueError("campaign variant_name must be nonblank")
        if not isinstance(variant.get("channel"), str):
            raise ValueError("campaign variant channel must be text")
        channel = str(variant.get("channel") or "").strip()
        if channel not in {"email", "sms", "direct_mail"}:
            raise ValueError("unsupported campaign channel")
        subject_raw = variant.get("subject")
        if subject_raw is not None and not isinstance(subject_raw, str):
            raise ValueError("campaign variant subject must be text or null")
        subject = (
            assert_borrower_campaign_copy(
                assert_public_campaign_text(
                    subject_raw, field_name="variant subject", max_length=120
                ),
                field_name="variant subject",
            )
            if subject_raw is not None
            else None
        )
        if channel == "email" and not subject:
            raise ValueError("email campaign variants require a subject")
        if not isinstance(variant.get("body"), str):
            raise ValueError("campaign variant body must be text")
        body = assert_borrower_campaign_copy(
            assert_public_campaign_text(
                variant.get("body"), field_name="variant body", max_length=1000
            ),
            field_name="variant body",
        )
        if not body:
            raise ValueError("campaign variant body must be nonblank")
        generation_mode_raw = variant.get("generation_mode")
        if generation_mode_raw is not None and not isinstance(generation_mode_raw, str):
            raise ValueError("campaign variant generation_mode must be text")
        generation_mode = str(generation_mode_raw or "").strip()
        if generation_mode not in {"supervisor", "reviewed_fallback"}:
            raise ValueError("borrower campaign copy is not server-reviewed")
        generator_label_raw = variant.get("generator_label")
        if generator_label_raw is not None and not isinstance(generator_label_raw, str):
            raise ValueError("campaign variant generator_label must be text")
        generator_label = assert_public_campaign_text(
            generator_label_raw or "",
            field_name="variant generator_label",
            max_length=80,
        )
        weight_pct_raw = variant.get("weight_pct")
        if isinstance(weight_pct_raw, bool):
            raise ValueError("invalid campaign weight")
        weight_value = float(weight_pct_raw) if weight_pct_raw is not None else None
        if weight_value is not None and (
            not math.isfinite(weight_value) or not 0 <= weight_value <= 100
        ):
            raise ValueError("invalid campaign weight")
        weight_pct = (
            int(weight_value)
            if weight_value is not None and weight_value.is_integer()
            else weight_value
        )
    except (TypeError, ValueError):
        emit(
            log,
            "campaign_variant_public_projection_rejected",
            level=logging.WARNING,
            outcome="omitted",
            reason="invalid_public_payload",
        )
        return None

    copy_verified_at_creation = bool(
        allow_durable_verification
        and durable_campaign_variant_copy_verified(
            variant,
            criteria_fingerprint=criteria_fingerprint,
        )
    )
    if not copy_verified_at_creation:
        emit(
            log,
            "campaign_variant_public_projection_rejected",
            level=logging.WARNING,
            outcome="omitted",
            reason="unverified_borrower_copy",
        )
        return None
    public_variant: dict[str, object] = {
        "variant_name": variant_name,
        "channel": channel,
        "subject": subject,
        "body": body,
        "weight_pct": weight_pct,
        "generation_mode": generation_mode,
        "generator_label": generator_label,
        "copy_verified_at_creation": copy_verified_at_creation,
    }
    if public_variant.keys() != _PUBLIC_CAMPAIGN_VARIANT_FIELDS:
        raise AssertionError("public campaign variant fields do not match the allowlist")
    return public_variant


def _project_campaign_json_or_default(
    raw_value: Any,
    *,
    field_name: CampaignPublicJsonField,
    fallback: dict[str, object] | list[dict[str, object]] | None,
) -> dict[str, object] | list[dict[str, object]] | None:
    return _project_campaign_json_with_status(
        raw_value,
        field_name=field_name,
        fallback=fallback,
    )[0]


def _project_campaign_json_with_status(
    raw_value: Any,
    *,
    field_name: CampaignPublicJsonField,
    fallback: dict[str, object] | list[dict[str, object]] | None,
) -> tuple[dict[str, object] | list[dict[str, object]] | None, bool]:
    try:
        value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        return project_public_campaign_json_field(field_name, value), True
    except (TypeError, ValueError):
        emit(
            log,
            "campaign_json_public_projection_rejected",
            level=logging.WARNING,
            outcome="omitted",
            field=field_name,
            reason="invalid_public_payload",
        )
        return fallback, False


def _project_campaign_name_or_default(raw_value: Any) -> str:
    return _project_campaign_name_with_status(raw_value)[0]


def _project_campaign_name_with_status(raw_value: Any) -> tuple[str, bool]:
    try:
        return project_public_campaign_name(raw_value), True
    except (TypeError, ValueError):
        emit(
            log,
            "campaign_name_public_projection_rejected",
            level=logging.WARNING,
            outcome="replaced",
            reason="invalid_public_name",
        )
        return "Campaign unavailable", False


def campaign_summary_from_row(row: dict[str, Any]) -> CampaignSummary:
    criteria_projected, criteria_valid = _project_campaign_json_with_status(
        row.get("criteria"),
        field_name="criteria",
        fallback={},
    )
    criteria_value = cast(
        dict[str, object],
        criteria_projected,
    )
    suppression_projected, suppression_valid = _project_campaign_json_with_status(
        row.get("suppression_policy"),
        field_name="suppression_policy",
        fallback={},
    )
    suppression_policy = cast(
        dict[str, object],
        suppression_projected,
    )
    normalized_raw = row.get("normalized_message_variants")
    relational_variants_are_authoritative = normalized_raw is not None
    variants_raw = (
        normalized_raw
        if relational_variants_are_authoritative
        else row.get("legacy_message_variants", row.get("message_variants"))
    )
    variants_valid = True
    try:
        variants_value = json.loads(variants_raw) if isinstance(variants_raw, str) else variants_raw
    except (TypeError, ValueError):
        variants_value = []
        variants_valid = False
    criteria_fingerprint = campaign_criteria_fingerprint(criteria_value)
    message_variants: list[dict[str, object]] = []
    if isinstance(variants_value, list):
        for variant in variants_value:
            if not isinstance(variant, dict):
                variants_valid = False
                continue
            public_variant = _public_campaign_variant(
                variant,
                criteria_fingerprint=criteria_fingerprint,
                allow_durable_verification=relational_variants_are_authoritative,
            )
            if public_variant is not None:
                message_variants.append(public_variant)
            else:
                variants_valid = False
    elif variants_value is not None:
        variants_valid = False
    channel_projected, channel_valid = _project_campaign_json_with_status(
        row.get("channel_cascade"),
        field_name="channel_cascade",
        fallback=[],
    )
    channel_cascade = cast(
        list[dict[str, object]],
        channel_projected,
    )
    send_projected, send_valid = _project_campaign_json_with_status(
        row.get("send_window"),
        field_name="send_window",
        fallback={},
    )
    send_window = cast(
        dict[str, object],
        send_projected,
    )
    holdout_projected, holdout_valid = _project_campaign_json_with_status(
        row.get("holdout"),
        field_name="holdout",
        fallback=None,
    )
    holdout = cast(
        dict[str, object] | None,
        holdout_projected,
    )
    roi_projected, roi_valid = _project_campaign_json_with_status(
        row.get("roi_assumptions"),
        field_name="roi_assumptions",
        fallback=None,
    )
    roi_assumptions = cast(
        dict[str, object] | None,
        roi_projected,
    )
    campaign_name, name_valid = _project_campaign_name_with_status(row.get("name"))
    contract_raw = row.get("json_contract_version")
    try:
        current_contract = contract_raw is not None and int(contract_raw) == 1
    except (TypeError, ValueError):
        current_contract = False
    issue: str | None = None
    if not current_contract:
        issue = "legacy_contract"
    elif not name_valid:
        issue = "invalid_name"
    elif not criteria_valid:
        issue = "invalid_criteria"
    elif not suppression_valid:
        issue = "invalid_policy"
    elif not all((channel_valid, send_valid, holdout_valid, roi_valid)):
        issue = "invalid_configuration"
    elif not variants_valid:
        issue = "invalid_message_variants"
    elif str(row.get("treatment_state") or "legacy_unbound") != "ready":
        issue = "treatment_unbound"
    household_dedup = json_value(row.get("household_dedup"), {})
    household_summary = json_value(row.get("household_summary"), {})
    return CampaignSummary(
        campaign_id=str(row.get("campaign_id")),
        name=campaign_name,
        owner_email=str(row.get("owner_email") or "unknown"),
        status=str(row.get("status") or "draft"),  # type: ignore[arg-type]
        treatment_state=str(row.get("treatment_state") or "legacy_unbound"),  # type: ignore[arg-type]
        actionable=issue is None,
        actionability_issue=issue,  # type: ignore[arg-type]
        criteria=criteria_value,
        suppression_policy=suppression_policy,
        message_variants=message_variants,
        channel_cascade=channel_cascade,
        send_window=send_window,
        holdout=holdout,
        roi_assumptions=roi_assumptions,
        household_dedup=household_dedup_config_from_value(household_dedup),
        household_summary=household_dedup_summary_from_value(household_summary),
        created_at=coerce_utc_datetime(row.get("created_at")),
        updated_at=coerce_utc_datetime(row.get("updated_at")),
    )
