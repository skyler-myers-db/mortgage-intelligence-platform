"""Reviewed vocabulary for audit metadata: denylist, allowlists, and errors.

Single responsibility: hold the governance vocabulary the audit ledger is
written against -- the PII key denylist, the allowlisted metadata keys and
their reviewed value sets, the bounded numeric limits, and the three typed
violations raised when a write falls outside it. Nothing here inspects a
payload; the validators import this module so the reviewed terms stay
auditable in one place.

The exceptions live here rather than beside the validators so every layer
(policy, validation, public-value assertions, the store) can raise them
without importing back up the chain.

``backend.services.audit_store`` re-exports the names other modules and
tests import, so existing import sites keep working unchanged.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from backend.schemas.genie_geo_filters import GENIE_CITY_FILTER_KEY
from backend.schemas.genie_numeric_filters import (
    GENIE_NUMERIC_FILTER_BOUNDS,
    GENIE_NUMERIC_FILTER_KEYS,
)
from backend.schemas.lead import SEGMENT_CODE_VALUES
from backend.services.agent_tools import registered_agent_tool_names
from backend.services.audit_decision_inputs import DECISION_INPUT_KEYS
from backend.services.scoring import NBO_PRODUCT_LABELS

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
#     approval_id, offer_code, borrower_id, request_id, draft_subject, draft_body,
#     draft_generation_id, draft_response_hash, draft_source_refreshed_at,
#     campaign_treatment_fingerprint,
#     draft_edited, draft_attribution, rationale, bulk_id, bulk_rationale,
#     decision_inputs
#   backend/api/outreach.py::reject_outreach
#     approval_id, offer_code, borrower_id, request_id, rationale, rationale_code
#   backend/api/outreach.py::draft_outreach
#     generation_mode
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
        "source_refreshed_at",
        "segment_codes",
        "recommended_offer",
        "workspace_offer_code",
        # Outreach draft / approve / reject
        "channel",
        "offer_code",
        "approval_id",
        "borrower_id",
        "request_id",
        "draft_subject",
        "draft_body",
        "draft_generation_id",
        "draft_response_hash",
        "campaign_treatment_fingerprint",
        "draft_source_refreshed_at",
        "draft_edited",
        "draft_attribution",
        "has_subject",
        "rationale",
        "rationale_code",
        "bulk_id",
        "bulk_rationale",
        "reason",
        "variant_name",
        "generation_mode",
        "campaign_generation_mode",
        "generator_label",
        "variant_provenance",
        # Marketing-contactability and disclosure proof
        "marketing_eligible",
        "consent_status",
        "suppression_reason",
        "last_touch_at",
        "eligible_recontact_at",
        # S1.4 contact-eligibility enforcement: do-not-contact flag and the
        # consent-provenance slug (synthetic_seed or a CRM/CDP connector id).
        # Both are controlled machine values, never free text.
        "dnc",
        "eligibility_source",
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
        # Emitted by /leads when the request carried a city cohort.
        GENIE_CITY_FILTER_KEY,
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
        # Numeric floors a governed Genie cohort replays into the queue, so the
        # VIEW_LEADS row records the thresholds the list was actually cut at.
        # (``min_equity_pct`` is recorded inside ``portfolio_criteria``, which
        # is where that vocabulary compiles it.)
        "min_opportunity_score",
        "min_rate_spread_bps",
        # Verified Growth Agent -> Lead Queue handoff provenance. These values
        # come from a server-verified signed token, never from URL labels.
        "growth_agent_run_id",
        "growth_agent_filters_fingerprint",
        "growth_agent_cohort_fingerprint",
        "growth_agent_source_snapshot",
        # Sales manager workflow state
        "assignment_id",
        "assigned_by",
        "assigned_at",
        "expires_at",
        "strategy",
        # S2 loan-officer entity + assignment lifecycle. ``loan_officer_id``
        # is a server-issued UUID; ``from_status``/``to_status`` are the
        # reviewed lifecycle stages, never free text.
        "loan_officer_id",
        "from_status",
        "to_status",
        "assigned_count",
        "lo_email",
        "lo_emails",
        "per_lo_counts",
        "disposition_id",
        "outcome",
        # S6 assignment outcome recording (feedback-table pattern)
        "assignment_outcome",
        "feedback_id",
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
        "dedupe_unit",
        "household_dedup_enabled",
        "household_primary_strategy",
        "household_candidate_count",
        "household_primary_count",
        "household_suppressed_count",
        "household_household_count",
        "household_owner_link_count",
        "household_mailing_address_count",
        "household_singleton_count",
        "status",
        "expected_status",
        # T0 campaign quarantine proof. Enum + boolean only; no cohort members.
        "treatment_state",
        "terminal_archive_without_treatment",
        # Immutable T0 campaign manifest proof (bounded enums, hashes, counts).
        "treatment_algorithm_version",
        "treatment_contract_fingerprint",
        "treatment_fingerprint",
        "source_snapshot_id",
        "candidate_count",
        "selected_primary_count",
        "treatment_count",
        "holdout_count",
        # Admin degraded-banner proof drill
        "forced_state",
        "forced_dependency",
        "ttl_s",
        "proof_scope",
        "job_key",
        "job_name",
        "job_id",
        "run_id",
        "cooldown_seconds",
        "lifecycle_sync_mode",
        "lakebase_row_count",
        "mirrored_row_count",
        "funnel_snapshot_row_count",
        # Governed customer activation / writeback outbox.
        "activation_id",
        "destination_key",
        "destination_type",
        "activation_status",
    }
)

_FREE_TEXT_METADATA_KEYS: frozenset[str] = frozenset(
    {"draft_subject", "draft_body", "rationale", "bulk_rationale", "reason", "notes"}
)
_BORROWER_DRAFT_METADATA_KEYS: frozenset[str] = frozenset({"draft_subject", "draft_body"})
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
        "variant_provenance",
    }
)
_HUMAN_NAME_OR_PLACEHOLDER_PATTERN = re.compile(
    r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b|"
    r"\[(?:first|last|full)[_\s-]?[Nn]ame\]|\{(?:first|last|full)[_\s-]?[Nn]ame\}"
)
_AUDIT_HUMAN_IDENTITY_DIRECTIVE_RE = re.compile(
    r"\b(?i:call|contact|email|message|ask|tell|notify|assign|refer)\s+"
    r"(?!(?:us|we|you|they|our|the|this|your|a|an|consent|authorization|permission|outreach|"
    r"support|compliance|operations|servicing|"
    r"is|was|has|had|will|should|must|may|can|could)\b)"
    r"[a-z][a-z'’-]{1,29}\s+[a-z][a-z'’-]{1,29}\b"
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
        "loan_officer_id",
        "draft_generation_id",
        "growth_agent_run_id",
    }
)
_CAMPAIGN_LABEL_METADATA_KEYS: frozenset[str] = frozenset(
    {"variant_name", "recommended_offer", "workspace_offer_code"}
)
_INTERNAL_STAFF_EMAIL_METADATA_KEYS: frozenset[str] = frozenset(
    {"assigned_to_email", "assigned_by", "lo_email"}
)
_INTERNAL_STAFF_EMAIL_LIST_METADATA_KEYS: frozenset[str] = frozenset({"lo_emails"})
_SALES_STRATEGIES: frozenset[str] = frozenset({"manual", "round_robin", "score_balanced"})
_ASSIGNMENT_LIFECYCLE_STATUSES: frozenset[str] = frozenset(
    {"assigned", "contact_drafted", "approved", "actioned", "outcome_recorded"}
)
# S6 recorded assignment outcome -- deliberately distinct from the call
# disposition outcomes ("outcome" key) and the customer-system lead outcome
# types ("lead_outcome_type" key). Mirrors ASSIGNMENT_OUTCOMES in
# backend/schemas/loan_officer.py.
_ASSIGNMENT_OUTCOMES: frozenset[str] = frozenset({"success", "no_response", "declined"})
_SALES_DISPOSITION_OUTCOMES: frozenset[str] = frozenset(
    {
        "called_no_answer",
        "called_left_voicemail",
        "connected",
        "callback_scheduled",
        "application_started",
        "not_interested",
        "not_now",
        "dead",
    }
)
_LEAD_OUTCOME_TYPES: frozenset[str] = frozenset(
    {"application_submitted", "closed_funded", "lost_to_competitor", "withdrawn", "not_qualified"}
)
_LEAD_OUTCOME_SOURCE_SYSTEMS: frozenset[str] = frozenset(
    {"salesforce", "crm_cdp", "los_pos", "servicing", "webhook", "manual_import"}
)
_PUBLIC_BUSINESS_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &.,:+-]{0,79}$")
_PUBLIC_COMPETITOR_LABEL_PATTERN = re.compile(r"^Competitor ([A-Z]|Other)$")
# Governed refusal vocabulary, shared by Ask Genie
# (``genie.refused_prompt``) and the growth co-pilot
# (``growth_agent.refused_prompt``) so one compliance query spans both
# surfaces. ``unreviewed_criterion`` names the fail-closed
# "criteria are not in the reviewed vocabulary" state: before it existed,
# those refusals were written as ``protected_class``, putting false
# fair-lending findings in the ledger (persona audit, 2026-08-07).
#
# Fair-lending review reads ``protected_class``, ``protected_class_proxy``
# AND ``unreviewed_criterion``: the last is a selection attempt on an
# attribute the reviewed vocabulary does not cover, and that state cannot
# distinguish an invented token from an unlisted health or demographic
# attribute. It is a "not proven safe" record, not a "proven benign" one.
_GOVERNED_REFUSAL_REASONS: frozenset[str] = frozenset(
    {
        "protected_class",
        "protected_class_proxy",
        "instruction_override",
        "pii_request",
        "scope_bypass",
        "out_of_scope",
        "unreviewed_criterion",
        "cross_lender_targeting",
        "unavailable_source",
    }
)

_ALLOWED_OFFER_CODES: frozenset[str] = frozenset(NBO_PRODUCT_LABELS) | {"recapture"}
_OUTREACH_GENERATION_MODES: frozenset[str] = frozenset({"supervisor", "governed_fallback"})
_OUTREACH_DRAFT_ATTRIBUTIONS: frozenset[str] = frozenset(
    _OUTREACH_GENERATION_MODES
    | {f"human_edited_from_{mode}" for mode in _OUTREACH_GENERATION_MODES}
)

_BORROWER_ID_LIST_METADATA_KEYS: frozenset[str] = frozenset(
    {"borrower_ids", "rendered_borrower_ids"}
)


def _metadata_values_for(
    metadata: dict[str, Any],
    keys: frozenset[str] | set[str],
) -> list[tuple[str, Any]]:
    lowered = {key.lower() for key in keys}
    return [(key, value) for key, value in metadata.items() if key.lower() in lowered]


_ALLOWED_RESULT_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "zips",
        # Reviewed `(city, state)` pairs. Named from the canonical module
        # rather than spelled out, so the ledger cannot reject a key the
        # cohort writer accepted -- the write-then-500 shape of PR #191.
        GENIE_CITY_FILTER_KEY,
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
        # Reviewed numeric floors a Genie answer hands to the Lead Queue, and
        # the names of the predicates the queue could NOT replay. Both are
        # machine-generated: the floors are bounded integers, the disclosure
        # is a bounded list of identifier-shaped key names (never values).
        # Splatted from the canonical bounds so a new floor key can never be
        # accepted by the cohort writer and rejected by this ledger.
        *GENIE_NUMERIC_FILTER_KEYS,
        "unreplayable_filters",
    }
)
# Per-key inclusive (minimum, maximum). ``min_rate_spread_bps`` accepts
# negatives; score/equity do not. See backend/schemas/genie_numeric_filters.py.
_RESULT_FILTER_NUMERIC_BOUNDS: Mapping[str, tuple[int, int]] = GENIE_NUMERIC_FILTER_BOUNDS
_MAX_RESULT_FILTER_VALUES = 500
_MAX_RESULT_FILTER_STATES = 56
_MAX_UNREPLAYABLE_RESULT_FILTERS = 12
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
_ACTIVATION_DESTINATION_TYPES: frozenset[str] = frozenset(
    {"salesforce", "crm_cdp", "los_pos", "servicing", "webhook"}
)
_ACTIVATION_STATUSES: frozenset[str] = frozenset(
    {"dry_run", "staged", "delivered", "failed", "cancelled"}
)
_ACTIVATION_DESTINATION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_HOUSEHOLD_DEDUPE_UNITS: frozenset[str] = frozenset({"borrower", "household"})
_HOUSEHOLD_PRIMARY_STRATEGIES: frozenset[str] = frozenset({"highest_opportunity_eligible"})
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
            "to extend it): " + ", ".join(sorted(unexpected_keys))
        )


class AuditMetadataValueViolation(RuntimeError):
    """Raised when a reviewed audit metadata key carries an unsafe value."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        super().__init__(f"Audit metadata field {field!r} failed value policy: {reason}")
