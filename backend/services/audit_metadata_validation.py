"""Audit metadata validators: denylist, allowlist, and per-field value policy.

Single responsibility: inspect a metadata payload against the reviewed
vocabulary in ``audit_metadata_policy`` and raise when it falls outside --
deep PII key/value scans, the allowlist gate, the top-level audit column
checks, the per-field value policies (portfolio criteria, result filters,
decision inputs, string lists), and the free-text scrub that runs before
any of them.

The 500-line public-value assertion for allowlisted keys lives beside this
module in ``audit_metadata_public_values``; ``build_safe_audit_metadata``
in ``backend.services.audit_store`` is the one entry point that runs the
whole chain in order.
"""
from __future__ import annotations

import re
from typing import Any

from backend.schemas._validators_person_names import (
    contains_human_name_shape,
    titlecase_pair_is_non_person,
)
from backend.schemas.borrower_copy_claims import (
    contains_unsupported_borrower_qualification_claim,
)
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.borrower_cta_evidence import contains_borrower_cta_contradiction
from backend.schemas.common import (
    contains_pii_marker,
    contains_protected_class_marketing_text,
    validate_public_audit_action,
    validate_public_audit_entity_type,
    validate_public_audit_event_type,
    validate_public_audit_identifier_or_none,
    validate_public_audit_subject_segment,
    validate_public_borrower_id,
    validate_public_opaque_id,
)
from backend.schemas.genie_geo_filters import (
    CITY_STATE_PAIR_RE,
    GENIE_CITY_FILTER_KEY,
    MAX_CITY_FILTER_VALUES,
)
from backend.schemas.genie_numeric_filters import (
    is_reviewed_numeric_floor,
    numeric_filter_range_text,
)
from backend.services.audit_metadata_policy import (
    _ALLOWED_FUNNEL_STAGES,
    _ALLOWED_METADATA_KEYS,
    _ALLOWED_RESULT_FILTER_KEYS,
    _ALLOWED_SEGMENT_CODES,
    _AUDIT_HUMAN_IDENTITY_DIRECTIVE_RE,
    _BORROWER_DRAFT_METADATA_KEYS,
    _DECISION_INPUT_KEYS,
    _FREE_TEXT_METADATA_KEYS,
    _GROWTH_AGENT_REVIEWED_NAME_WORD_ALLOWLIST,
    _HUMAN_NAME_OR_PLACEHOLDER_PATTERN,
    _INTERNAL_STAFF_EMAIL_LIST_METADATA_KEYS,
    _INTERNAL_STAFF_EMAIL_METADATA_KEYS,
    _MAX_RESULT_FILTER_STATES,
    _MAX_RESULT_FILTER_VALUES,
    _MAX_UNREPLAYABLE_RESULT_FILTERS,
    _NESTED_METADATA_KEYS_WITH_OWN_POLICY,
    _PII_DENYLIST_KEYS,
    _RESULT_FILTER_NUMERIC_BOUNDS,
    AuditMetadataValueViolation,
    AuditMetadataViolation,
    AuditPIIError,
)
from backend.services.pii_redaction import (
    normalize_public_lender_ref,
    scrub_free_text,
)


def _metadata_keys_deep(value: Any) -> set[str]:
    if isinstance(value, dict):
        out: set[str] = {str(k).lower() for k in value}
        for nested in value.values():
            out.update(_metadata_keys_deep(nested))
        return out
    if isinstance(value, list):
        out = set()
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
    if (
        contains_pii_marker(text)
        or scrub_free_text(text) != text
        or contains_borrower_copy_contextual_name(text)
    ):
        hits.add(path)
    return hits


def _growth_agent_reviewed_text_contains_pii(value: Any) -> bool:
    if value is None or isinstance(value, bool | int | float):
        return False
    text = str(value)
    return bool(
        contains_pii_marker(text)
        or scrub_free_text(text) != text
        or contains_borrower_copy_contextual_name(text)
        or _growth_agent_reviewed_text_contains_human_name(text)
    )


def _growth_agent_reviewed_text_contains_human_name(text: str) -> bool:
    for match in _HUMAN_NAME_OR_PLACEHOLDER_PATTERN.finditer(text):
        candidate = match.group(0)
        if candidate.startswith(("[", "{")):
            return True
        if titlecase_pair_is_non_person(candidate):
            continue
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
        safe_request_id = validate_public_opaque_id(request_id) if request_id is not None else None
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
            raise AuditMetadataValueViolation(
                field, "contains values outside the reviewed vocabulary"
            )


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
    if GENIE_CITY_FILTER_KEY in value:
        # Pattern-checked against the SAME regex the cohort writer used,
        # so a pair one accepts cannot be the pair the other refuses.
        _assert_string_list(
            f"result_filters.{GENIE_CITY_FILTER_KEY}",
            value[GENIE_CITY_FILTER_KEY],
            pattern=CITY_STATE_PAIR_RE.pattern,
            max_items=MAX_CITY_FILTER_VALUES,
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
    if "approval_status" in value and str(value["approval_status"]) not in {
        "pending",
        "approved",
        "rejected",
        "hold",
    }:
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
            raise AuditMetadataValueViolation(
                "result_filters.aged_days", "must be 1 to 90"
            ) from exc
        if aged_days < 1 or aged_days > 90:
            raise AuditMetadataValueViolation("result_filters.aged_days", "must be 1 to 90")
    for numeric_field in _RESULT_FILTER_NUMERIC_BOUNDS:
        if numeric_field not in value:
            continue
        if not is_reviewed_numeric_floor(numeric_field, value[numeric_field]):
            raise AuditMetadataValueViolation(
                f"result_filters.{numeric_field}",
                f"must be an integer {numeric_filter_range_text(numeric_field)}",
            )
    if "unreplayable_filters" in value:
        _assert_string_list(
            "result_filters.unreplayable_filters",
            value["unreplayable_filters"],
            pattern=r"^[a-z0-9_]{1,64}$",
            max_items=_MAX_UNREPLAYABLE_RESULT_FILTERS,
        )


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
    for field in (
        "rate_spread_bps",
        "equity_pct",
        "heloc_propensity_score",
        "refi_propensity_score",
    ):
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
            clean_text = str(cleaned[key])
            if contains_protected_class_marketing_text(clean_text):
                raise AuditMetadataValueViolation(
                    key,
                    "must not contain protected-class targeting language",
                )
            if contains_unsupported_borrower_qualification_claim(clean_text):
                raise AuditMetadataValueViolation(
                    key,
                    "must not contain unsupported borrower-facing claims",
                )
            if (
                contains_borrower_copy_contextual_name(clean_text)
                or (
                    key == "notes"
                    and (
                        contains_human_name_shape(clean_text)
                        or any(
                            hit.group(0).startswith(("[", "{"))
                            for hit in _HUMAN_NAME_OR_PLACEHOLDER_PATTERN.finditer(
                                clean_text
                            )
                        )
                    )
                )
                or (
                    key not in _BORROWER_DRAFT_METADATA_KEYS
                    and _AUDIT_HUMAN_IDENTITY_DIRECTIVE_RE.search(clean_text)
                )
            ):
                raise AuditMetadataValueViolation(
                    key,
                    "must not contain human-name-shaped text or unresolved placeholders",
                )
            if contains_borrower_cta_contradiction(clean_text):
                raise AuditMetadataValueViolation(
                    key,
                    "must not contain a contact action that contradicts consent or response handling",
                )
    return cleaned
