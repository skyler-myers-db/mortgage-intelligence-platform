"""Audit store -- Lakebase-backed append-only ledger.

Slice-5 cutover: replaces the in-memory list with a Postgres-backed
writer against ``mip_app.action_audit``. The router contract is
unchanged -- ``write(...)`` returns an ``AuditEvent`` and ``list(limit)``
returns events in descending event-time order.

No silent fallback: when Lakebase is unreachable, ``LakebaseAuditStore``
methods raise ``LakebaseError`` (from ``backend.services.lakebase``).
The audit router catches that and surfaces HTTP 503; Slice 6 adds the
retry / circuit-breaker layer so transient network hiccups don't panic
the UI.

Actor attribution: governance §4 requires the real authenticated user,
not ``"service-user"``. Databricks Apps forwards the workspace user in
``X-Forwarded-Email``; ``resolve_actor(request)`` extracts it and falls
back to ``settings.default_actor`` with a logged warning so operators
can spot dev/test paths in production logs. The router chain is:
routers read ``Request`` -> call ``resolve_actor`` -> pass to
``audit_store.write(actor=...)``.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from fastapi import Request

from backend.config.settings import settings
from backend.schemas.audit import AuditEvent
from backend.schemas.common import (
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
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.pii_redaction import (
    mask_cotality_id,
    normalize_public_lender_ref,
    scrub_free_text,
)
from backend.services.scoring import NBO_PRODUCT_LABELS

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Fallback-identity counter. Slice-RBAC follow-up: when the Databricks
# Apps edge does NOT forward ``X-Forwarded-Email`` (local dev, a broken
# proxy, or a code path that didn't plumb the ``Request`` through), we
# fall back to ``settings.default_actor``. Governance wants that event
# to be observable -- every fallback is potentially an un-attributed
# audit row, and a non-zero count in production is a regression signal
# worth paging on.
#
# Implementation is a plain module-level integer incremented under a
# (non-threadsafe, best-effort) counter. FastAPI workers are separate
# processes; the number is process-local like the other counters in
# ``backend/services/observability.py``. Tests exercise the counter via
# ``_reset_fallback_counter_for_tests`` + ``get_fallback_identity_count``.
# ----------------------------------------------------------------------


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
        "owner_name",
        "owner_full_name",
        "display_name",
        "street_address",
        "mailing_street",
        "borrower_name",
        "email",
        "phone",
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
#     opportunity_score, confidence, segment_codes, recommended_offer
#   backend/api/outreach.py::draft_outreach
#     channel, offer_code
#   backend/api/outreach.py::approve_outreach
#     approval_id, offer_code, borrower_id, request_id, draft_body,
#     rationale, bulk_id, bulk_rationale
#   backend/api/outreach.py::reject_outreach
#     approval_id, offer_code, borrower_id, request_id, rationale, rationale_code
#   backend/api/leads.py::list_leads_ranked
#     rendered_borrower_ids, portfolio_id, segment, segment_mode, limit
#   backend/api/offers.py::recommend_offer
#     offer_code, confidence, thresholds_applied
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
        # Genie control-layer actions
        "action_type",
        "conversation_id",
        "message_id",
        "question_hash",
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
        # Offers
        "thresholds_applied",
    }
)

_FREE_TEXT_METADATA_KEYS: frozenset[str] = frozenset(
    {"draft_body", "rationale", "bulk_rationale", "reason", "notes"}
)
_HUMAN_NAME_OR_PLACEHOLDER_PATTERN = re.compile(
    r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b|"
    r"\[(?:first|last|full)[_\s-]?[Nn]ame\]|\{(?:first|last|full)[_\s-]?[Nn]ame\}"
)

_BORROWER_ID_METADATA_KEYS: frozenset[str] = frozenset({"borrower_id"})

_OPAQUE_ID_METADATA_KEYS: frozenset[str] = frozenset(
    {"approval_id", "bulk_id", "campaign_id", "request_id", "assignment_id", "disposition_id"}
)
_CAMPAIGN_LABEL_METADATA_KEYS: frozenset[str] = frozenset({"variant_name"})
_INTERNAL_STAFF_EMAIL_METADATA_KEYS: frozenset[str] = frozenset(
    {"assigned_to_email", "assigned_by", "lo_email"}
)
_INTERNAL_STAFF_EMAIL_LIST_METADATA_KEYS: frozenset[str] = frozenset({"lo_emails"})
_SALES_STRATEGIES: frozenset[str] = frozenset({"manual", "round_robin", "score_balanced"})
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

_ALLOWED_OFFER_CODES: frozenset[str] = frozenset(NBO_PRODUCT_LABELS) | {"recapture"}

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
        "states",
        "county",
        "counties",
        "segment_codes",
        "segment_mode",
        "target_lender_ref",
        "borrower_ids",
        "portfolio_criteria",
        "source",
    }
)
_MAX_RESULT_FILTER_VALUES = 500
_MAX_RESULT_FILTER_STATES = 56

_ALLOWED_SEGMENT_CODES: frozenset[str] = frozenset(
    {"itm", "listed", "permit", "investor", "equity", "retention"}
)


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


def _assert_no_pii(metadata: dict[str, Any]) -> None:
    """Raise ``AuditPIIError`` if ``metadata`` has any denylist keys.

    Top-level only: callers nest structured payload under
    ``payload_json``, but no router currently stuffs borrower names into
    nested objects. If that changes we deepen the check; for now a
    top-level scan is the least-surprising contract.
    """
    if not metadata:
        return
    lowered = _metadata_keys_deep(metadata)
    hits = lowered & _PII_DENYLIST_KEYS
    if hits:
        raise AuditPIIError(sorted(hits))


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
                "must be Summit Mortgage, Competitor A-Z, Competitor Other, or All",
            ) from exc
    for _, portfolio_criteria in _metadata_values_for(metadata, {"portfolio_criteria"}):
        if portfolio_criteria is None:
            continue
        _assert_portfolio_criteria_value_policy(portfolio_criteria)
    for _, result_filters in _metadata_values_for(metadata, {"result_filters"}):
        if result_filters is None:
            continue
        _assert_result_filters_value_policy(result_filters)
    for field, value in _metadata_values_for(metadata, {"strategy"}):
        if value is not None and str(value) not in _SALES_STRATEGIES:
            raise AuditMetadataValueViolation(field, "must be a governed sales assignment strategy")
    for field, value in _metadata_values_for(metadata, {"outcome"}):
        if value is not None and str(value) not in _SALES_DISPOSITION_OUTCOMES:
            raise AuditMetadataValueViolation(field, "must be a governed call disposition outcome")
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
    log.warning(
        "audit_store.resolve_actor: no X-Forwarded-Email header -- "
        "falling back to settings.default_actor=%s",
        settings.default_actor,
        extra={
            "event": "identity_fallback",
            "default_actor": settings.default_actor,
            "fallback_count": _FALLBACK_IDENTITY_COUNT,
        },
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
# In-memory store -- kept for unit tests and as a reference impl. The
# production factory always returns ``LakebaseAuditStore``; tests that
# want a fast, no-network audit surface instantiate this directly and
# inject via FastAPI dependency_overrides.
# ----------------------------------------------------------------------


class InMemoryAuditStore:
    """Deterministic in-memory audit ledger. Tests only."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

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
    ) -> AuditEvent:
        payload = payload_json or {}
        # Governance: denylist PII keys at write time, not read time.
        # Applies equally to the in-memory store so unit tests exercise
        # the guard without needing Lakebase.
        metadata = _sanitize_metadata({**payload, "action": action})
        payload = {k: v for k, v in metadata.items() if k != "action"}
        _assert_no_pii(metadata)
        # R6-20: allowlist complement to the denylist. Fails loudly on
        # any key that isn't explicitly reviewed in
        # ``_ALLOWED_METADATA_KEYS``.
        _assert_allowlisted(metadata)
        _assert_public_safe_values(metadata)
        safe_event_type = _coerce_event_type(event_type, action)
        (
            safe_action,
            safe_entity_type,
            safe_entity_id,
            safe_event_type,
            safe_subject_segment,
            safe_request_id,
        ) = _validate_top_level_audit_columns(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=safe_event_type,
            subject_segment=subject_segment,
            request_id=request_id,
        )
        event = AuditEvent(
            event_id=f"evt-{uuid4().hex[:12]}",
            actor=actor,
            action=safe_action,
            entity_type=safe_entity_type,
            entity_id=safe_entity_id,
            payload_json=payload,
            evidence_ids=evidence_ids or [],
            created_at=datetime.now(UTC).isoformat(),
            event_type=safe_event_type,
            subject_clip=(
                mask_cotality_id("clip", subject_clip)
                if subject_clip is not None
                else None
            ),
            subject_segment=safe_subject_segment,
            request_id=safe_request_id,
        )
        self._events.append(event)
        return event

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
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEvent]:
        filtered = self._events
        if actor:
            filtered = [e for e in filtered if e.actor == actor]
        if action:
            filtered = [e for e in filtered if e.action == action]
        if entity_id:
            filtered = [e for e in filtered if e.entity_id == entity_id]
        if borrower_id:
            filtered = [
                e for e in filtered
                if e.entity_id == borrower_id
                or str((e.payload_json or {}).get("borrower_id") or "") == borrower_id
            ]
        if subject_clip:
            filtered = [e for e in filtered if e.subject_clip == subject_clip]
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if since:
            filtered = [
                e for e in filtered
                if datetime.fromisoformat(e.created_at.replace("Z", "+00:00")) >= since
            ]
        if until:
            filtered = [
                e for e in filtered
                if datetime.fromisoformat(e.created_at.replace("Z", "+00:00")) <= until
            ]
        return list(reversed(filtered[-limit:]))


# ----------------------------------------------------------------------
# Lakebase-backed store -- production path.
# ----------------------------------------------------------------------


_INSERT_SQL = """
INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    subject_clip, subject_segment, request_id,
    evidence_ids, metadata
) VALUES (
    %(event_type)s, %(actor_email)s, %(entity_type)s, %(entity_id)s,
    %(subject_clip)s, %(subject_segment)s, %(request_id)s,
    %(evidence_ids)s, %(metadata)s::jsonb
)
RETURNING audit_id, event_at
"""

_SELECT_SQL_TEMPLATE = """
SELECT audit_id, event_type, actor_email, entity_type, entity_id,
       subject_clip, subject_segment, request_id,
       evidence_ids, metadata, event_at
FROM mip_app.action_audit
{where_clause}
ORDER BY event_at DESC
LIMIT %(limit)s
"""


def _build_insert_params(
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = payload_json or {}
    metadata = _sanitize_metadata({**payload, "action": action})
    payload = {k: v for k, v in metadata.items() if k != "action"}
    _assert_no_pii(metadata)
    _assert_allowlisted(metadata)
    _assert_public_safe_values(metadata)
    safe_event_type = _coerce_event_type(event_type, action)
    (
        safe_action,
        safe_entity_type,
        safe_entity_id,
        safe_event_type,
        safe_subject_segment,
        safe_request_id,
    ) = _validate_top_level_audit_columns(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=safe_event_type,
        subject_segment=subject_segment,
        request_id=request_id,
    )
    safe_subject_clip = (
        mask_cotality_id("clip", subject_clip)
        if subject_clip is not None
        else None
    )
    params: dict[str, Any] = {
        "event_type": safe_event_type,
        "actor_email": actor,
        "entity_type": safe_entity_type,
        "entity_id": safe_entity_id,
        "subject_clip": safe_subject_clip,
        "subject_segment": safe_subject_segment,
        "request_id": safe_request_id,
        "evidence_ids": list(evidence_ids or []),
        "metadata": json.dumps(metadata),
    }
    _ = safe_action
    return payload, params


def _audit_event_from_row(
    row: dict[str, Any],
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    payload_json: dict[str, Any],
    evidence_ids: list[str] | None = None,
    event_type: str | None = None,
    subject_clip: str | None = None,
    subject_segment: str | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    event_at = row["event_at"]
    created_at = event_at.isoformat() if hasattr(event_at, "isoformat") else str(event_at)
    return AuditEvent(
        event_id=str(row["audit_id"]),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload_json,
        evidence_ids=evidence_ids or [],
        created_at=created_at,
        event_type=_coerce_event_type(event_type, action),
        subject_clip=subject_clip,
        subject_segment=subject_segment,
        request_id=request_id,
    )


def write_audit_event_in_transaction(
    conn: Any,
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
) -> AuditEvent:
    """Insert one audit row using an already-open Lakebase transaction."""
    payload, params = _build_insert_params(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload_json,
        evidence_ids=evidence_ids,
        event_type=event_type,
        subject_clip=subject_clip,
        subject_segment=subject_segment,
        request_id=request_id,
    )
    row = conn.execute(_INSERT_SQL, params).fetchone()
    if row is None:
        raise RuntimeError("Lakebase INSERT returned no row")
    return _audit_event_from_row(
        dict(row),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload,
        evidence_ids=evidence_ids,
        event_type=event_type,
        subject_clip=params["subject_clip"],
        subject_segment=subject_segment,
        request_id=request_id,
    )


class LakebaseAuditStore:
    """Audit store backed by the Lakebase ``mip_app.action_audit`` table.

    Each ``write`` opens a short transaction, INSERTs one row, and
    returns the AuditEvent with the server-assigned UUID + timestamp.
    ``list`` selects ORDER BY event_at DESC LIMIT N.
    """

    def __init__(self, client: LakebaseClient | None = None) -> None:
        # Accept an injected client for unit testability; default to
        # the process-singleton.
        self._client = client or get_lakebase_client()

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
    ) -> AuditEvent:
        payload, params = _build_insert_params(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json,
            evidence_ids=evidence_ids,
            event_type=event_type,
            subject_clip=subject_clip,
            subject_segment=subject_segment,
            request_id=request_id,
        )
        row = self._client.fetchone(_INSERT_SQL, params)
        if row is None:
            # Should be impossible with RETURNING, but guard anyway --
            # the Protocol promises an AuditEvent, not None.
            raise RuntimeError("Lakebase INSERT returned no row")
        return _audit_event_from_row(
            row,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload,
            evidence_ids=list(evidence_ids or []),
            event_type=params["event_type"],
            subject_clip=params["subject_clip"],
            subject_segment=subject_segment,
            request_id=request_id,
        )

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
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if actor:
            clauses.append("actor_email = %(actor)s")
            params["actor"] = actor
        if entity_id:
            clauses.append("entity_id = %(entity_id)s")
            params["entity_id"] = entity_id
        if borrower_id:
            clauses.append("(entity_id = %(borrower_id)s OR metadata->>'borrower_id' = %(borrower_id)s)")
            params["borrower_id"] = borrower_id
        if subject_clip:
            clauses.append("subject_clip = %(subject_clip)s")
            params["subject_clip"] = mask_cotality_id("clip", subject_clip)
        if event_type:
            clauses.append("event_type = %(event_type)s")
            params["event_type"] = event_type
        if action:
            clauses.append("metadata->>'action' = %(action)s")
            params["action"] = action
        if since:
            clauses.append("event_at >= %(since)s")
            params["since"] = since
        if until:
            clauses.append("event_at <= %(until)s")
            params["until"] = until
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = _SELECT_SQL_TEMPLATE.format(where_clause=where_clause)
        rows = self._client.fetchall(sql, params, limit=limit)
        out: list[AuditEvent] = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                # psycopg usually returns JSONB as dict, but fall back.
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            action = metadata.pop("action", row["event_type"])
            out.append(
                AuditEvent(
                    event_id=str(row["audit_id"]),
                    actor=row["actor_email"],
                    action=action,
                    entity_type=row.get("entity_type") or "",
                    entity_id=row.get("entity_id") or "",
                    payload_json=metadata,
                    evidence_ids=list(row.get("evidence_ids") or []),
                    created_at=row["event_at"].isoformat(),
                    event_type=row["event_type"],
                    subject_clip=(
                        mask_cotality_id("clip", row.get("subject_clip"))
                        if row.get("subject_clip") is not None
                        else None
                    ),
                    subject_segment=row.get("subject_segment"),
                    request_id=row.get("request_id"),
                )
            )
        return out


# ----------------------------------------------------------------------
# Factory -- single choke point for the FastAPI dependency graph.
# ----------------------------------------------------------------------


_AUDIT_STORE: AuditStore | None = None


def get_audit_store() -> AuditStore:
    """Lazy process-singleton audit store.

    Production path constructs a ``LakebaseAuditStore`` backed by the
    Lakebase client singleton. Tests override this factory in
    ``tests/conftest.py`` with an ``InMemoryAuditStore`` so unit tests
    never touch Postgres.
    """
    global _AUDIT_STORE
    if _AUDIT_STORE is None:
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
