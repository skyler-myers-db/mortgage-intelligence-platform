"""Mechanical text hazards and the shared fail-closed unsafe-AI-text guard.

Detects prompt-injection language, secrets/internal references, and mechanical
PII or raw source identifiers, and composes them with the protected-class and
name-shape detectors into :func:`contains_unsafe_ai_text`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from backend.schemas._validators_person_names import contains_human_name_shape
from backend.schemas._validators_protected_class import contains_protected_class_marketing_text

_PROMPT_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|override|bypass|forget)\b.{0,80}\b"
    r"(?:previous|prior|system|developer|safety|guardrails?|policy|rules?|instructions?|prompts?)\b|"
    r"\b(?:system|developer)\s+(?:message|prompt)\b|"
    r"\b(?:reveal|show|print|return|expose)\b.{0,60}\b(?:hidden|system|developer)\s+prompt\b|"
    r"\b(?:jailbreak|prompt injection|do anything now|follow these new instructions)\b|"
    # Persona overrides (2026-08-07 cross-surface audit). Role-scoped so
    # ordinary copy ("act as your trusted advisor") never matches on the
    # stricter marketing surfaces that share this pattern.
    r"\bpretend\s+to\s+be\b|\broleplay\s+as\b|\bfrom\s+now\s+on,?\s+you\b|"
    r"\byou\s+are\s+now\s+(?:a|an|the|my|unrestricted|free|able|no\s+longer)\b|"
    r"\bact\s+as\s+(?:a|an)\s+(?:system|admin|administrator|developer|databricks|"
    r"root|superuser|unrestricted|unfiltered|jailbroken)\b|"
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
    # Bare identifier-column NAMES. `owner_link` is deliberately NOT here: it
    # is the product's own glossary term (CLAUDE.md: "Owner Link = Cotality
    # mastered owner/entity relationship identifier"), so it appears in governed
    # gold prose and as a column header. Blocking the bare name made the Genie
    # result grid unshowable: measured live on paychex 2026-08-11, 322 of the
    # 330 distinct `why_now` values carry "Owner Link ties N related
    # properties...", and every `SELECT ... owner_link_id ...` blocked on the
    # HEADER alone, because `_visible_text_values` flattens Mapping keys too.
    # The block surfaced as `unsafe_field: "table_rows"` and looked random only
    # because Genie re-plans the SQL per turn -- a top-10 answer never projects
    # those columns, a full breakdown always does.
    #
    # It stays blocked WITH A VALUE by the pattern below (`owner_link: ABC123456`),
    # which is the shape that actually leaks an identifier. The genuinely
    # PII-bearing column names -- borrower_name, owner_name, street_address --
    # are untouched.
    re.compile(
        r"\b(?:clip_ref|raw[_\s-]?clip|"
        r"owner[_\s-]?name|borrower[_\s-]?name|customer[_\s-]?name|"
        r"prospect[_\s-]?name|street[_\s-]?address|mailing[_\s-]?address)\b",
        re.IGNORECASE,
    ),
    re.compile(
        # `owner_link_id` needs the optional `_id` tail explicitly: the bare
        # `link` alternative stops at "link" and the `\b` then fails against
        # "_id", so `owner_link_id = QX7T2M9P44` escaped once `owner_link` was
        # removed from the bare-name pattern above.
        r"\b(?:clip|owner[_\s-]?(?:link(?:[_\s-]?id)?|identifier|id)|"
        r"borrower[_\s-]?identifier)\b"
        r"\s*[:#=]\s*[A-Za-z0-9_-]{6,}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:account|loan)\s+(?:number|id)\s*[:#=]?\s*[A-Za-z0-9_-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    ),
    # Long digit runs are raw-identifier shaped (CLIP, account ids). The
    # fractional tail of a high-precision decimal is NOT: a governed AVG that
    # Genie did not round ("167.66792784271334") produced a 14-digit run and
    # blocked the whole answer, which hit every unrounded average/ratio in the
    # product (live persona audit 2026-08-07, VP-Lending flagship question).
    # Excluding a run preceded by "<digit>." keeps every standalone identifier
    # run flagged, including the integer part of a decimal.
    re.compile(r"(?<!\d\.)\b\d{9,}\b"),
    re.compile(
        r"\[(?:first|last|full)[_\s-]?name\]|\{(?:first|last|full)[_\s-]?name\}|"
        r"\binsert governed\b",
        re.IGNORECASE,
    ),
)


def contains_prompt_injection_text(value: str) -> bool:
    """Return true for instruction-override language at governed text boundaries."""

    return bool(_PROMPT_INJECTION_RE.search(str(value)))


def contains_confidential_or_internal_text(value: str) -> bool:
    """Detect secrets, credentials, internal instructions, URLs, and endpoints."""

    text = str(value)
    return any(pattern.search(text) for pattern in _CONFIDENTIAL_OR_INTERNAL_TEXT_PATTERNS)


def contains_mechanical_pii_or_raw_identifier(value: str) -> bool:
    """Detect mechanical PII, unresolved placeholders, and raw source identifiers."""

    text = str(value)
    return any(pattern.search(text) for pattern in _MECHANICAL_PII_OR_RAW_IDENTIFIER_PATTERNS)


# Two live sets (name-shape and protected-class) plus the single-value tuples
# the place resolver's admission gate compiles while it loads. Eviction only
# costs a recompile, but 16 keeps a load from churning the two hot sets out.
@lru_cache(maxsize=16)
def _governed_phrase_pattern(phrases: tuple[str, ...]) -> re.Pattern[str] | None:
    """One alternation for the whole governed set, cached per set.

    Word-boundary anchored on purpose: a governed value may only ever erase
    ITSELF as a complete token run. ``Lone Tree`` cannot reach ``Lone Treeman``
    and a hypothetical governed ``Black`` cannot reach ``blackballed``.
    Alternatives are longest-first because Python alternation is ordered, so a
    multiword value is consumed before any single-word prefix of it.
    """

    cleaned = sorted({item.strip() for item in phrases if item.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(
        r"(?<![A-Za-z0-9])(?:"
        + "|".join(re.escape(phrase) for phrase in cleaned)
        + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


@lru_cache(maxsize=64)
def _guard_context_pattern(context: str, *, trailing: bool) -> re.Pattern[str]:
    """Match a guard context sitting immediately beside an occurrence.

    Separator-agnostic on purpose. The protected-class scanner folds EVERY
    non-alphanumeric run to a single space before matching, and compiles its
    own multi-word terms with ``[- ]``, so ``native-hawaiian`` is the same
    phrase to it as ``native hawaiian``. A guard that only knew about a literal
    space was therefore defeated by ordinary orthography — an adversarial
    review measured 90 evasions across 9 separators, and hyphenated is the
    canonical spelling of the ethnonyms involved.

    Written as a search over the neighbouring text rather than a lookbehind
    precisely because the separator run is variable width.
    """

    if trailing:
        return re.compile(r"^[^A-Za-z0-9]+" + re.escape(context) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(
        r"(?:^|[^A-Za-z0-9])" + re.escape(context) + r"[^A-Za-z0-9]+$", re.IGNORECASE
    )


def _occurrence_is_guarded(
    text: str,
    start: int,
    end: int,
    left_contexts: tuple[str, ...],
    right_contexts: tuple[str, ...],
) -> bool:
    """True when erasing this occurrence would damage the term beside it."""

    return any(
        _guard_context_pattern(context, trailing=False).search(text[:start])
        for context in left_contexts
    ) or any(
        _guard_context_pattern(context, trailing=True).search(text[end:])
        for context in right_contexts
    )


def mask_governed_phrases(
    value: str,
    phrases: Sequence[str],
    guards: Sequence[tuple[str, Sequence[str], Sequence[str]]] = (),
) -> str:
    """Blank whole-token occurrences of governed non-person phrases.

    ``guards`` names, per phrase, the neighbouring contexts in which the
    occurrence must be left alone because erasing it would damage the protected
    term beside it. Callers that pass none get the plain whole-run erase.
    """

    text = str(value)
    pattern = _governed_phrase_pattern(tuple(phrases))
    if pattern is None:
        return text
    if not guards:
        return pattern.sub(" ", text)
    by_phrase = {
        phrase.casefold(): (tuple(left), tuple(right))
        for phrase, left, right in guards
        if phrase.strip()
    }

    def _erase(match: re.Match[str]) -> str:
        left, right = by_phrase.get(" ".join(match.group(0).split()).casefold(), ((), ()))
        if (left or right) and _occurrence_is_guarded(
            text, match.start(), match.end(), left, right
        ):
            return match.group(0)
        return " "

    return pattern.sub(_erase, text)


def contains_unsafe_ai_text(
    value: str,
    *,
    include_titlecase: bool = True,
    assume_reviewed_read_only_analytics: bool = False,
    name_shape_value: str | None = None,
    name_shape_allowed_phrases: Sequence[str] = (),
    name_shape_guards: Sequence[tuple[str, Sequence[str], Sequence[str]]] = (),
    name_shape_sentence_initial_place_terms: Sequence[str] = (),
    protected_class_allowed_phrases: Sequence[str] = (),
    protected_class_guards: Sequence[tuple[str, Sequence[str], Sequence[str]]] = (),
) -> bool:
    """Shared fail-closed guard for model-authored or model-directed prose.

    ``assume_reviewed_read_only_analytics`` relaxes only the campaign
    audience-formation criterion machine (see
    :func:`contains_protected_class_marketing_text`); it never disables the
    PII, injection, confidential, name-shape, or direct protected-class
    detectors.

    Every relaxation below is scoped to ONE detector, and the scoping is the
    whole safety argument: a governed value can only ever blind the single
    scanner it was proven to false-positive against, because no other scanner
    is handed the rewritten copy.

    ``name_shape_value`` replaces the copy given to
    :func:`contains_human_name_shape` only -- the Genie policy passes its
    ``City, ST`` geography strip here. ``name_shape_allowed_phrases`` masks
    governed place values out of that same copy for the same reason.
    ``name_shape_sentence_initial_place_terms`` reaches that same scanner and
    nothing else: it names the places before which a capitalized opening word
    is orthography, not an identity ("The Washington cities with the most
    in-the-money borrowers ..." is not a person named "The Washington").
    Captured live 2026-08-12: that pair, plus "Which Washington" in two
    follow-up questions, is what withheld the whole governed narrative for
    "Which Washington cities have the most in-the-money borrowers?". The place
    is never consumed — it still reaches every scanner here.

    ``protected_class_allowed_phrases`` masks governed place values out of the
    copy given to :func:`contains_protected_class_marketing_text` only. This is
    the dangerous one -- it is the fair-lending scanner -- so the resolver that
    supplies it admits a value only after proving that masking the value cannot
    disarm a must-block probe -- see
    ``genie_place_dimension._disarms_a_protected_class_canary``.

    Callers that pass none of the three (campaign and outreach surfaces) are
    bit-for-bit unchanged.
    """

    text = str(value)
    name_shape_text = text if name_shape_value is None else str(name_shape_value)
    return (
        contains_mechanical_pii_or_raw_identifier(text)
        or contains_protected_class_marketing_text(
            mask_governed_phrases(text, protected_class_allowed_phrases, protected_class_guards),
            assume_reviewed_read_only_analytics=assume_reviewed_read_only_analytics,
        )
        or contains_prompt_injection_text(text)
        or contains_confidential_or_internal_text(text)
        or contains_human_name_shape(
            mask_governed_phrases(
                name_shape_text, name_shape_allowed_phrases, name_shape_guards
            ),
            include_titlecase=include_titlecase,
            sentence_initial_place_terms=name_shape_sentence_initial_place_terms,
        )
    )
