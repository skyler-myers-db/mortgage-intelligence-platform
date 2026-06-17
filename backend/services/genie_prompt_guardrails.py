"""Pre-Genie prompt guardrails for governed mortgage analytics."""
from __future__ import annotations

import re

from backend.services.state_footprint import get_state_footprint_resolver

_INSTRUCTION_OVERRIDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:ignore|disregard|override|bypass|forget)\b.{0,80}"
        r"\b(?:previous|prior|system|developer|safety|guardrail|policy|policies|rules|instructions|prompt)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:system|developer)\s*:\s*(?:you\s+may\s+now|ignore|disregard|override|bypass|print|reveal|show)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:you\s+may\s+now\s+answer\s+anything|answer\s+anything\s+now|jailbreak|developer\s+mode)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:print|reveal|show|dump)\b.{0,60}"
        r"\b(?:system|developer)\b.{0,40}\b(?:prompt|instructions|message)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)

_PII_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:full\s+names?|names?|owner\s+names?)\b.{0,80}\b(?:borrowers?|owners?|properties)\b|"
        r"\b(?:borrowers?|owners?|properties)\b.{0,80}\b(?:full\s+names?|names?|owner\s+names?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:email(?:\s+addresses?)?|phone(?:\s+numbers?)?|ssn|social\s+security)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:street|mailing|situs|site|property|home|exact)\s+address(?:es)?\b|"
        r"\b(?:exact|full|raw)?\s*(?:property|home|street|mailing|situs|site)\s+address(?:es)?\b|"
        r"\baddress(?:es)?\b.{0,80}\b(?:borrowers?|owners?|properties)\b|"
        r"\b(?:borrowers?|owners?|properties)\b.{0,80}\baddress(?:es)?\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:raw|exact)\s+(?:servicer|lender)\s+(?:string|name|value)\b|"
        r"\b(?:servicer|lender)\s+string\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:at|on|for)\s+(?:\d{1,6}\s+)?[A-Z][A-Za-z0-9'.-]*"
        r"(?:\s+[A-Z][A-Za-z0-9'.-]*){0,4}\s+"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way)\b",
        re.IGNORECASE,
    ),
)

_SOURCE_GAP_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:permits?|building\s+permits?|permit-filed|permit\s+signals?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:fico|credit\s+scores?|credit[-\s]+bureau|tri[-\s]+merge|vantage\s*score)\b",
        re.IGNORECASE,
    ),
)

_SCOPE_BYPASS_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:list|show|dump|enumerate)\b.{0,60}\b(?:tables?|schemas?|catalogs?|workspace\s+metadata)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:show\s+tables|information_schema|system\.|cotality_mortgage_data\.|hive_metastore\.|mip\.raw\.|mip\.silver\.)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:drop|create|alter|insert|update|delete|merge|truncate|grant|revoke|use|set|exec|execute)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bunion\s+select\b|\bxp_cmdshell\b", re.IGNORECASE),
)

_OFF_TOPIC_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:weather|haiku|poem|poetry|recipe|sports|trivia|celebrity|politics)\b",
        re.IGNORECASE,
    ),
)

_CROSS_LENDER_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:wells\s+fargo|chase|rocket\s+mortgage|quicken\s+loans|lendingtree)\b"
        r".{0,100}\b(?:borrowers?|customers?|customer\s+list|pipeline|leads?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:borrowers?|customers?|customer\s+list|pipeline|leads?)\b"
        r".{0,100}\b(?:wells\s+fargo|chase|rocket\s+mortgage|quicken\s+loans|lendingtree)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:third[-\s]+party|competitor|lead[-\s]+vendor)[-\s]+"
        r"(?:owned\s+)?(?:borrowers?|customers?|leads?|pipeline)\b",
        re.IGNORECASE,
    ),
)

_US_STATE_NAMES: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_AMBIGUOUS_STATE_CODES: frozenset[str] = frozenset({"HI", "ID", "IN", "ME", "OH", "OK", "OR"})
_GEOGRAPHY_STATE_HINTS: dict[str, tuple[str, str]] = {
    "atlanta": ("Atlanta, Georgia", "GA"),
    "georgia": ("Georgia", "GA"),
    "boston": ("Boston, Massachusetts", "MA"),
    "massachusetts": ("Massachusetts", "MA"),
    "toronto": ("Toronto, Canada", "Canada"),
    "canada": ("Canada", "Canada"),
    "puerto rico": ("Puerto Rico", "PR"),
    "guam": ("Guam", "GU"),
}


def _ambiguous_state_code_match_is_contextual(
    question: str, match: re.Match[str]
) -> bool:
    """Treat common-word USPS codes as states only with geography context."""

    before = question[: match.start()]
    after = question[match.end():]
    has_geo_preface = bool(
        re.search(
            r"(?:^|[\s(,/;:-])(?:in|for|from|state|states|market|coverage|geography|geo)[:\s]+$",
            before,
            flags=re.IGNORECASE,
        )
    )
    if not has_geo_preface and not before.rstrip().endswith(("(", "[")):
        return False

    next_word = re.match(r"[\s,;:.-]+([A-Za-z]+)", after)
    if next_word is None:
        return True
    return next_word.group(1).lower() in {"is", "are", "has", "have", "with", "and"}


def _prompt_pattern_match(
    question: str,
    patterns: tuple[re.Pattern[str], ...],
) -> str | None:
    normalized = re.sub(r"\s+", " ", question).strip()
    for pattern in patterns:
        match = pattern.search(normalized)
        if match:
            return match.group(0)[:120]
    return None


def instruction_override_prompt_match(question: str) -> str | None:
    return _prompt_pattern_match(question, _INSTRUCTION_OVERRIDE_PATTERNS)


def pii_prompt_match(question: str) -> str | None:
    return _prompt_pattern_match(question, _PII_PROMPT_PATTERNS)


def source_gap_prompt_match(question: str) -> str | None:
    return _prompt_pattern_match(question, _SOURCE_GAP_PROMPT_PATTERNS)


def scope_bypass_prompt_match(question: str) -> str | None:
    return _prompt_pattern_match(question, _SCOPE_BYPASS_PROMPT_PATTERNS)


def off_topic_prompt_match(question: str) -> str | None:
    return _prompt_pattern_match(question, _OFF_TOPIC_PROMPT_PATTERNS)


def cross_lender_prompt_match(question: str) -> str | None:
    return _prompt_pattern_match(question, _CROSS_LENDER_PROMPT_PATTERNS)


def _mentioned_states(question: str) -> list[tuple[str, str]]:
    q = question.lower()
    matched: list[tuple[str, str]] = []
    for name, code in _US_STATE_NAMES.items():
        name_pattern = r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
        code_pattern = r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])"
        code_match = False
        exact_code_matches = tuple(re.finditer(code_pattern, question, flags=re.IGNORECASE))
        if exact_code_matches:
            code_match = code not in _AMBIGUOUS_STATE_CODES or any(
                _ambiguous_state_code_match_is_contextual(question, match)
                for match in exact_code_matches
            )
        if re.search(name_pattern, q) or code_match:
            matched.append((name.title(), code))
    return matched


def footprint_metadata_gap_match(question: str) -> tuple[str, str] | None:
    matched = _mentioned_states(question)
    if not matched:
        return None
    resolver = get_state_footprint_resolver()
    if not resolver.using_fallback():
        return None
    return matched[0]


def outside_footprint_match(question: str) -> tuple[str, str, list[str]] | None:
    matched = _mentioned_states(question)
    footprint_codes = get_state_footprint_resolver().state_codes()
    allowed_codes = set(footprint_codes)
    for name, code in matched:
        if code not in allowed_codes:
            return (name, code, footprint_codes)
    if matched:
        return None
    q = question.lower()
    for token, (label, code) in _GEOGRAPHY_STATE_HINTS.items():
        if (
            re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", q)
            and code not in allowed_codes
        ):
            return (label, code, footprint_codes)
    return None
