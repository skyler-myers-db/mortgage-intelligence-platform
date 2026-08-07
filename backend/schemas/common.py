"""Shared validation helpers and common API schema fragments."""

import re

from pydantic import BaseModel, Field

from backend.schemas._validators_person_names import contains_human_name_shape
from backend.schemas._validators_protected_class import contains_protected_class_marketing_text
from backend.schemas._validators_tenant import configured_public_lender_name
from backend.schemas._validators_unsafe_text import (
    contains_mechanical_pii_or_raw_identifier,
    contains_prompt_injection_text,
)

PUBLIC_BORROWER_ID_PATTERN = re.compile(r"^B-[A-Za-z0-9][A-Za-z0-9_-]{0,126}$")
PUBLIC_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
PUBLIC_SERVER_ID_PATTERN = re.compile(
    r"^(?:auto-[a-f0-9]{32}|genie-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|"
    r"genie-action-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$"
)
PUBLIC_AUDIT_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_,.:+-]{0,127}$")
PUBLIC_AUDIT_ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
PUBLIC_AUDIT_EVENT_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
PUBLIC_AUDIT_ENTITY_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PUBLIC_AUDIT_SEGMENT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
PUBLIC_CAMPAIGN_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,:+-]{0,63}$")
_HUMAN_NAME_SHAPE_PATTERN = re.compile(r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PUBLIC_INTERNAL_STAFF_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9._%+-]+@(entrada\.ai|summit\.example)$",
    re.IGNORECASE,
)
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)")
_STREET_ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+\s+"
    r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|way)\b",
    re.IGNORECASE,
)
_UNRESOLVED_PLACEHOLDER_PATTERN = re.compile(
    r"\[(?:first|last|full)[_\s-]?name\]|\{(?:first|last|full)[_\s-]?name\}|"
    r"\binsert governed\b|\braw[_\s-]?clip\b|\bowner[_\s-]?name\b|"
    r"\bstreet[_\s-]?address\b|\bmailing[_\s-]?address\b",
    re.IGNORECASE,
)
_PUBLIC_CAMPAIGN_LABEL_PHRASE_ALLOWLIST: tuple[str, ...] = (
    "Summit Mortgage",
    "Equal Housing",
    "New York",
    "New Jersey",
    "New Mexico",
    "North Carolina",
    "North Dakota",
    "South Carolina",
    "South Dakota",
    "Rhode Island",
    "West Virginia",
    "United States",
)
_PUBLIC_CAMPAIGN_LABEL_WORD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "Campaign",
        "Cash",
        "Current",
        "Customer",
        "Equity",
        "Former",
        "Home",
        "HELOC",
        "Investor",
        "Loan",
        "Mortgage",
        "Out",
        "Pilot",
        "Recapture",
        "Refi",
        "Refinance",
        "Retention",
        "Review",
        "Watch",
        "Brief",
        "Opportunity",
        "Segment",
        "Growth",
        "Daily",
        "Weekly",
    }
)
_PUBLIC_LABEL_SUFFIXES = frozenset({"brief", "campaign", "monitor", "watch"})


def validate_public_borrower_id(value: str) -> str:
    """Return a normalized public borrower id or raise ``ValueError``.

    Raw Cotality identifiers, numeric IDs, and blank values must not be accepted
    on state-changing API contracts. Public borrower identifiers are generated
    by the app boundary and always use the ``B-`` prefix.
    """

    borrower_id = str(value).strip()
    if not PUBLIC_BORROWER_ID_PATTERN.fullmatch(borrower_id):
        raise ValueError("borrower_id must be an app-scoped public borrower id")
    return borrower_id


def contains_pii_marker(value: str) -> bool:
    """Return True for obvious PII-like text we never persist in audit keys.

    These patterns are intentionally small and mechanical. They catch the
    high-risk operator mistakes seen in audit metadata (email, SSN, and
    US-phone-shaped strings) without trying to classify borrower names.
    """

    text = str(value)
    return bool(
        _EMAIL_PATTERN.search(text) or _SSN_PATTERN.search(text) or _PHONE_PATTERN.search(text)
    )


def validate_internal_staff_email(value: str) -> str:
    """Return a normalized internal staff email or raise ``ValueError``.

    Staff identity is operational metadata, not borrower contact data. Keep it
    constrained to the two demo/workspace domains so assignment/disposition
    contracts cannot accidentally persist customer emails.
    """

    email = str(value).strip().lower()
    if not PUBLIC_INTERNAL_STAFF_EMAIL_PATTERN.fullmatch(email):
        raise ValueError("staff email must be an approved internal demo/workspace email")
    return email


def validate_public_opaque_id(value: str) -> str:
    """Return a public-safe opaque id for audit/request correlation.

    Accepted values are browser UUIDs or server-issued prefixes. This keeps
    idempotency and bulk-correlation keys opaque while preventing phone,
    SSN, email, or "helpful" human text from entering the audit ledger.
    """

    opaque_id = str(value).strip()
    if PUBLIC_UUID_PATTERN.fullmatch(opaque_id) or PUBLIC_SERVER_ID_PATTERN.fullmatch(opaque_id):
        return opaque_id
    if contains_pii_marker(opaque_id):
        raise ValueError("id must not contain email, SSN, or phone-shaped text")
    raise ValueError("id must be a UUID or governed server-issued opaque id")


def validate_public_campaign_label(value: str, *, field_name: str = "variant_name") -> str:
    """Return a short campaign label safe for URLs, UI chips, and audit metadata."""

    label = re.sub(r"\s+", " ", str(value).strip())
    if not label:
        raise ValueError(f"{field_name} must not be blank")
    if (
        contains_pii_marker(label)
        or contains_mechanical_pii_or_raw_identifier(label)
        or _STREET_ADDRESS_PATTERN.search(label)
        or _UNRESOLVED_PLACEHOLDER_PATTERN.search(label)
        or contains_prompt_injection_text(label)
        or contains_protected_class_marketing_text(label)
    ):
        raise ValueError(f"{field_name} must not contain PII, raw identifiers, or placeholders")
    if not PUBLIC_CAMPAIGN_LABEL_PATTERN.fullmatch(label):
        raise ValueError(f"{field_name} must be a public-safe campaign label")
    name_scan = label
    for phrase in (*_PUBLIC_CAMPAIGN_LABEL_PHRASE_ALLOWLIST, configured_public_lender_name()):
        name_scan = name_scan.replace(phrase, "")
    if contains_human_name_shape(name_scan, include_titlecase=False):
        raise ValueError(f"{field_name} must not contain human-name-shaped text")
    for match in _HUMAN_NAME_SHAPE_PATTERN.finditer(name_scan):
        words = [part.strip() for part in match.group(0).split() if part.strip()]
        if words and all(word in _PUBLIC_CAMPAIGN_LABEL_WORD_ALLOWLIST for word in words):
            continue
        raise ValueError(f"{field_name} must not contain human-name-shaped text")
    words = re.findall(r"[A-Za-z]{2,30}", name_scan)
    if len(words) == 3 and words[-1].casefold() in _PUBLIC_LABEL_SUFFIXES:
        allowed_words = {word.casefold() for word in _PUBLIC_CAMPAIGN_LABEL_WORD_ALLOWLIST}
        if all(word.casefold() not in allowed_words for word in words[:2]):
            raise ValueError(f"{field_name} must not contain human-name-shaped text")
    return label


def validate_public_free_comment(
    value: str,
    *,
    max_len: int = 280,
    field_name: str = "comment",
) -> str:
    """Validate a short public-safe free-text comment (e.g. Genie feedback).

    Reuses the same PII / human-name / street-address / placeholder guards the
    campaign-label validator applies, but allows normal sentence punctuation
    and a caller-supplied length cap. Rejects (raises ``ValueError``) rather
    than echoing the offending text, so a 422 never reflects PII back.
    """

    text = re.sub(r"\s+", " ", str(value).strip())
    if not text:
        raise ValueError(f"{field_name} must not be blank")
    if len(text) > max_len:
        raise ValueError(f"{field_name} must be at most {max_len} characters")
    if (
        contains_pii_marker(text)
        or contains_mechanical_pii_or_raw_identifier(text)
        or _STREET_ADDRESS_PATTERN.search(text)
        or _UNRESOLVED_PLACEHOLDER_PATTERN.search(text)
        or contains_prompt_injection_text(text)
        or contains_protected_class_marketing_text(text)
    ):
        raise ValueError(f"{field_name} must not contain PII, raw identifiers, or placeholders")
    name_scan = text
    for phrase in (*_PUBLIC_CAMPAIGN_LABEL_PHRASE_ALLOWLIST, configured_public_lender_name()):
        name_scan = name_scan.replace(phrase, "")
    if _HUMAN_NAME_SHAPE_PATTERN.search(name_scan) or contains_human_name_shape(
        name_scan,
        include_titlecase=False,
    ):
        raise ValueError(f"{field_name} must not contain human-name-shaped text")
    return text


def validate_no_human_name_shape(value: str, *, field_name: str) -> str:
    """Reject human-name-shaped text while preserving later redaction inputs.

    Saved workspace drafts retain the existing defense-in-depth redaction of
    phone, email, SSN, and address text. Names cannot be reliably redacted, so
    they are rejected before the draft reaches that persistence layer.
    """

    text = str(value).strip()
    name_scan = text
    for pattern in (_EMAIL_PATTERN, _SSN_PATTERN, _PHONE_PATTERN, _STREET_ADDRESS_PATTERN):
        name_scan = pattern.sub(" [redacted] ", name_scan)
    name_scan = _UNRESOLVED_PLACEHOLDER_PATTERN.sub(" [redacted] ", name_scan)
    for phrase in (*_PUBLIC_CAMPAIGN_LABEL_PHRASE_ALLOWLIST, configured_public_lender_name()):
        name_scan = name_scan.replace(phrase, "")
    if _HUMAN_NAME_SHAPE_PATTERN.search(name_scan) or contains_human_name_shape(
        name_scan,
        include_titlecase=False,
    ):
        raise ValueError(f"{field_name} must not contain human-name-shaped text")
    return text


def validate_public_audit_text(value: str, *, field_name: str = "value") -> str:
    """Validate short top-level audit identifiers such as entity labels.

    This is not for draft/rationale prose. It only protects top-level audit
    columns that are query keys and should contain public-safe slugs, public
    borrower ids, UUIDs, or short labels.
    """

    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must not be blank")
    if validate_public_audit_identifier_or_none(text) is not None:
        return text
    raise ValueError(f"{field_name} must be a public-safe audit identifier")


def validate_public_audit_identifier_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if (
        PUBLIC_BORROWER_ID_PATTERN.fullmatch(text)
        or PUBLIC_UUID_PATTERN.fullmatch(text)
        or PUBLIC_SERVER_ID_PATTERN.fullmatch(text)
    ):
        return text
    if contains_pii_marker(text):
        raise ValueError("audit identifier must not contain email, SSN, or phone-shaped text")
    if PUBLIC_AUDIT_TEXT_PATTERN.fullmatch(text):
        return text
    raise ValueError("audit identifier must be a public-safe slug, borrower id, or opaque id")


def validate_public_audit_action(value: str) -> str:
    action = str(value).strip()
    if contains_pii_marker(action) or not PUBLIC_AUDIT_ACTION_PATTERN.fullmatch(action):
        raise ValueError("action must be a lowercase machine verb")
    return action


def validate_public_audit_event_type(value: str) -> str:
    event_type = str(value).strip()
    if contains_pii_marker(event_type) or not PUBLIC_AUDIT_EVENT_TYPE_PATTERN.fullmatch(event_type):
        raise ValueError("event_type must be an uppercase machine token")
    return event_type


def validate_public_audit_entity_type(value: str) -> str:
    entity_type = str(value).strip()
    if contains_pii_marker(entity_type) or not PUBLIC_AUDIT_ENTITY_TYPE_PATTERN.fullmatch(
        entity_type
    ):
        raise ValueError("entity_type must be a lowercase machine slug")
    return entity_type


def validate_public_audit_subject_segment(value: str) -> str:
    segment = str(value).strip()
    if contains_pii_marker(segment):
        raise ValueError("subject_segment must not contain email, SSN, or phone-shaped text")
    parts = [part.strip() for part in segment.split(",") if part.strip()]
    if not parts or any(not PUBLIC_AUDIT_SEGMENT_PATTERN.fullmatch(part) for part in parts):
        raise ValueError("subject_segment must be comma-separated segment codes")
    return ",".join(parts)


class EvidenceEvent(BaseModel):
    evidence_id: str
    source_product: str
    source_table: str
    signal_type: str
    signal_value: str
    display_text: str
    confidence: float = Field(ge=0, le=1)
    timestamp: str
