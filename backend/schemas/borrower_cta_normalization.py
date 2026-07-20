"""Narrow safe-affirmative normalization for borrower CTA validation."""

import re

from backend.schemas.borrower_cta_actions import BORROWER_CTA_ACTION_RE_FRAGMENT

_SAFE_AFFIRMATIVE_IDIOM_RE = re.compile(
    rf"\b(?:(?:do\s+not|don['’]t|never)\s+(?:ever\s+)?|"
    rf"(?:we\s+)?(?:recommend|advise)\s+(?:that\s+)?you\s+not(?:\s+to)?\s+|"
    rf"(?:you\s+are\s+)?instructed\s+not\s+to\s+)"
    rf"(?:(?:hesitate|forget|wait|fail)\s+to|be\s+afraid\s+to)\s+"
    rf"(?P<action>{BORROWER_CTA_ACTION_RE_FRAGMENT})\b",
    re.IGNORECASE,
)
_SAFE_COMMA_DIRECTIVE_RE = re.compile(
    rf"\b(?:(?:do\s+not|don['’]t)\s+(?:wait|delay|worry)|"
    rf"no\s+need\s+to\s+(?:wait|delay|worry))\s*,\s*(?:please\s+)?"
    rf"(?P<action>{BORROWER_CTA_ACTION_RE_FRAGMENT})\b",
    re.IGNORECASE,
)
_SAFE_CONDITIONAL_DIRECTIVE_RE = re.compile(
    rf"\b(?:do\s+not|don['’]t)\s+(?P<action>{BORROWER_CTA_ACTION_RE_FRAGMENT}"
    rf"(?:\s+(?:us|me))?)(?=\s+unless\s+(?:you|borrowers?)\s+"
    r"(?:want|would\s+like|choose|prefer|are\s+ready)\b)",
    re.IGNORECASE,
)
_SAFE_AUTONOMY_BEFORE_OPTION_RE = re.compile(
    r"\bno\s+(?:response|action|reply|contact)\s+(?:is\s+)?(?:required|needed)" r"\s*,?\s+but\b",
    re.IGNORECASE,
)
_SAFE_LENDER_INVITATION_NAME_SCAN_RE = re.compile(
    r"\b(?:(?:may|can|could|would)\s+)?(?:we|our\s+team)\s+"
    r"(?:invite|ask|encourage|welcome|request)\s+"
    r"(?:you|borrowers?|mortgage\s+holders?|homeowners?|applicants?|clients?|"
    r"recipients?|customers?)\b",
    re.IGNORECASE,
)
_SAFE_ORGANIZATIONAL_IDENTITY_PREFIX_RE = re.compile(
    r"\b(?P<prefix>send|route|forward|deliver|address|email|message|give|for|"
    r"attention|attn|fao|cc|bcc|to)(?P<separator>\s+|\s*:\s*)"
    r"(?P<identity>compliance|operations|servicing|support|underwriting)\b",
    re.IGNORECASE,
)


def normalize_safe_affirmative_cta(value: str) -> str:
    """Remove only reviewed idiomatic negation around a genuine option."""

    normalized = _SAFE_AFFIRMATIVE_IDIOM_RE.sub(lambda match: str(match.group("action")), value)
    normalized = _SAFE_COMMA_DIRECTIVE_RE.sub(lambda match: str(match.group("action")), normalized)
    normalized = _SAFE_CONDITIONAL_DIRECTIVE_RE.sub(
        lambda match: str(match.group("action")), normalized
    )
    return _SAFE_AUTONOMY_BEFORE_OPTION_RE.sub(" ", normalized)


def normalize_safe_lender_invitation_for_name_scan(value: str) -> str:
    """Remove a governed lender invitation prefix before human-name scanning."""

    normalized = _SAFE_LENDER_INVITATION_NAME_SCAN_RE.sub(" ", value)
    return _SAFE_ORGANIZATIONAL_IDENTITY_PREFIX_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('separator')}"
            f"{match.group('identity').casefold()}"
        ),
        normalized,
    )
