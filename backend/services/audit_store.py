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
from backend.schemas.common import validate_public_borrower_id
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.pii_redaction import (
    mask_cotality_id,
    normalize_public_lender_ref,
    scrub_free_text,
)

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
#     approval_id, offer_code, borrower_id, request_id, draft_body
#   backend/api/outreach.py::reject_outreach
#     approval_id, offer_code, borrower_id, request_id, rationale
#   backend/api/leads.py::list_leads_ranked
#     rendered_borrower_ids, portfolio_id, segment, segment_mode, limit
#   backend/api/offers.py::recommend_offer
#     offer_code, confidence, thresholds_applied
#   backend/api/admin.py::set_rules
#     overrides
#
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
        "reason",
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
        "target_lender_ref",
        "portfolio_criteria",
        "cohort_id",
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
        # Admin rules override
        "overrides",
    }
)

_FREE_TEXT_METADATA_KEYS: frozenset[str] = frozenset(
    {"draft_body", "rationale", "reason"}
)

_BORROWER_ID_METADATA_KEYS: frozenset[str] = frozenset({"borrower_id"})

_BORROWER_ID_LIST_METADATA_KEYS: frozenset[str] = frozenset(
    {"borrower_ids", "rendered_borrower_ids"}
)

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
    for field in _BORROWER_ID_METADATA_KEYS:
        value = metadata.get(field)
        if value is not None:
            try:
                validate_public_borrower_id(str(value))
            except ValueError as exc:
                raise AuditMetadataValueViolation(
                    field,
                    "must be an app-scoped public borrower id",
                ) from exc
    for field in _BORROWER_ID_LIST_METADATA_KEYS:
        value = metadata.get(field)
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
    target = metadata.get("target_lender_ref")
    if target is not None:
        try:
            normalize_public_lender_ref(str(target), allow_all=True)
        except ValueError as exc:
            raise AuditMetadataValueViolation(
                "target_lender_ref",
                "must be Summit Mortgage, Competitor A-Z, Competitor Other, or All",
            ) from exc
    portfolio_criteria = metadata.get("portfolio_criteria")
    if portfolio_criteria is not None:
        _assert_portfolio_criteria_value_policy(portfolio_criteria)
    result_filters = metadata.get("result_filters")
    if result_filters is not None:
        _assert_result_filters_value_policy(result_filters)


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

    def list(self, limit: int = 50) -> list[AuditEvent]: ...


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
        metadata = _sanitize_metadata({"action": action, **payload})
        payload = {k: v for k, v in metadata.items() if k != "action"}
        _assert_no_pii(metadata)
        # R6-20: allowlist complement to the denylist. Fails loudly on
        # any key that isn't explicitly reviewed in
        # ``_ALLOWED_METADATA_KEYS``.
        _assert_allowlisted(metadata)
        _assert_public_safe_values(metadata)
        event = AuditEvent(
            event_id=f"evt-{uuid4().hex[:12]}",
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload,
            evidence_ids=evidence_ids or [],
            created_at=datetime.now(UTC).isoformat(),
            event_type=_coerce_event_type(event_type, action),
            subject_clip=(
                mask_cotality_id("clip", subject_clip)
                if subject_clip is not None
                else None
            ),
            subject_segment=subject_segment,
            request_id=request_id,
        )
        self._events.append(event)
        return event

    def list(self, limit: int = 50) -> list[AuditEvent]:
        return list(reversed(self._events[-limit:]))


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

_SELECT_SQL = """
SELECT audit_id, event_type, actor_email, entity_type, entity_id,
       subject_clip, subject_segment, request_id,
       evidence_ids, metadata, event_at
FROM mip_app.action_audit
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
    metadata = _sanitize_metadata({"action": action, **payload})
    payload = {k: v for k, v in metadata.items() if k != "action"}
    _assert_no_pii(metadata)
    _assert_allowlisted(metadata)
    _assert_public_safe_values(metadata)
    safe_subject_clip = (
        mask_cotality_id("clip", subject_clip)
        if subject_clip is not None
        else None
    )
    params: dict[str, Any] = {
        "event_type": _coerce_event_type(event_type, action),
        "actor_email": actor,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "subject_clip": safe_subject_clip,
        "subject_segment": subject_segment,
        "request_id": request_id,
        "evidence_ids": list(evidence_ids or []),
        "metadata": json.dumps(metadata),
    }
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

    def list(self, limit: int = 50) -> list[AuditEvent]:
        rows = self._client.fetchall(_SELECT_SQL, {"limit": limit}, limit=limit)
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
