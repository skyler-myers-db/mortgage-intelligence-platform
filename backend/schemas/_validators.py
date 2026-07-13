"""Shared schema validators with no service-layer dependencies."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

_DEFAULT_PUBLIC_LENDER_NAME = "Summit Mortgage"
_PUBLIC_COMPETITOR_REF_RE = re.compile(r"^Competitor ([A-Z]|Other)$")
_PublicLenderNameProvider = Callable[[], str]
_StateFootprintProvider = Callable[[], tuple[Sequence[tuple[str, str]], bool]]
_public_lender_name_provider: _PublicLenderNameProvider | None = None
_state_footprint_provider: _StateFootprintProvider | None = None

_PROTECTED_CLASS_MARKETING_RE = re.compile(
    r"\b(?:age|aged|asian|autis(?:m|tic)|black|blind|buddhist|color|deaf|"
    r"disab(?:ility|led)|wheelchair(?:\s+users?)?|elderly|ethnic(?:ity)?|"
    r"familial status|families? with children|family status|female|gender|handicap(?:ped)?|"
    r"gay|lesbian|bisexual|transgender|non[- ]?binary|queer|gender identity|"
    r"hispanic|latino|male|marital status|military status|muslim|islam(?:ic)?|"
    r"christian|hindu|jewish|jew|sikh|national origin|native american|"
    r"pacific islander|pregnan(?:cy|t)|race|racial|religion|religious|"
    r"senior citizens?|sex|sexual orientation|single (?:mothers?|fathers?|parents?)|"
    r"source of income|veteran|white|woman|women)\b",
    re.IGNORECASE,
)

_PROMPT_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|override|bypass|forget)\b.{0,80}\b"
    r"(?:previous|prior|system|developer|safety|guardrail|policy|rules?|instructions?|prompt)\b|"
    r"\b(?:system|developer)\s+(?:message|prompt)\b|"
    r"\b(?:reveal|show|print|return|expose)\b.{0,60}\b(?:hidden|system|developer)\s+prompt\b|"
    r"\b(?:jailbreak|prompt injection|do anything now|follow these new instructions)\b|"
    r"<\|(?:system|developer|assistant|user)\|>|"
    r"^\s*(?:system|developer)\s*:",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)

_CONFIDENTIAL_OR_INTERNAL_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:api[-_ ]?keys?|client[-_ ]?secrets?|passwords?|passwds?|credentials?|"
        r"access[-_ ]?tokens?|refresh[-_ ]?tokens?|authorization\s*[:=]|"
        r"bearer\s+[A-Za-z0-9._~+/=-]{4,}|(?:databricks|lakebase|oauth|session|auth)"
        r"[-_ ]?tokens?)\b|\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:dapi[a-z0-9]{16,}|AKIA[0-9A-Z]{16}|"
        r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:system|developer|internal|hidden|private)\s+"
        r"(?:prompt|message|instructions?|policy|configuration)\b|"
        r"\b(?:chain[- ]of[- ]thought|hidden reasoning|model scratchpad)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:https?|wss?|ftp|jdbc|databricks|s3|dbfs)://\S+|"
        r"\bwww\.[A-Za-z0-9.-]+(?:/\S*)?|"
        r"\b(?:localhost|127\.0\.0\.1)(?::\d{2,5})?(?:/\S*)?|"
        r"\b[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\."
        r"(?:com|net|org|io|ai|cloud|internal|local|dev)(?::\d{2,5})?(?:/\S*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])/(?:api|oauth2?|serving-endpoints?|secrets?|sql|warehouses?)"
        r"(?:/[A-Za-z0-9._~:@!$&'()*+,;=-]+)+|"
        r"\b(?:serving|model|workspace|internal|private|databricks|lakebase)[-_ ]endpoints?\b",
        re.IGNORECASE,
    ),
)

_TITLECASE_HUMAN_NAME_RE = re.compile(
    r"\b[A-Z][a-z]{1,30}(?:\s+|\s*\|\s*)(?:[A-Z](?:\s+|\s*\|\s*))?"
    r"[A-Z][a-z]{1,30}\b"
)
_LEADING_ANALYTICS_COMMAND_RE = re.compile(
    r"\b(?:Compare|Explain|Find|List|Open|Prioritize|Rank|Review|Show|Target)\s+(?=[A-Z])"
)
_NON_PERSON_TITLECASE_SUFFIXES = frozenset(
    {"borough", "city", "county", "metro", "msa", "parish", "region", "township"}
)
_COMMON_FIRST_NAMES = frozenset(
    {
        "alice",
        "barbara",
        "david",
        "elizabeth",
        "james",
        "jane",
        "jennifer",
        "john",
        "joseph",
        "linda",
        "maria",
        "mary",
        "michael",
        "patricia",
        "richard",
        "robert",
        "sarah",
        "thomas",
        "william",
    }
)
_COMMON_LAST_NAMES = frozenset(
    {
        "anderson",
        "brown",
        "davis",
        "doe",
        "garcia",
        "gonzalez",
        "hernandez",
        "johnson",
        "jones",
        "lee",
        "lopez",
        "martinez",
        "miller",
        "moore",
        "rodriguez",
        "smith",
        "taylor",
        "thomas",
        "williams",
        "wilson",
    }
)

_REVIEWED_NON_PERSON_PHRASES: tuple[str, ...] = (
    "Mortgage Intelligence Platform",
    "Mortgage Growth Agent",
    "Daily Refi Opportunity Brief",
    "Listed-for-Sale Purchase Watch",
    "Competitor Recapture Monitor",
    "High-Equity HELOC Watch",
    "Borrower Dossier Review",
    "Branch Manager Capacity Review",
    "Custom Segment Workflow",
    "Source Freshness Sentinel",
    "Building Permits",
    "Equal Housing Lender",
    "Equal Housing",
    "Offer Orchestrator",
    "Portfolio Builder",
    "Genie Conversation",
    "Databricks Genie",
    "Unity Catalog",
    "Supervisor Agent",
    "Growth Agent",
    "Lead Queue",
    "Borrower Dossier",
    "Mosaic AI",
    "Agent Bricks",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "West Virginia",
    "United States",
)

_PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:loan|lien)\s+ages?\b", re.IGNORECASE),
    re.compile(r"\bages?\s+of\s+(?:the\s+)?(?:loan|lien)s?\b", re.IGNORECASE),
    re.compile(r"\b(?:loan|lien)\s+aging\b", re.IGNORECASE),
    re.compile(
        r"\b(?:white|black)\s+(?:plains|settlement|salmon|center|creek|river|"
        r"falls|rock|oaks?|haven|bluffs?|stone|mountain|hills?|city|county|lake|"
        r"earth|water|sands?|house|hall|bear|fish|hawk|diamond)\b",
        re.IGNORECASE,
    ),
)

_PROTECTED_CLASS_PROXY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmajority[- ]minority\b", re.IGNORECASE),
    re.compile(
        r"\b(?:spanish[- ]speaking|limited[- ]english(?: proficiency)?|"
        r"limited english proficient)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:immigrants?|refugees?)\b", re.IGNORECASE),
    re.compile(r"\bmosques?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:section\s*8(?:\s+housing)?|housing[- ]vouchers?|public[- ]assistance)\b",
        re.IGNORECASE,
    ),
)
_PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bspanish[- ]speaking\s+(?:loan officers?|staff|representatives?|"
        r"support|services?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\blimited[- ]english(?: proficiency)?\s+"
        r"(?:support|services?|materials?|disclosures?|translations?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsection\s*8\s+of\s+(?:the\s+)?(?:document|report|review|policy|appendix)\b",
        re.IGNORECASE,
    ),
)
_PROTECTED_CLASS_PROXY_HARD_TARGETING_RE = re.compile(
    r"\b(?:target|targeting|prioritize|rank|score|segment|filter|exclude|select|"
    r"redline|steer|solicit|prospect|market to|advertise to|campaign to|outreach to|"
    r"contact)\b",
    re.IGNORECASE,
)
_PROTECTED_CLASS_PROXY_POPULATION_RE = re.compile(
    r"\b(?:applicants?|borrowers?|communities|community|customers?|households?|"
    r"homeowners?|leads?|neighbou?rhoods?|postal codes?|prospects?|recipients?|"
    r"residents?|tracts?|zip codes?|zips?)\b",
    re.IGNORECASE,
)

_MECHANICAL_PII_OR_RAW_IDENTIFIER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
    re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}\s+"
        r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|"
        r"ter|terrace|way|pl|place|pkwy|parkway)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:clip_ref|raw[_\s-]?clip|owner[_\s-]?link(?:[_\s-]?id)?|"
        r"owner[_\s-]?name|borrower[_\s-]?name|customer[_\s-]?name|"
        r"prospect[_\s-]?name|street[_\s-]?address|mailing[_\s-]?address)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:clip|owner[_\s-]?(?:link|identifier|id)|borrower[_\s-]?identifier)\b"
        r"\s*[:#=]\s*[A-Za-z0-9_-]{6,}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:account|loan)\s+(?:number|id)\s*[:#=]?\s*[A-Za-z0-9_-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{9,}\b"),
    re.compile(
        r"\[(?:first|last|full)[_\s-]?name\]|\{(?:first|last|full)[_\s-]?name\}|"
        r"\binsert governed\b",
        re.IGNORECASE,
    ),
)

_CONTEXTUAL_HUMAN_NAME_RE = re.compile(
    r"\b(?:call|contact|email|message|text|ask|target|prioritize|dear|hello|hi)\s+"
    r"(?!(?:to|the|a|an|this|that|your|our)\b)[A-Za-z]{2,30}\s+[A-Za-z]{2,30}\b|"
    r"\b[A-Za-z]{2,30}\s+[A-Za-z]{2,30}\s+(?:qualifies?|is the top borrower)\b",
    re.IGNORECASE,
)


def set_public_lender_name_provider(provider: _PublicLenderNameProvider | None) -> None:
    """Register the configured tenant lender without importing runtime settings."""

    global _public_lender_name_provider
    _public_lender_name_provider = provider


def _configured_public_lender_name() -> str:
    if _public_lender_name_provider is None:
        return _DEFAULT_PUBLIC_LENDER_NAME
    try:
        configured = _public_lender_name_provider().strip()
    except Exception:
        return _DEFAULT_PUBLIC_LENDER_NAME
    return configured or _DEFAULT_PUBLIC_LENDER_NAME


def configured_public_lender_name() -> str:
    """Return the public display name for the configured tenant lender."""

    return _configured_public_lender_name()


def set_state_footprint_provider(provider: _StateFootprintProvider | None) -> None:
    """Register the runtime geography resolver without importing services."""

    global _state_footprint_provider
    _state_footprint_provider = provider


def _state_footprint_snapshot() -> tuple[Sequence[tuple[str, str]], bool]:
    if _state_footprint_provider is None:
        return (), True
    return _state_footprint_provider()


def reviewed_geography_labels() -> set[str]:
    """Return lowercased Portfolio Builder geography labels."""

    states, using_fallback = _state_footprint_snapshot()
    if using_fallback:
        return {"all"}
    labels = {"all", *(name.lower() for _code, name in states)}
    labels.add(f"all {len(states)} states")
    return labels


def reviewed_state_codes() -> set[str]:
    """Return currently reviewed two-letter state codes, or empty on fallback."""

    states, using_fallback = _state_footprint_snapshot()
    if using_fallback:
        return set()
    return {code for code, _name in states}


def is_public_lender_ref(value: str | None, *, allow_all: bool = False) -> bool:
    """Return TRUE when ``value`` is from the public-safe lender vocabulary."""

    if value is None:
        return False
    stripped = value.strip()
    if allow_all and stripped == "All":
        return True
    if stripped == _configured_public_lender_name():
        return True
    return bool(_PUBLIC_COMPETITOR_REF_RE.fullmatch(stripped))


def normalize_public_lender_ref(
    value: str | None,
    *,
    allow_all: bool = False,
) -> str | None:
    """Validate a caller-provided lender filter without generalizing raw input."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if is_public_lender_ref(stripped, allow_all=allow_all):
        return stripped
    raise ValueError("target_lender_ref must be a public-safe lender alias")


def assert_no_protected_class_marketing_text(value: str, *, field_name: str) -> str:
    """Reject protected-class language from targeting or outreach copy.

    This is intentionally narrower than a general prose validator. It is used
    only at campaign/outreach decision boundaries, where protected-class
    language must fail closed instead of being silently scrubbed or persisted.
    """

    if contains_protected_class_marketing_text(value):
        raise ValueError(f"{field_name} cannot contain protected-class targeting language")
    return value


def contains_protected_class_marketing_text(value: str) -> bool:
    """Return true for protected-class or obvious proxy targeting language."""

    scannable = str(value)
    for pattern in _PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS:
        scannable = pattern.sub(" ", scannable)
    return bool(
        _PROTECTED_CLASS_MARKETING_RE.search(scannable)
        or contains_protected_class_proxy_marketing_text(scannable)
    )


def contains_protected_class_proxy_marketing_text(value: str) -> bool:
    """Detect protected-class proxies only when used for people or targeting.

    The proxy terms are not intrinsically unsafe. Language-access support,
    branch-service coverage, and document section references remain usable;
    borrower/geography selection and explicit targeting fail closed.
    """

    text = str(value)
    for safe_pattern in _PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
        text = safe_pattern.sub(" ", text)
    for pattern in _PROTECTED_CLASS_PROXY_PATTERNS:
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 120) : match.end() + 120]
            if _PROTECTED_CLASS_PROXY_HARD_TARGETING_RE.search(window):
                return True
            if _PROTECTED_CLASS_PROXY_POPULATION_RE.search(window):
                return True
    return False


def contains_prompt_injection_text(value: str) -> bool:
    """Return true for instruction-override language at governed text boundaries."""

    return bool(_PROMPT_INJECTION_RE.search(str(value)))


def contains_confidential_or_internal_text(value: str) -> bool:
    """Detect secrets, credentials, internal instructions, URLs, and endpoints."""

    text = str(value)
    return any(pattern.search(text) for pattern in _CONFIDENTIAL_OR_INTERNAL_TEXT_PATTERNS)


def contains_human_name_shape(
    value: str,
    *,
    allowed_phrases: Sequence[str] = (),
    include_titlecase: bool = True,
) -> bool:
    """Detect title-case names and reviewed common lowercase first/last pairs.

    General two-word lowercase prose is not treated as an identity. The common
    pair vocabulary closes the audited ``john smith`` class without turning
    ordinary mortgage phrases into false positives.
    """

    text = _remove_reviewed_non_person_phrases(str(value), allowed_phrases=allowed_phrases)
    text = _LEADING_ANALYTICS_COMMAND_RE.sub(" ", text)
    if include_titlecase and any(
        re.split(r"\s+|\|", match.group(0))[-1].casefold()
        not in _NON_PERSON_TITLECASE_SUFFIXES
        for match in _TITLECASE_HUMAN_NAME_RE.finditer(text)
    ):
        return True
    if _CONTEXTUAL_HUMAN_NAME_RE.search(text):
        return True
    words = re.findall(r"[A-Za-z]{2,30}", text.casefold())
    return any(
        first in _COMMON_FIRST_NAMES and last in _COMMON_LAST_NAMES
        for first, last in zip(words, words[1:], strict=False)
    )


def contains_mechanical_pii_or_raw_identifier(value: str) -> bool:
    """Detect mechanical PII, unresolved placeholders, and raw source identifiers."""

    text = str(value)
    return any(pattern.search(text) for pattern in _MECHANICAL_PII_OR_RAW_IDENTIFIER_PATTERNS)


def contains_unsafe_ai_text(value: str, *, include_titlecase: bool = True) -> bool:
    """Shared fail-closed guard for model-authored or model-directed prose."""

    text = str(value)
    return (
        contains_mechanical_pii_or_raw_identifier(text)
        or contains_protected_class_marketing_text(text)
        or contains_prompt_injection_text(text)
        or contains_confidential_or_internal_text(text)
        or contains_human_name_shape(text, include_titlecase=include_titlecase)
    )


def _remove_reviewed_non_person_phrases(
    value: str,
    *,
    allowed_phrases: Sequence[str],
) -> str:
    phrases = {
        *_REVIEWED_NON_PERSON_PHRASES,
        _configured_public_lender_name(),
        *allowed_phrases,
    }
    cleaned = value
    for phrase in sorted((item.strip() for item in phrases if item.strip()), key=len, reverse=True):
        cleaned = re.sub(re.escape(phrase), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def contains_contextual_human_name(value: str) -> bool:
    """Detect name-shaped text in contexts where a person is being addressed.

    General lowercase two-word prose is intentionally not classified as a
    name. The governed free-text boundaries use this alongside mechanical PII
    checks and title-case detection to catch case-normalized names such as
    ``call john smith`` without treating ordinary sentences as identities.
    """

    return bool(_CONTEXTUAL_HUMAN_NAME_RE.search(str(value)))
