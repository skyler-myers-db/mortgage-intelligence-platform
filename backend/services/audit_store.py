"""Audit store -- Lakebase-backed append-only ledger.

Runtime writes through ``mip_app.action_audit``. ``resolve_actor`` reads the
Databricks Apps ``X-Forwarded-Email`` header and emits an observable fallback
warning when local/test paths use ``settings.default_actor``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from fastapi import Request

from backend.config.settings import settings
from backend.schemas.audit import AuditEvent
from backend.schemas.common import (
    contains_pii_marker,
    validate_internal_staff_email,
    validate_public_audit_action,
    validate_public_audit_entity_type,
    validate_public_audit_event_type,
    validate_public_audit_identifier_or_none,
    validate_public_audit_subject_segment,
    validate_public_borrower_id,
    validate_public_campaign_label,
    validate_public_opaque_id,
)
from backend.schemas.lead import SEGMENT_CODE_VALUES
from backend.services.agent_tools import registered_agent_tool_names
from backend.services.audit_decision_inputs import DECISION_INPUT_KEYS
from backend.services.audit_metadata_value_policy import (
    validate_row_count,
    validate_source_assets,
    validate_sql_hash,
)
from backend.services.observability import emit
from backend.services.pii_redaction import (
    normalize_public_lender_ref,
    scrub_free_text,
)
from backend.services.scoring import NBO_PRODUCT_LABELS

log = logging.getLogger(__name__)


# Process-local count of identity-header fallbacks. Tests exercise it via
# ``_reset_fallback_counter_for_tests`` + ``get_fallback_identity_count``.


_FALLBACK_IDENTITY_COUNT: int = 0


def get_fallback_identity_count() -> int:
    """Return the current process-local fallback-identity count."""
    return _FALLBACK_IDENTITY_COUNT


def _reset_fallback_counter_for_tests() -> None:
    """Test helper -- zero the counter between tests."""
    global _FALLBACK_IDENTITY_COUNT
    _FALLBACK_IDENTITY_COUNT = 0


# ----------------------------------------------------------------------
# PII denylist -- Slice 6 governance follow-up. The audit ledger is
# append-only and read-heavy; once a raw name or address lands there,
# we cannot scrub it without disturbing the chain. The denylist blocks
# at *write* time rather than at read time so PII never reaches the
# JSONB column in the first place.
#
# Keys are lower-cased and compared case-insensitively. We match on the
# whole key, not substrings, to avoid false positives against
# legitimate keys like ``owner_link_id`` or ``display_lender``.
# ----------------------------------------------------------------------


_PII_DENYLIST_KEYS: frozenset[str] = frozenset(
    {
        "address",
        "borrower_email",
        "owner_name",
        "owner_full_name",
        "owner_address",
        "display_name",
        "full_name",
        "first_name",
        "last_name",
        "mailing_address",
        "street_address",
        "mailing_street",
        "property_address",
        "borrower_name",
        "email",
        "phone",
        "raw_lender",
        "raw_owner_name",
    }
)


# ----------------------------------------------------------------------
# R6-20 allowlist -- belt-and-suspenders PII containment for audit
# metadata. ``lakebase/schema.sql`` line 83 says "NO PII" in the
# ``metadata JSONB`` column comment; the denylist above covers the
# obvious PII keys but it is *reactive* -- it catches keys we already
# knew were bad. A dev accidentally adding ``owner_address`` or
# ``contact_preference`` to an approve payload would slip through.
#
# The allowlist flips the default: only keys we have intentionally
# written get through. Every new audit metadata key needs an explicit
# entry here, which means the reviewer adding the key has to think
# about PII surface before the write lands in production.
#
# To extend this list: audit the call site, confirm the value is not
# PII-adjacent (no names, addresses, phone numbers, ssns, dobs), then
# add the key here and a line in the PR description explaining why.
#
# Inventory is the union of keys written by every audit.write() call
# site in backend/api/* as of 2026-04-23:
#
#   backend/api/borrowers.py::read_borrower_360
#     opportunity_score, confidence, segment_codes, recommended_offer,
#     decision_inputs
#   backend/api/outreach.py::draft_outreach
#     channel, offer_code
#   backend/api/outreach.py::approve_outreach
#     approval_id, offer_code, borrower_id, request_id, draft_body,
#     rationale, bulk_id, bulk_rationale, decision_inputs
#   backend/api/outreach.py::reject_outreach
#     approval_id, offer_code, borrower_id, request_id, rationale, rationale_code
#   backend/api/leads.py::list_leads_ranked
#     rendered_borrower_ids, portfolio_id, segment, segment_mode, limit
#   backend/api/offers.py::recommend_offer
#     offer_code, confidence, thresholds_applied, decision_inputs
#   backend/services/repositories/databricks_portfolio.py
#     portfolio_criteria, suppression_policy, channel_cascade, send_window,
#     holdout, roi_assumptions, marketable_population, status
# Plus two keys injected by the audit layer itself:
#   action      -- canonical verb, added by LakebaseAuditStore.write
#   evidence_ids -- some flows may pass it inside payload_json (legacy)
#                   instead of the top-level kwarg; accept both shapes
#
# ``reason`` is included for forward compat: the outreach reject path
# stores a caller-supplied rationale (already free-text-scrubbed by
# ``scrub_free_text``); a future slice may rename ``rationale`` ->
# ``reason`` to match governance §4 vocabulary.
# ----------------------------------------------------------------------


_ALLOWED_METADATA_KEYS: frozenset[str] = frozenset(
    {
        # Audit-layer injected
        "action",
        "evidence_ids",
        # Borrower 360 view
        "opportunity_score",
        "confidence",
        "segment_codes",
        "recommended_offer",
        "workspace_offer_code",
        # Outreach draft / approve / reject
        "channel",
        "offer_code",
        "approval_id",
        "borrower_id",
        "request_id",
        "draft_body",
        "rationale",
        "rationale_code",
        "bulk_id",
        "bulk_rationale",
        "reason",
        "variant_name",
        # Marketing-contactability and disclosure proof
        "marketing_eligible",
        "consent_status",
        "suppression_reason",
        "last_touch_at",
        "eligible_recontact_at",
        "disclosure_version",
        "disclosure_state",
        "disclosure_channel",
        # Leads list
        "rendered_borrower_ids",
        "borrower_ids",
        "portfolio_id",
        "segment",
        "segment_mode",
        "state",
        "zip",
        "county",
        "counties",
        "states",
        "zips",
        "limit",
        "approval_status",
        "outreach_status",
        "assigned_to_email",
        # Feature C: loan-officer assignment + follow-up reminder captured
        # at approval time. ``assigned_to_email`` (above) is already
        # internal-staff-email validated; ``follow_up_at`` is an ISO
        # timestamp (now + N days), no PII.
        "follow_up_at",
        "aged_days",
        "target_lender_ref",
        "portfolio_criteria",
        "cohort_id",
        # Sales manager workflow state
        "assignment_id",
        "assigned_by",
        "assigned_at",
        "expires_at",
        "strategy",
        "assigned_count",
        "lo_email",
        "lo_emails",
        "per_lo_counts",
        "disposition_id",
        "outcome",
        "attempt_number",
        "occurred_at",
        "callback_at",
        "notes",
        "lead_outcome_id",
        "lead_outcome_type",
        "source_system",
        "source_record_ref",
        "loan_amount",
        "competitor_lender_label",
        # Genie control-layer actions
        "action_type",
        "refusal_reason",
        "conversation_id",
        "message_id",
        "question_hash",
        # Genie answer feedback (thumbs up/down). ``helpful`` is a bool;
        # ``comment_present`` records only whether a sanitized free-text note
        # accompanied the feedback -- the note itself is scrubbed and posted as
        # a Genie comment, never stored verbatim in audit metadata.
        "helpful",
        "comment_present",
        "row_count",
        "saved_count",
        "campaign_id",
        "criteria_hash",
        "criteria_keys",
        "source",
        "source_assets",
        "visualization_kind",
        "route",
        "result_filters",
        "sql_hash",
        "requested_state",
        "footprint_states",
        # Governed property loan lookup (address -> CLIP -> loan). The audit
        # payload carries the FIRST 16 HEX of the address_hash only (never the
        # full hash, never the raw street address), the 5-digit ZIP, a hit
        # boolean, and a masked clip ref on hit.
        "address_hash",
        "zip5",
        "hit",
        # Mortgage Growth Agent read/monitor workflows. These are
        # reviewed workflow ids, counts, route filters, and proof-step
        # summaries only -- no raw prompt text or borrower identities.
        "workflow_id",
        "workflow_title",
        "run_status",
        "broad_total",
        "actionable_total",
        "trace_id",
        "tool_result_hash",
        "specialist_agent",
        "tool_steps",
        "policy_checks",
        "governance_chips",
        # Offers
        "thresholds_applied",
        "decision_inputs",
        # Portfolio/campaign write paths that insert inside a Lakebase
        # transaction but still use the central audit metadata policy.
        "suppression_policy",
        "channel_cascade",
        "send_window",
        "holdout",
        "roi_assumptions",
        "marketable_population",
        "status",
        # Admin degraded-banner proof drill
        "forced_state",
        "forced_dependency",
        "ttl_s",
        "proof_scope", "job_key", "job_name", "job_id", "run_id", "cooldown_seconds",
        "lifecycle_sync_mode", "lakebase_row_count", "mirrored_row_count", "funnel_snapshot_row_count",
        # Governed customer activation / writeback outbox.
        "activation_id", "destination_key", "destination_type", "activation_status",
    }
)

_FREE_TEXT_METADATA_KEYS: frozenset[str] = frozenset(
    {"draft_body", "rationale", "bulk_rationale", "reason", "notes"}
)
_NESTED_METADATA_KEYS_WITH_OWN_POLICY: frozenset[str] = frozenset(
    {
        "decision_inputs",
        "governance_chips",
        "per_lo_counts",
        "policy_checks",
        "portfolio_criteria",
        "result_filters",
        "thresholds_applied",
        "tool_steps",
    }
)
_HUMAN_NAME_OR_PLACEHOLDER_PATTERN = re.compile(
    r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b|"
    r"\[(?:first|last|full)[_\s-]?[Nn]ame\]|\{(?:first|last|full)[_\s-]?[Nn]ame\}"
)
_GROWTH_AGENT_REVIEWED_NAME_WORD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ALL",
        "Agent",
        "Applied",
        "Bricks",
        "Broad",
        "Candidates",
        "Conversation",
        "Competitor",
        "Custom",
        "Data",
        "Databricks",
        "Eligible",
        "Equity",
        "Evaluation",
        "Genie",
        "Growth",
        "HELOC",
        "High",
        "Home",
        "Human",
        "Intent",
        "Lakebase",
        "Lead",
        "MLflow",
        "Mortgage",
        "Mosaic",
        "PII",
        "Policy",
        "Prime",
        "Queue",
        "Refi",
        "Reviewed",
        "Segment",
        "Summit",
        "Supervisor",
        "Watch",
        "Workflow",
    }
)

_BORROWER_ID_METADATA_KEYS: frozenset[str] = frozenset({"borrower_id"})

_OPAQUE_ID_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "approval_id",
        "bulk_id",
        "campaign_id",
        "request_id",
        "assignment_id",
        "disposition_id",
        "lead_outcome_id",
        "activation_id",
    }
)
_CAMPAIGN_LABEL_METADATA_KEYS: frozenset[str] = frozenset({"variant_name", "recommended_offer", "workspace_offer_code"})
_INTERNAL_STAFF_EMAIL_METADATA_KEYS: frozenset[str] = frozenset({"assigned_to_email", "assigned_by", "lo_email"})
_INTERNAL_STAFF_EMAIL_LIST_METADATA_KEYS: frozenset[str] = frozenset({"lo_emails"})
_SALES_STRATEGIES: frozenset[str] = frozenset({"manual", "round_robin", "score_balanced"})
_SALES_DISPOSITION_OUTCOMES: frozenset[str] = frozenset(
    {"called_no_answer", "called_left_voicemail", "connected", "callback_scheduled", "application_started", "not_interested", "not_now", "dead"}
)
_LEAD_OUTCOME_TYPES: frozenset[str] = frozenset(
    {"application_submitted", "closed_funded", "lost_to_competitor", "withdrawn", "not_qualified"}
)
_LEAD_OUTCOME_SOURCE_SYSTEMS: frozenset[str] = frozenset(
    {"salesforce", "crm_cdp", "los_pos", "servicing", "webhook", "manual_import"}
)
_PUBLIC_BUSINESS_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &.,:+-]{0,79}$")
_PUBLIC_COMPETITOR_LABEL_PATTERN = re.compile(r"^Competitor ([A-Z]|Other)$")
_GENIE_REFUSAL_REASONS: frozenset[str] = frozenset({"protected_class", "instruction_override", "pii_request", "scope_bypass", "out_of_scope"})

_ALLOWED_OFFER_CODES: frozenset[str] = frozenset(NBO_PRODUCT_LABELS) | {"recapture"}

_BORROWER_ID_LIST_METADATA_KEYS: frozenset[str] = frozenset({"borrower_ids", "rendered_borrower_ids"})


def _metadata_values_for(
    metadata: dict[str, Any],
    keys: frozenset[str] | set[str],
) -> list[tuple[str, Any]]:
    lowered = {key.lower() for key in keys}
    return [(key, value) for key, value in metadata.items() if key.lower() in lowered]

_ALLOWED_RESULT_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "zips",
        "states",
        "county",
        "counties",
        "segment_codes",
        "segment_mode",
        "funnel_stage",
        "target_lender_ref",
        "borrower_ids",
        "portfolio_criteria",
        "source",
        "approval_status",
        "outreach_status",
        "aged_days",
    }
)
_MAX_RESULT_FILTER_VALUES = 500
_MAX_RESULT_FILTER_STATES = 56
_DECISION_INPUT_KEYS: frozenset[str] = frozenset(DECISION_INPUT_KEYS)

_ALLOWED_SEGMENT_CODES: frozenset[str] = frozenset(SEGMENT_CODE_VALUES)
_ALLOWED_FUNNEL_STAGES: frozenset[str] = frozenset(
    {
        "addressable",
        "in_the_money",
        "high_opportunity",
        "offer_recommended",
        "approved",
        "actioned",
    }
)
_FORCED_DEGRADED_DEPENDENCIES: frozenset[str] = frozenset({"warehouse", "lakebase", "genie", "all"})
_ACTIVATION_DESTINATION_TYPES: frozenset[str] = frozenset({"salesforce", "crm_cdp", "los_pos", "servicing", "webhook"})
_ACTIVATION_STATUSES: frozenset[str] = frozenset({"dry_run", "staged", "delivered", "failed", "cancelled"})
_ACTIVATION_DESTINATION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_GROWTH_AGENT_WORKFLOWS: frozenset[str] = frozenset(
    {
        "daily_refi_brief",
        "borrower_dossier_review",
        "listing_watch",
        "competitor_recapture_monitor",
        "high_equity_heloc_watch",
        "branch_capacity_review",
        "source_freshness_sentinel",
        "custom_segment_watch",
    }
)
_GROWTH_AGENT_TITLES: frozenset[str] = frozenset(
    {
        "Daily Refi Opportunity Brief",
        "Borrower Dossier Review",
        "Listed-for-Sale Purchase Watch",
        "Competitor Recapture Monitor",
        "High-Equity / HELOC Watch",
        "Branch Manager Capacity Review",
        "Source/Freshness Sentinel",
        "Custom Segment Workflow",
    }
)
_GROWTH_AGENT_RUN_STATUSES: frozenset[str] = frozenset({"completed"})
_GROWTH_AGENT_SPECIALISTS: frozenset[str] = frozenset(
    {
        "structured_data_agent",
        "borrower_dossier_agent",
        "offer_agent",
        "compliance_agent",
        "campaign_agent",
        "data_ops_agent",
    }
)
_GROWTH_AGENT_TOOL_NAMES: frozenset[str] = frozenset(registered_agent_tool_names())


class AuditPIIError(RuntimeError):
    """Raised when audit metadata would contain raw PII.

    Surfaces as a 500 in dev so the offending route gets fixed; in
    production the router's ``except`` still lets this propagate so the
    ledger never gets poisoned. This is louder than silently dropping
    the row -- governance needs to know when write-paths try to log
    names.
    """

    def __init__(self, forbidden_keys: list[str]) -> None:
        self.forbidden_keys = forbidden_keys
        super().__init__(
            "Audit metadata contains forbidden PII-adjacent keys: "
            + ", ".join(sorted(forbidden_keys))
        )


class AuditMetadataViolation(RuntimeError):
    """Raised when audit metadata contains a key outside the allowlist.

    R6-20: the ``lakebase/schema.sql`` comment says "NO PII" on the
    metadata JSONB column, but the guarantee was only mechanically
    enforced against a known-bad denylist. A router adding a new field
    (e.g. a dev plumbs ``owner_name`` through a reject payload) would
    slip past the denylist if the field name didn't lexically match a
    known-bad key.

    The allowlist inverts the default: only reviewed keys pass through,
    so an unvetted addition fails loudly in tests before it can land in
    production.
    """

    def __init__(self, unexpected_keys: list[str]) -> None:
        self.unexpected_keys = unexpected_keys
        super().__init__(
            "Audit metadata contains unexpected keys (not on the "
            "reviewed allowlist -- see ``_ALLOWED_METADATA_KEYS`` in "
            "backend/services/audit_store.py for the inventory and how "
            "to extend it): "
            + ", ".join(sorted(unexpected_keys))
        )


class AuditMetadataValueViolation(RuntimeError):
    """Raised when a reviewed audit metadata key carries an unsafe value."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        super().__init__(f"Audit metadata field {field!r} failed value policy: {reason}")


def _metadata_keys_deep(value: Any) -> set[str]:
    if isinstance(value, dict):
        out: set[str] = {str(k).lower() for k in value}
        for nested in value.values():
            out.update(_metadata_keys_deep(nested))
        return out
    if isinstance(value, list):
        out: set[str] = set()
        for nested in value:
            out.update(_metadata_keys_deep(nested))
        return out
    return set()


def _metadata_pii_value_paths(value: Any, *, path: str = "metadata") -> set[str]:
    """Return JSON paths whose scalar values contain obvious PII markers.

    ``_sanitize_metadata`` scrubs the intentionally free-text top-level fields
    before this function runs. This recursive pass is for arbitrary nested
    containers under allowed keys such as ``source`` where a caller could
    otherwise hide ``{"free_text": "123 Main St"}`` from the top-level
    allowlist and free-text scrub.
    """
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_lower = str(key).lower()
            if (
                key_lower in _INTERNAL_STAFF_EMAIL_METADATA_KEYS
                or key_lower in _INTERNAL_STAFF_EMAIL_LIST_METADATA_KEYS
            ):
                # Staff email metadata is operational, not borrower contact
                # data, and is constrained by _assert_public_safe_values.
                continue
            if path == "metadata":
                if key_lower in _NESTED_METADATA_KEYS_WITH_OWN_POLICY:
                    continue
                if not isinstance(nested, dict | list):
                    # Top-level scalar fields already have key-specific
                    # validators below (opaque ids, campaign labels, staff
                    # emails, etc.). This scan exists for arbitrary nested
                    # containers such as source={...}.
                    continue
            hits.update(_metadata_pii_value_paths(nested, path=f"{path}.{key}"))
        return hits
    if isinstance(value, list):
        for idx, nested in enumerate(value):
            hits.update(_metadata_pii_value_paths(nested, path=f"{path}[{idx}]"))
        return hits
    if value is None or isinstance(value, bool | int | float):
        return hits
    text = str(value)
    if contains_pii_marker(text) or scrub_free_text(text) != text:
        hits.add(path)
    return hits


def _growth_agent_reviewed_text_contains_pii(value: Any) -> bool:
    if value is None or isinstance(value, bool | int | float):
        return False
    text = str(value)
    return bool(
        contains_pii_marker(text)
        or scrub_free_text(text) != text
        or _growth_agent_reviewed_text_contains_human_name(text)
    )


def _growth_agent_reviewed_text_contains_human_name(text: str) -> bool:
    for match in _HUMAN_NAME_OR_PLACEHOLDER_PATTERN.finditer(text):
        candidate = match.group(0)
        if candidate.startswith(("[", "{")):
            return True
        words = [word for word in re.split(r"\s+", candidate) if word]
        if words and all(word in _GROWTH_AGENT_REVIEWED_NAME_WORD_ALLOWLIST for word in words):
            continue
        return True
    return False


def _assert_no_pii(metadata: dict[str, Any]) -> None:
    """Raise ``AuditPIIError`` if ``metadata`` has any denylist keys.

    The key scan is deep: allowed top-level containers may hold structured
    proof objects, but they must not smuggle raw borrower/contact/address keys
    into nested JSON. Scalar value scanning is also deep for email, phone, SSN,
    and street-address shapes; intentionally free-text fields are scrubbed
    before this check runs.
    """
    if not metadata:
        return
    lowered = _metadata_keys_deep(metadata)
    hits = lowered & _PII_DENYLIST_KEYS
    value_hits = _metadata_pii_value_paths(metadata)
    if hits or value_hits:
        raise AuditPIIError(sorted(hits | value_hits))


def _assert_allowlisted(metadata: dict[str, Any]) -> None:
    """Raise ``AuditMetadataViolation`` if any top-level key is unknown.

    Complements ``_assert_no_pii`` (denylist) with an allowlist gate so
    a new-but-unreviewed key fails loudly. Top-level only, matching the
    denylist's scope. See ``_ALLOWED_METADATA_KEYS`` for the inventory
    and extension procedure.
    """
    if not metadata:
        return
    lowered_keys = {k.lower() for k in metadata}
    unexpected = lowered_keys - _ALLOWED_METADATA_KEYS
    if unexpected:
        raise AuditMetadataViolation(sorted(unexpected))


def _assert_public_safe_values(metadata: dict[str, Any]) -> None:
    """Validate reviewed free-ish values that have their own public policy."""
    if not metadata:
        return
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
    }.items():
        for _, value in _metadata_values_for(metadata, {field}):
            if value is not None and str(value) not in allowed:
                raise AuditMetadataValueViolation(field, "contains values outside the reviewed vocabulary")
    for field, ttl_s in _metadata_values_for(metadata, {"ttl_s"}):
        if not isinstance(ttl_s, int) or isinstance(ttl_s, bool) or ttl_s < 0 or ttl_s > 300:
            raise AuditMetadataValueViolation(field, "must be an integer between 0 and 300")
    for field, seconds in _metadata_values_for(metadata, {"cooldown_seconds"}):
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0 or seconds > 7200:
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
            raise AuditMetadataValueViolation(field, "must be a public-safe source record reference") from exc
    for field, value in _metadata_values_for(metadata, {"loan_amount"}):
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 100_000_000:
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
        if value is not None and str(value) not in _GENIE_REFUSAL_REASONS:
            raise AuditMetadataValueViolation(field, "must be a governed Genie refusal reason")
    for field, value in _metadata_values_for(metadata, {"helpful", "comment_present", "hit"}):
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
            raise AuditMetadataValueViolation(field, "must be a governed activation destination slug")
    for field, value in _metadata_values_for(metadata, {"destination_type"}):
        if value is not None and str(value) not in _ACTIVATION_DESTINATION_TYPES:
            raise AuditMetadataValueViolation(field, "must be a governed activation destination type")
    for field, value in _metadata_values_for(metadata, {"activation_status"}):
        if value is not None and str(value) not in _ACTIVATION_STATUSES:
            raise AuditMetadataValueViolation(field, "must be a governed activation outbox status")
    for field, value in _metadata_values_for(metadata, {"workflow_id"}):
        if value is not None and str(value) not in _GROWTH_AGENT_WORKFLOWS:
            raise AuditMetadataValueViolation(field, "must be a governed growth-agent workflow id")
    for field, value in _metadata_values_for(metadata, {"workflow_title"}):
        if value is not None and str(value) not in _GROWTH_AGENT_TITLES:
            raise AuditMetadataValueViolation(field, "must be a governed growth-agent workflow title")
    for field, value in _metadata_values_for(metadata, {"run_status"}):
        if value is not None and str(value) not in _GROWTH_AGENT_RUN_STATUSES:
            raise AuditMetadataValueViolation(field, "must be a governed growth-agent run status")
    for field, value in _metadata_values_for(metadata, {"trace_id"}):
        if value is not None and not re.fullmatch(r"agent-trace-[0-9a-fA-F-]{36}", str(value)):
            raise AuditMetadataValueViolation(field, "must be a governed agent trace id")
    for field, value in _metadata_values_for(metadata, {"tool_result_hash"}):
        if value is None:
            continue
        try:
            validate_sql_hash(value)
        except ValueError as exc:
            raise AuditMetadataValueViolation(field, str(exc)) from exc
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
    for field, value in _metadata_values_for(metadata, {"tool_steps", "policy_checks", "governance_chips"}):
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
                        raise AuditMetadataValueViolation(field, "must reference a reviewed growth-agent tool")
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
                            raise AuditMetadataValueViolation(field, "must be a governed agent trace id")
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
    for field, value in _metadata_values_for(metadata, {"row_count"}):
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
            raise AuditMetadataValueViolation(field, "must be an object keyed by internal staff email")
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


def _validate_top_level_audit_columns(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    event_type: str | None,
    subject_segment: str | None,
    request_id: str | None,
) -> tuple[str, str, str, str | None, str | None, str | None]:
    try:
        safe_action = validate_public_audit_action(action)
        safe_entity_type = validate_public_audit_entity_type(entity_type)
        safe_entity_id = validate_public_audit_identifier_or_none(entity_id)
        if safe_entity_id is None:
            raise ValueError("entity_id must not be blank")
        safe_event_type = (
            validate_public_audit_event_type(event_type) if event_type is not None else None
        )
        safe_subject_segment = (
            validate_public_audit_subject_segment(subject_segment)
            if subject_segment is not None
            else None
        )
        safe_request_id = (
            validate_public_opaque_id(request_id) if request_id is not None else None
        )
    except ValueError as exc:
        raise AuditMetadataValueViolation("audit_column", str(exc)) from exc
    return (
        safe_action,
        safe_entity_type,
        safe_entity_id,
        safe_event_type,
        safe_subject_segment,
        safe_request_id,
    )


_ALLOWED_PORTFOLIO_CRITERIA_KEYS: frozenset[str] = frozenset(
    {
        "geography",
        "occupancy",
        "lien_status",
        "lender_relationship",
        "product",
        "target_lender_ref",
        "min_equity_pct_label",
        "min_equity_pct",
        "owner_link",
        "purchase_intent",
        "states",
        "marketing_eligibility",
        "consent_status",
        "recency",
    }
)


def _assert_portfolio_criteria_value_policy(value: Any) -> None:
    if not isinstance(value, dict):
        raise AuditMetadataValueViolation(
            "portfolio_criteria",
            "must be an object with reviewed Portfolio Builder keys",
        )
    unexpected = {str(k).lower() for k in value} - _ALLOWED_PORTFOLIO_CRITERIA_KEYS
    if unexpected:
        raise AuditMetadataValueViolation(
            "portfolio_criteria",
            "contains unreviewed keys: " + ", ".join(sorted(unexpected)),
        )
    try:
        from backend.schemas.portfolio import PortfolioCriteria

        PortfolioCriteria(**value)
    except ValueError as exc:
        raise AuditMetadataValueViolation(
            "portfolio_criteria",
            "contains values outside the reviewed Portfolio Builder vocabularies",
        ) from exc


def _assert_string_list(
    field: str,
    value: Any,
    *,
    pattern: str | None = None,
    allowed: frozenset[str] | None = None,
    max_items: int = 100,
) -> None:
    if not isinstance(value, list):
        raise AuditMetadataValueViolation(field, "must be a reviewed list")
    if len(value) > max_items:
        raise AuditMetadataValueViolation(field, f"must contain at most {max_items} values")
    rx = re.compile(pattern) if pattern else None
    for item in value:
        text = str(item)
        if rx is not None and not rx.fullmatch(text):
            raise AuditMetadataValueViolation(field, "contains values outside the reviewed format")
        if allowed is not None and text not in allowed:
            raise AuditMetadataValueViolation(field, "contains values outside the reviewed vocabulary")


def _assert_result_filters_value_policy(value: Any) -> None:
    if not isinstance(value, dict):
        raise AuditMetadataValueViolation(
            "result_filters",
            "must be an object with reviewed cohort filter keys",
        )
    unexpected = {str(k).lower() for k in value} - _ALLOWED_RESULT_FILTER_KEYS
    if unexpected:
        raise AuditMetadataValueViolation(
            "result_filters",
            "contains unreviewed keys: " + ", ".join(sorted(unexpected)),
        )
    if "zips" in value:
        _assert_string_list(
            "result_filters.zips",
            value["zips"],
            pattern=r"^\d{5}$",
            max_items=_MAX_RESULT_FILTER_VALUES,
        )
    if "states" in value:
        _assert_string_list(
            "result_filters.states",
            value["states"],
            pattern=r"^[A-Z]{2}$",
            max_items=_MAX_RESULT_FILTER_STATES,
        )
    if "county" in value and not re.fullmatch(r"^\d{5}$", str(value["county"])):
        raise AuditMetadataValueViolation("result_filters.county", "must be a 5-digit county FIPS")
    if "counties" in value:
        _assert_string_list(
            "result_filters.counties",
            value["counties"],
            pattern=r"^\d{5}$",
            max_items=_MAX_RESULT_FILTER_VALUES,
        )
    if "segment_codes" in value:
        _assert_string_list(
            "result_filters.segment_codes",
            value["segment_codes"],
            allowed=_ALLOWED_SEGMENT_CODES,
            max_items=6,
        )
    if "segment_mode" in value and str(value["segment_mode"]) not in {"any", "all"}:
        raise AuditMetadataValueViolation("result_filters.segment_mode", "must be any or all")
    if "funnel_stage" in value and str(value["funnel_stage"]) not in _ALLOWED_FUNNEL_STAGES:
        raise AuditMetadataValueViolation(
            "result_filters.funnel_stage",
            "must be a reviewed funnel stage",
        )
    if "target_lender_ref" in value:
        try:
            normalize_public_lender_ref(str(value["target_lender_ref"]), allow_all=True)
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                "result_filters.target_lender_ref",
                "must be a public-safe lender alias",
            ) from exc
    if "borrower_ids" in value:
        borrower_ids = value["borrower_ids"]
        if not isinstance(borrower_ids, list):
            raise AuditMetadataValueViolation("result_filters.borrower_ids", "must be a list")
        if len(borrower_ids) > _MAX_RESULT_FILTER_VALUES:
            raise AuditMetadataValueViolation(
                "result_filters.borrower_ids",
                f"must contain at most {_MAX_RESULT_FILTER_VALUES} values",
            )
        for item in borrower_ids:
            try:
                validate_public_borrower_id(str(item))
            except ValueError as exc:
                raise AuditMetadataValueViolation(
                    "result_filters.borrower_ids",
                    "must contain only app-scoped public borrower ids",
                ) from exc
    if "portfolio_criteria" in value:
        _assert_portfolio_criteria_value_policy(value["portfolio_criteria"])
    if "source" in value and str(value["source"]) not in {"genie", "trusted_sql"}:
        raise AuditMetadataValueViolation("result_filters.source", "must be genie or trusted_sql")
    if "approval_status" in value and str(value["approval_status"]) not in {"pending", "approved", "rejected", "hold"}:
        raise AuditMetadataValueViolation(
            "result_filters.approval_status",
            "must be a reviewed approval status",
        )
    if "outreach_status" in value and str(value["outreach_status"]) not in {
        "none",
        "queued",
        "actioned",
        "sent",
        "bounced",
        "replied",
    }:
        raise AuditMetadataValueViolation(
            "result_filters.outreach_status",
            "must be a reviewed outreach status",
        )
    if "aged_days" in value:
        try:
            aged_days = int(value["aged_days"])
        except (TypeError, ValueError) as exc:
            raise AuditMetadataValueViolation("result_filters.aged_days", "must be 1 to 90") from exc
        if aged_days < 1 or aged_days > 90:
            raise AuditMetadataValueViolation("result_filters.aged_days", "must be 1 to 90")


def _assert_decision_inputs_value_policy(value: Any) -> None:
    if not isinstance(value, dict):
        raise AuditMetadataValueViolation(
            "decision_inputs",
            "must be an object with the reviewed scoring input keys",
        )
    keys = {str(key) for key in value}
    missing = _DECISION_INPUT_KEYS - keys
    extra = keys - _DECISION_INPUT_KEYS
    if missing or extra:
        detail: list[str] = []
        if missing:
            detail.append("missing " + ", ".join(sorted(missing)))
        if extra:
            detail.append("unreviewed " + ", ".join(sorted(extra)))
        raise AuditMetadataValueViolation("decision_inputs", "; ".join(detail))
    for field in ("rate_spread_bps", "equity_pct", "heloc_propensity_score", "refi_propensity_score"):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int):
            raise AuditMetadataValueViolation(f"decision_inputs.{field}", "must be an integer")
    equity_pct = int(value["equity_pct"])
    if equity_pct < 0 or equity_pct > 100:
        raise AuditMetadataValueViolation(
            "decision_inputs.equity_pct",
            "must be a percentage between 0 and 100",
        )
    for field in (
        "has_permit",
        "has_heloc_propensity_trigger",
        "has_refi_propensity_trigger",
        "listed_for_sale",
        "is_investor",
        "is_current_customer",
        "is_competitor_lien",
    ):
        if not isinstance(value[field], bool):
            raise AuditMetadataValueViolation(f"decision_inputs.{field}", "must be boolean")


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return reviewed audit metadata with known free-text values scrubbed.

    The allowlist says a key is allowed to exist; it does not prove caller
    supplied free text is clean. Scrub the few intentionally free-text fields
    at the write choke point so direct AuditStore use cannot persist a raw
    email, phone number, address, or SSN in the append-only ledger.
    """

    if not metadata:
        return {}
    cleaned = dict(metadata)
    for key in _FREE_TEXT_METADATA_KEYS:
        if key in cleaned and cleaned[key] is not None:
            cleaned[key] = scrub_free_text(str(cleaned[key]))
            if key == "notes" and _HUMAN_NAME_OR_PLACEHOLDER_PATTERN.search(str(cleaned[key])):
                raise AuditMetadataValueViolation(
                    key,
                    "must not contain human-name-shaped text or unresolved placeholders",
                )
    return cleaned


def build_safe_audit_metadata(
    payload_json: dict[str, Any] | None,
    *,
    action: str,
) -> dict[str, Any]:
    """Return metadata after the shared audit safety policy has run.

    Most writes flow through ``LakebaseAuditStore.write``. A few Lakebase
    repository methods insert the business row and the audit row in one
    SQL statement for atomicity; those paths still need the same metadata
    scrub, denylist, allowlist, and value validation before binding JSONB.
    """

    validate_public_audit_action(action)
    metadata = _sanitize_metadata({**(payload_json or {}), "action": action})
    _assert_no_pii(metadata)
    _assert_allowlisted(metadata)
    _assert_public_safe_values(metadata)
    return metadata


# ----------------------------------------------------------------------
# Protocol -- routers depend on this, not on a concrete class.
# ----------------------------------------------------------------------


@runtime_checkable
class AuditStore(Protocol):
    """Minimal audit surface. Kept narrow so swapping the backing store
    (in-memory for tests, Lakebase for production) is a factory edit.
    """

    def write(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_json: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        event_type: str | None = None,
        subject_clip: str | None = None,
        subject_segment: str | None = None,
        request_id: str | None = None,
    ) -> AuditEvent: ...

    def list(
        self,
        limit: int = 50,
        *,
        actor: str | None = None,
        action: str | None = None,
        entity_id: str | None = None,
        borrower_id: str | None = None,
        subject_clip: str | None = None,
        event_type: str | None = None,
        correlation_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEvent]: ...


# ----------------------------------------------------------------------
# Shared helpers.
# ----------------------------------------------------------------------


_UNTRUSTED_EDGE_ACTOR: str = "unknown-actor@untrusted-edge"


def resolve_actor(request: Request | None) -> str:
    """Read the workspace identity forwarded by Databricks Apps.

    Logs a WARNING when the header is absent so operators can spot
    dev/test traffic in production logs. The fallback value is
    ``settings.default_actor`` so audit rows are never written with
    a placeholder string or NULL in the authenticated actor column.

    R5-09 trust boundary: when ``settings.trust_forwarded_headers`` is
    False we ignore ``X-Forwarded-Email`` / ``X-Forwarded-User`` entirely
    and attribute the row to a distinct marker string so an operator
    grepping audit rows can spot "this deploy does not trust the edge,
    actor is unknowable" at a glance. The default stays True because the
    Databricks Apps edge IS the authoritative identity stripper; the
    flag exists for unusual reverse-proxy deploys.
    """
    if request is not None and settings.trust_forwarded_headers:
        email = request.headers.get("X-Forwarded-Email")
        if email:
            return email
        user = request.headers.get("X-Forwarded-User")
        if user:
            return user
    if request is not None and not settings.trust_forwarded_headers:
        # Trust disabled: don't even read the headers. Return the
        # untrusted-edge marker so audit attribution stays honest.
        # Do NOT bump the fallback-identity counter -- this is an
        # intentional deploy posture, not an identity-header miss.
        return _UNTRUSTED_EDGE_ACTOR
    # Fallback path: bump the counter and emit a structured WARNING so
    # the event is observable in stdout JSON logs AND surfaced through
    # ``/api/health`` as ``fallback_identity_fallbacks_process_total``
    # (the legacy ``fallback_identity_fallbacks_total`` key is still
    # emitted for one cycle; R6-08 rename).
    global _FALLBACK_IDENTITY_COUNT
    _FALLBACK_IDENTITY_COUNT += 1
    emit(
        log,
        "identity_fallback",
        level=logging.WARNING,
        default_actor=settings.default_actor,
        fallback_count=_FALLBACK_IDENTITY_COUNT,
        message="audit_store.resolve_actor: no forwarded identity header",
    )
    return settings.default_actor


def _coerce_event_type(event_type: str | None, action: str) -> str:
    """Governance §4 wants canonical verbs (VIEW_BORROWER / APPROVE /
    DRAFT_OUTREACH / RECOMMEND_OFFER / RUN_GENIE / VIEW_LEADS). When a
    caller passes only ``action`` (the pre-Slice-5 contract), we
    upper-case it and replace the dot separator. e.g.
    ``"outreach.approve"`` -> ``"OUTREACH_APPROVE"``.
    """
    if event_type:
        return event_type
    return action.replace(".", "_").replace("-", "_").upper()


# ----------------------------------------------------------------------
# Factory -- single choke point for the FastAPI dependency graph.
# ----------------------------------------------------------------------


_AUDIT_STORE: AuditStore | None = None


def get_audit_store() -> AuditStore:
    """Lazy process-singleton audit store.

    Production path constructs a ``LakebaseAuditStore`` backed by the
    Lakebase client singleton. Tests override this factory in
    ``tests/conftest.py`` with the test fixture audit store so unit tests
    never touch Postgres.
    """
    global _AUDIT_STORE
    if _AUDIT_STORE is None:
        from backend.services.audit_lakebase_store import LakebaseAuditStore

        _AUDIT_STORE = LakebaseAuditStore()
    return _AUDIT_STORE


def _reset_audit_store_for_tests() -> None:
    """Test helper -- drop the cached audit store so factory overrides stick."""
    global _AUDIT_STORE
    _AUDIT_STORE = None


# ----------------------------------------------------------------------
# Legacy shim -- the pre-Slice-5 audit router imported a module-level
# ``audit_store`` instance. We keep the name for back-compat but route
# all reads / writes through ``get_audit_store()`` so test overrides on
# the factory still take effect. The audit router migrates to the
# factory in this slice; the shim exists only so any stale import
# doesn't explode during the deploy window.
# ----------------------------------------------------------------------


class _AuditStoreProxy:
    def write(self, **kwargs: Any) -> AuditEvent:
        return get_audit_store().write(**kwargs)

    def list(self, limit: int = 50) -> list[AuditEvent]:
        return get_audit_store().list(limit=limit)


audit_store: AuditStore = _AuditStoreProxy()
