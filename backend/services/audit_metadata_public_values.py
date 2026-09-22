"""Public-value assertion for every allowlisted audit metadata key.

Single responsibility: one function, ``_assert_public_safe_values``, which
walks an allowlisted metadata payload key by key and proves each value is
safe to persist in the append-only ledger -- reviewed vocabulary, bounded
numbers, masked identifiers, public labels, and no borrower identity.

It is a single reviewed switch rather than many small validators so the
per-key policy stays readable in one place; it is its own module because
it is the largest single unit in the audit metadata chain.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from backend.schemas.common import (
    contains_pii_marker,
    validate_internal_staff_email,
    validate_public_audit_identifier_or_none,
    validate_public_borrower_id,
    validate_public_campaign_label,
    validate_public_opaque_id,
)
from backend.schemas.genie_numeric_filters import (
    is_reviewed_numeric_floor,
    numeric_filter_range_text,
)
from backend.services.audit_metadata_policy import (
    _ACTIVATION_DESTINATION_KEY_PATTERN,
    _ACTIVATION_DESTINATION_TYPES,
    _ACTIVATION_STATUSES,
    _ALLOWED_OFFER_CODES,
    _ASSIGNMENT_LIFECYCLE_STATUSES,
    _ASSIGNMENT_OUTCOMES,
    _BORROWER_ID_LIST_METADATA_KEYS,
    _BORROWER_ID_METADATA_KEYS,
    _CAMPAIGN_LABEL_METADATA_KEYS,
    _FORCED_DEGRADED_DEPENDENCIES,
    _GOVERNED_REFUSAL_REASONS,
    _GROWTH_AGENT_RUN_STATUSES,
    _GROWTH_AGENT_SPECIALISTS,
    _GROWTH_AGENT_TITLES,
    _GROWTH_AGENT_TOOL_NAMES,
    _GROWTH_AGENT_WORKFLOWS,
    _HOUSEHOLD_DEDUPE_UNITS,
    _HOUSEHOLD_PRIMARY_STRATEGIES,
    _INTERNAL_STAFF_EMAIL_LIST_METADATA_KEYS,
    _INTERNAL_STAFF_EMAIL_METADATA_KEYS,
    _LEAD_OUTCOME_SOURCE_SYSTEMS,
    _LEAD_OUTCOME_TYPES,
    _OPAQUE_ID_METADATA_KEYS,
    _OUTREACH_DRAFT_ATTRIBUTIONS,
    _OUTREACH_GENERATION_MODES,
    _PUBLIC_COMPETITOR_LABEL_PATTERN,
    _RESULT_FILTER_NUMERIC_BOUNDS,
    _SALES_DISPOSITION_OUTCOMES,
    _SALES_STRATEGIES,
    AuditMetadataValueViolation,
    _metadata_values_for,
)
from backend.services.audit_metadata_validation import (
    _assert_decision_inputs_value_policy,
    _assert_portfolio_criteria_value_policy,
    _assert_result_filters_value_policy,
    _growth_agent_reviewed_text_contains_pii,
)
from backend.services.audit_metadata_value_policy import (
    validate_row_count,
    validate_source_assets,
    validate_sql_hash,
)
from backend.services.pii_redaction import normalize_public_lender_ref


def _assert_public_safe_values(metadata: dict[str, Any]) -> None:
    """Validate reviewed free-ish values that have their own public policy."""
    if not metadata:
        return
    for field, rows in _metadata_values_for(metadata, {"variant_provenance"}):
        if not isinstance(rows, list) or len(rows) > 12:
            raise AuditMetadataValueViolation(field, "must be a bounded provenance list")
        allowed_keys = {
            "variant_name",
            "generation_mode",
            "generator_label",
            "provenance_key_id",
            "provenance_issued_at",
            "provenance_expires_at",
            "provenance_copy_hash",
            "provenance_criteria_fingerprint",
            "provenance_performance_fingerprint",
        }
        for row in rows:
            if not isinstance(row, dict) or set(row) - allowed_keys:
                raise AuditMetadataValueViolation(field, "contains an invalid provenance object")
            try:
                validate_public_campaign_label(
                    str(row.get("variant_name") or ""),
                    field_name="variant_name",
                )
            except ValueError as exc:
                raise AuditMetadataValueViolation(
                    field,
                    "contains an invalid public provenance label",
                ) from exc
            generation_mode = str(row.get("generation_mode") or "")
            reviewed_generator_labels = {
                "supervisor": "Databricks Agent Responses",
                "reviewed_fallback": "Reviewed campaign framework",
                "operator": "Operator edited",
            }
            if generation_mode not in reviewed_generator_labels:
                raise AuditMetadataValueViolation(field, "contains an invalid generation mode")
            if row.get("generator_label") != reviewed_generator_labels[generation_mode]:
                raise AuditMetadataValueViolation(
                    field,
                    "contains a generator label that does not match its generation mode",
                )
            proof_fields = allowed_keys - {
                "variant_name",
                "generation_mode",
                "generator_label",
            }
            present_proof_fields = proof_fields.intersection(row)
            if present_proof_fields and present_proof_fields != proof_fields:
                raise AuditMetadataValueViolation(field, "contains a partial provenance proof")
            has_proof = present_proof_fields == proof_fields
            if not has_proof:
                continue
            if row.get("provenance_key_id") is None:
                raise AuditMetadataValueViolation(field, "contains a partial provenance proof")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(row["provenance_key_id"])):
                raise AuditMetadataValueViolation(field, "contains an invalid provenance key id")
            for timestamp_key in ("provenance_issued_at", "provenance_expires_at"):
                try:
                    parsed = datetime.fromisoformat(str(row[timestamp_key]).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise AuditMetadataValueViolation(
                        field,
                        "contains an invalid provenance timestamp",
                    ) from exc
                if parsed.tzinfo is None:
                    raise AuditMetadataValueViolation(
                        field,
                        "contains a timezone-naive provenance timestamp",
                    )
            for hash_key in (
                "provenance_copy_hash",
                "provenance_criteria_fingerprint",
            ):
                if re.fullmatch(r"[0-9a-f]{64}", str(row[hash_key])) is None:
                    raise AuditMetadataValueViolation(field, "contains an invalid provenance hash")
            performance_hash = row["provenance_performance_fingerprint"]
            if (
                performance_hash is not None
                and re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(performance_hash),
                )
                is None
            ):
                raise AuditMetadataValueViolation(
                    field,
                    "contains an invalid performance provenance hash",
                )
    for field, value in _metadata_values_for(metadata, _BORROWER_ID_METADATA_KEYS):
        if value is None:
            continue
        try:
            validate_public_borrower_id(str(value))
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field,
                "must be an app-scoped public borrower id",
            ) from exc

    for field, offer_code in _metadata_values_for(metadata, {"offer_code"}):
        if offer_code is not None and str(offer_code) not in _ALLOWED_OFFER_CODES:
            raise AuditMetadataValueViolation(
                field,
                "must be a governed offer code",
            )
    for field, generation_mode in _metadata_values_for(metadata, {"generation_mode"}):
        if generation_mode is not None and str(generation_mode) not in _OUTREACH_GENERATION_MODES:
            raise AuditMetadataValueViolation(
                field,
                "must be a reviewed outreach generation mode",
            )
    for field, attribution in _metadata_values_for(metadata, {"draft_attribution"}):
        if attribution is not None and str(attribution) not in _OUTREACH_DRAFT_ATTRIBUTIONS:
            raise AuditMetadataValueViolation(
                field,
                "must be a reviewed generated-draft attribution",
            )
    for field, edited in _metadata_values_for(metadata, {"draft_edited"}):
        if not isinstance(edited, bool):
            raise AuditMetadataValueViolation(field, "must be boolean")
    for field, value in _metadata_values_for(
        metadata,
        {"draft_response_hash", "campaign_treatment_fingerprint"},
    ):
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", str(value)) is None:
            raise AuditMetadataValueViolation(field, "must be a SHA-256 hex digest")
    for field, value in _metadata_values_for(metadata, {"draft_source_refreshed_at"}):
        if value is None:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field,
                "must be an ISO-8601 timestamp",
            ) from exc
        if parsed.tzinfo is None:
            raise AuditMetadataValueViolation(
                field,
                "must include a timezone",
            )
    for field, generation_mode in _metadata_values_for(metadata, {"campaign_generation_mode"}):
        if generation_mode is not None and str(generation_mode) not in {
            "supervisor",
            "reviewed_fallback",
            "operator",
            "mixed",
        }:
            raise AuditMetadataValueViolation(
                field,
                "must be a reviewed campaign generation mode",
            )
    for field, value in _metadata_values_for(metadata, _OPAQUE_ID_METADATA_KEYS):
        if value is None:
            continue
        try:
            validate_public_opaque_id(str(value))
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field,
                "must be a UUID or governed server-issued opaque id",
            ) from exc
    for field, value in _metadata_values_for(metadata, _CAMPAIGN_LABEL_METADATA_KEYS):
        if value is None:
            continue
        try:
            validate_public_campaign_label(str(value), field_name=field)
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field,
                "must be a public-safe campaign label",
            ) from exc
    for field, value in _metadata_values_for(metadata, _INTERNAL_STAFF_EMAIL_METADATA_KEYS):
        if value is None:
            continue
        try:
            validate_internal_staff_email(str(value))
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field,
                "must be an approved internal staff email",
            ) from exc
    for field, value in _metadata_values_for(metadata, _INTERNAL_STAFF_EMAIL_LIST_METADATA_KEYS):
        if value is None:
            continue
        if not isinstance(value, list):
            raise AuditMetadataValueViolation(field, "must be a list of internal staff emails")
        for item in value:
            try:
                validate_internal_staff_email(str(item))
            except ValueError as exc:
                raise AuditMetadataValueViolation(
                    field,
                    "must contain only approved internal staff emails",
                ) from exc
    for field, value in _metadata_values_for(metadata, _BORROWER_ID_LIST_METADATA_KEYS):
        if value is None:
            continue
        if not isinstance(value, list):
            raise AuditMetadataValueViolation(
                field,
                "must be a list of app-scoped public borrower ids",
            )
        for item in value:
            try:
                validate_public_borrower_id(str(item))
            except ValueError as exc:
                raise AuditMetadataValueViolation(
                    field,
                    "must contain only app-scoped public borrower ids",
                ) from exc
    for field, target in _metadata_values_for(metadata, {"target_lender_ref"}):
        if target is None:
            continue
        try:
            normalize_public_lender_ref(str(target), allow_all=True)
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field,
                "must be the configured tenant lender, Competitor A-Z, Competitor Other, or All",
            ) from exc
    for field, allowed in {
        "forced_dependency": _FORCED_DEGRADED_DEPENDENCIES,
        "forced_state": {"on", "off"},
        "proof_scope": {"browser_cookie"},
        "dedupe_unit": _HOUSEHOLD_DEDUPE_UNITS,
        "household_primary_strategy": _HOUSEHOLD_PRIMARY_STRATEGIES,
    }.items():
        for _, value in _metadata_values_for(metadata, {field}):
            if value is not None and str(value) not in allowed:
                raise AuditMetadataValueViolation(
                    field, "contains values outside the reviewed vocabulary"
                )
    for numeric_field in _RESULT_FILTER_NUMERIC_BOUNDS:
        # Same bounds whether the floor is recorded at the top level of a
        # VIEW_LEADS row or nested in a Genie cohort's result_filters.
        for field, floor in _metadata_values_for(metadata, {numeric_field}):
            if floor is None:
                continue
            if not is_reviewed_numeric_floor(numeric_field, floor):
                raise AuditMetadataValueViolation(
                    field,
                    f"must be an integer {numeric_filter_range_text(numeric_field)}",
                )
    for field, ttl_s in _metadata_values_for(metadata, {"ttl_s"}):
        if not isinstance(ttl_s, int) or isinstance(ttl_s, bool) or ttl_s < 0 or ttl_s > 300:
            raise AuditMetadataValueViolation(field, "must be an integer between 0 and 300")
    for field, seconds in _metadata_values_for(metadata, {"cooldown_seconds"}):
        if (
            not isinstance(seconds, int)
            or isinstance(seconds, bool)
            or seconds < 0
            or seconds > 7200
        ):
            raise AuditMetadataValueViolation(field, "must be an integer between 0 and 7200")
    for _, portfolio_criteria in _metadata_values_for(metadata, {"portfolio_criteria"}):
        if portfolio_criteria is None:
            continue
        _assert_portfolio_criteria_value_policy(portfolio_criteria)
    for _, result_filters in _metadata_values_for(metadata, {"result_filters"}):
        if result_filters is None:
            continue
        _assert_result_filters_value_policy(result_filters)
    for _, decision_inputs in _metadata_values_for(metadata, {"decision_inputs"}):
        if decision_inputs is None:
            continue
        _assert_decision_inputs_value_policy(decision_inputs)
    for field, value in _metadata_values_for(metadata, {"strategy"}):
        if value is not None and str(value) not in _SALES_STRATEGIES:
            raise AuditMetadataValueViolation(field, "must be a governed sales assignment strategy")
    for field, value in _metadata_values_for(metadata, {"outcome"}):
        if value is not None and str(value) not in _SALES_DISPOSITION_OUTCOMES:
            raise AuditMetadataValueViolation(field, "must be a governed call disposition outcome")
    for field, value in _metadata_values_for(metadata, {"assignment_outcome"}):
        if value is not None and str(value) not in _ASSIGNMENT_OUTCOMES:
            raise AuditMetadataValueViolation(field, "must be a governed assignment outcome")
    for field, value in _metadata_values_for(metadata, {"feedback_id"}):
        if value is None:
            continue
        try:
            validate_public_audit_identifier_or_none(str(value))
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field, "must be a public-safe feedback identifier"
            ) from exc
    for field, value in _metadata_values_for(metadata, {"from_status", "to_status"}):
        if value is not None and str(value) not in _ASSIGNMENT_LIFECYCLE_STATUSES:
            raise AuditMetadataValueViolation(
                field, "must be a governed assignment lifecycle status"
            )
    for field, value in _metadata_values_for(metadata, {"lead_outcome_type"}):
        if value is not None and str(value) not in _LEAD_OUTCOME_TYPES:
            raise AuditMetadataValueViolation(field, "must be a governed lead outcome type")
    for field, value in _metadata_values_for(metadata, {"source_system"}):
        if value is not None and str(value) not in _LEAD_OUTCOME_SOURCE_SYSTEMS:
            raise AuditMetadataValueViolation(field, "must be a governed outcome source system")
    for field, value in _metadata_values_for(metadata, {"source_record_ref"}):
        if value is None:
            continue
        try:
            validate_public_audit_identifier_or_none(str(value))
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field, "must be a public-safe source record reference"
            ) from exc
    for field, value in _metadata_values_for(metadata, {"loan_amount"}):
        if value is None:
            continue
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > 100_000_000
        ):
            raise AuditMetadataValueViolation(field, "must be a bounded non-negative integer")
    for field, value in _metadata_values_for(metadata, {"competitor_lender_label"}):
        if value is None:
            continue
        text = str(value)
        if contains_pii_marker(text):
            raise AuditMetadataValueViolation(field, "must be a governed competitor alias")
        try:
            normalized = normalize_public_lender_ref(text)
        except ValueError as exc:
            raise AuditMetadataValueViolation(field, "must be a governed competitor alias") from exc
        if normalized is None or not _PUBLIC_COMPETITOR_LABEL_PATTERN.fullmatch(normalized):
            raise AuditMetadataValueViolation(field, "must be a governed competitor alias")
    for field, value in _metadata_values_for(metadata, {"refusal_reason"}):
        if value is not None and str(value) not in _GOVERNED_REFUSAL_REASONS:
            raise AuditMetadataValueViolation(field, "must be a governed refusal reason")
    for field, value in _metadata_values_for(
        metadata,
        {"helpful", "comment_present", "hit", "household_dedup_enabled"},
    ):
        if value is not None and not isinstance(value, bool):
            raise AuditMetadataValueViolation(field, "must be a boolean")
    for field, value in _metadata_values_for(metadata, {"address_hash"}):
        if value is not None and not re.fullmatch(r"[0-9a-f]{16}", str(value)):
            raise AuditMetadataValueViolation(
                field, "must be a 16-lowercase-hex address audit token"
            )
    for field, value in _metadata_values_for(metadata, {"zip5"}):
        if value is not None and not re.fullmatch(r"[0-9]{5}", str(value)):
            raise AuditMetadataValueViolation(field, "must be a 5-digit ZIP")
    for field, value in _metadata_values_for(metadata, {"source_assets"}):
        if value is None:
            continue
        try:
            validate_source_assets(value)
        except ValueError as exc:
            raise AuditMetadataValueViolation(field, str(exc)) from exc
    for field, value in _metadata_values_for(metadata, {"destination_key"}):
        if value is not None and not _ACTIVATION_DESTINATION_KEY_PATTERN.fullmatch(str(value)):
            raise AuditMetadataValueViolation(
                field, "must be a governed activation destination slug"
            )
    for field, value in _metadata_values_for(metadata, {"destination_type"}):
        if value is not None and str(value) not in _ACTIVATION_DESTINATION_TYPES:
            raise AuditMetadataValueViolation(
                field, "must be a governed activation destination type"
            )
    for field, value in _metadata_values_for(metadata, {"activation_status"}):
        if value is not None and str(value) not in _ACTIVATION_STATUSES:
            raise AuditMetadataValueViolation(field, "must be a governed activation outbox status")
    for field, value in _metadata_values_for(metadata, {"workflow_id"}):
        if value is not None and str(value) not in _GROWTH_AGENT_WORKFLOWS:
            raise AuditMetadataValueViolation(field, "must be a governed growth-agent workflow id")
    for field, value in _metadata_values_for(metadata, {"workflow_title"}):
        if value is not None and str(value) not in _GROWTH_AGENT_TITLES:
            raise AuditMetadataValueViolation(
                field, "must be a governed growth-agent workflow title"
            )
    for field, value in _metadata_values_for(metadata, {"run_status"}):
        if value is not None and str(value) not in _GROWTH_AGENT_RUN_STATUSES:
            raise AuditMetadataValueViolation(field, "must be a governed growth-agent run status")
    for field, value in _metadata_values_for(metadata, {"trace_id"}):
        if value is not None and not re.fullmatch(r"agent-trace-[0-9a-fA-F-]{36}", str(value)):
            raise AuditMetadataValueViolation(field, "must be a governed agent trace id")
    for field, value in _metadata_values_for(
        metadata,
        {
            "tool_result_hash",
            "growth_agent_filters_fingerprint",
            "growth_agent_cohort_fingerprint",
        },
    ):
        if value is None:
            continue
        try:
            validate_sql_hash(value)
        except ValueError as exc:
            raise AuditMetadataValueViolation(field, str(exc)) from exc
    for field, value in _metadata_values_for(metadata, {"growth_agent_source_snapshot"}):
        if value is None:
            continue
        try:
            validate_public_audit_identifier_or_none(str(value))
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                field, "must be a public-safe source snapshot identifier"
            ) from exc
    for field, value in _metadata_values_for(metadata, {"specialist_agent"}):
        if value is not None and str(value) not in _GROWTH_AGENT_SPECIALISTS:
            raise AuditMetadataValueViolation(field, "must be a governed specialist id")
    for field, value in _metadata_values_for(metadata, {"broad_total", "actionable_total"}):
        if value is None:
            continue
        try:
            validate_row_count(value)
        except ValueError as exc:
            raise AuditMetadataValueViolation(field, str(exc)) from exc
    for field, value in _metadata_values_for(
        metadata, {"tool_steps", "policy_checks", "governance_chips"}
    ):
        if value is None:
            continue
        if not isinstance(value, list) or len(value) > 12:
            raise AuditMetadataValueViolation(field, "must be a bounded reviewed list")
        for item in value:
            if not isinstance(item, dict):
                raise AuditMetadataValueViolation(field, "must contain reviewed objects")
            for key, nested_value in item.items():
                if key == "tool_name" and nested_value is not None:
                    if str(nested_value) not in _GROWTH_AGENT_TOOL_NAMES:
                        raise AuditMetadataValueViolation(
                            field, "must reference a reviewed growth-agent tool"
                        )
                    continue
                if key == "result_hash" and nested_value is not None:
                    try:
                        validate_sql_hash(nested_value)
                    except ValueError as exc:
                        raise AuditMetadataValueViolation(field, str(exc)) from exc
                    continue
                if key == "source_asset" and nested_value is not None:
                    try:
                        validate_source_assets([str(nested_value)])
                    except ValueError as exc:
                        raise AuditMetadataValueViolation(field, str(exc)) from exc
                    continue
                if key == "evidence_ref" and nested_value is not None:
                    evidence_ref = str(nested_value)
                    if evidence_ref.startswith("agent-trace-"):
                        if not re.fullmatch(r"agent-trace-[0-9a-fA-F-]{36}", evidence_ref):
                            raise AuditMetadataValueViolation(
                                field, "must be a governed agent trace id"
                            )
                        continue
                    if evidence_ref.startswith("mip."):
                        try:
                            validate_source_assets([evidence_ref])
                        except ValueError as exc:
                            raise AuditMetadataValueViolation(field, str(exc)) from exc
                        continue
                    try:
                        validate_sql_hash(evidence_ref)
                    except ValueError:
                        pass
                    else:
                        continue
                if _growth_agent_reviewed_text_contains_pii(nested_value):
                    raise AuditMetadataValueViolation(field, "must not contain PII-shaped values")
    strict_sql_hash = str(metadata.get("action") or "") == "view_borrower_proof"
    for field, value in _metadata_values_for(metadata, {"sql_hash"}):
        if value is not None and strict_sql_hash:
            try:
                validate_sql_hash(value)
            except ValueError as exc:
                raise AuditMetadataValueViolation(field, str(exc)) from exc
    household_count_fields = {
        "household_candidate_count",
        "household_primary_count",
        "household_suppressed_count",
        "household_household_count",
        "household_owner_link_count",
        "household_mailing_address_count",
        "household_singleton_count",
    }
    for field, value in _metadata_values_for(metadata, {"row_count"} | household_count_fields):
        if value is None:
            continue
        try:
            validate_row_count(value)
        except ValueError as exc:
            raise AuditMetadataValueViolation(field, str(exc)) from exc
    for field, value in _metadata_values_for(metadata, {"per_lo_counts"}):
        if value is None:
            continue
        if not isinstance(value, dict):
            raise AuditMetadataValueViolation(
                field, "must be an object keyed by internal staff email"
            )
        if len(value) > 25:
            raise AuditMetadataValueViolation(field, "must contain at most 25 loan officers")
        for key, count in value.items():
            try:
                validate_internal_staff_email(str(key))
            except ValueError as exc:
                raise AuditMetadataValueViolation(
                    field,
                    "must contain only approved internal staff emails",
                ) from exc
            if not isinstance(count, int) or count < 0 or count > 500:
                raise AuditMetadataValueViolation(field, "must contain bounded integer counts")
