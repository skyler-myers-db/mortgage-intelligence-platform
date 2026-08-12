"""Protected-class marketing detection for campaign and analytics boundaries.

The reviewed vocabulary regexes live in
``backend.schemas._validators_protected_class_patterns``; this module owns the
scanning machinery (confusable folding, audience-claim grammar, windowed proxy
matching, criterion state) and the fail-closed public detectors.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Literal

from backend.schemas._validators_protected_class_patterns import (
    GEOGRAPHIC_COMPOSITION_AUDIENCE_FORMATION_RE,
    PROTECTED_AGE_CITIZENSHIP_MARKETING_RE,
    PROTECTED_CLASS_GEOGRAPHIC_COMPOSITION_PATTERNS,
    PROTECTED_CLASS_MARKETING_RE,
    PROTECTED_CLASS_PROXY_HARD_TARGETING_RE,
    PROTECTED_CLASS_PROXY_PATTERNS,
    PROTECTED_CLASS_PROXY_POPULATION_RE,
    PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS,
    PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS,
    PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE,
    PROTECTED_HEALTH_GOVERNANCE_INTENT_RE,
    PROTECTED_HEALTH_SELECTION_CONTEXT_RE,
    PROTECTED_HEALTH_STATUS_MARKETING_RE,
    PROTECTED_HEALTH_TERM_MARKETING_RE,
)
from backend.schemas.marketing_safety_terms import (
    PROTECTED_NATIONAL_ORIGIN_RE,
    mask_protected_health_safe_contexts,
)
from backend.schemas.marketing_selection_criteria import (
    contains_unreviewed_selection_criterion,
    is_reviewed_campaign_audience_description_text,
    is_reviewed_campaign_audience_summary_text,
    is_reviewed_read_only_analytics_text,
)
from backend.schemas.marketing_text_normalization import (
    ascii_confusable_folds,
    in_word_leet_folds,
)
from backend.schemas.protected_relationships import PROTECTED_RELIGION_FAMILIAL_RELATION_RE

# Governed refusal reasons this module can report. Both are fail-closed
# rejections; only ``protected_class`` is a fair-lending finding.
ProtectedClassRefusalReason = Literal["protected_class", "unreviewed_criterion"]

_MARKETING_SYMBOL_CONFUSABLES: dict[int, str] = {
    ord("!"): "i",
    ord("$"): "s",
    ord("@"): "a",
    ord("|"): "l",
    ord("¢"): "c",
    ord("£"): "l",
    ord("€"): "e",
    ord("¥"): "y",
}
_AUDIENCE_OUTCOME_CLAIM_RE = re.compile(
    r"(?:^|[.!?;:\n])\s*(?P<audience>[^.!?;:\n]+?)\s+"
    r"(?:may|might|could|can|should)"
    r"(?:[^A-Za-z0-9.!?;]+[A-Za-z-]+ly){0,3}"
    r"(?:[^A-Za-z0-9.!?;]+be[^A-Za-z0-9.!?;]+able"
    r"[^A-Za-z0-9.!?;]+to)?"
    r"[^A-Za-z0-9.!?;]+(?:benefit|qualify|"
    r"be[^A-Za-z0-9.!?;]+eligible)"
    r"(?:[^A-Za-z0-9.!?;]+[A-Za-z-]+ly){0,3}"
    r"(?=\s*(?:from\b|for\b|today\b|[;,!?.]|$))",
    re.IGNORECASE,
)
_REVIEWED_GENERIC_AUDIENCE_RE = re.compile(
    r"(?:today[,]?\s+)?(?:you|your\s+(?:property\s+portfolio|portfolio|profile|loan|"
    r"mortgage\s+profile|household)|(?:a|an|this|that)\s+(?:borrower|homeowner|"
    r"applicant|customer|household|profile)|(?:(?:all|many|some|eligible|qualified|"
    r"reviewed|current|prospective|these|those|our|your)\s+)?(?:borrowers?|"
    r"homeowners?|applicants?|customers?|households?|people|persons?|recipients?|"
    r"property\s+portfolios?|loan\s+profiles?))",
    re.IGNORECASE,
)
_STRUCTURAL_AUDIENCE_KEYWORD_PATTERNS = tuple(
    (
        keyword,
        re.compile(
            r"(?<![A-Za-z])"
            + r"[^A-Za-z]*".join(re.escape(letter) for letter in keyword)
            + r"(?![A-Za-z])",
            re.IGNORECASE,
        ),
    )
    for keyword in (
        "eligible",
        "benefit",
        "qualify",
        "should",
        "might",
        "could",
        "may",
        "can",
    )
)


@lru_cache(maxsize=512)
def _mints_governed_term(candidate: str) -> bool:
    """True when a bare number's folded spelling is itself a governed term.

    The only reason a digit run is ever left unfolded. Consulted with the
    folded candidate alone -- ``lao``, ``laos``, ``l``, ``e`` -- so it asks a
    presence question of every direct bank and never a contextual one: at this
    point there is no sentence to take context from, and the question is
    whether these letters COULD be a governed term, not whether this sentence
    uses one that way.

    Kept in one place so the reserved set follows the vocabulary. Widening any
    bank automatically stops the corresponding number from folding, rather than
    leaving a stale literal list behind.
    """

    return any(
        pattern.search(candidate)
        for pattern in (
            PROTECTED_CLASS_MARKETING_RE,
            PROTECTED_AGE_CITIZENSHIP_MARKETING_RE,
            PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE,
            PROTECTED_RELIGION_FAMILIAL_RELATION_RE,
            PROTECTED_HEALTH_TERM_MARKETING_RE,
            PROTECTED_HEALTH_STATUS_MARKETING_RE,
            PROTECTED_NATIONAL_ORIGIN_RE,
        )
    )


def _structural_audience_scan_variants(value: str) -> set[str]:
    """Canonicalize only governed audience-claim keywords, preserving sentences."""

    in_word_symbols: list[str] = []
    for index, char in enumerate(value):
        replacement = _MARKETING_SYMBOL_CONFUSABLES.get(ord(char))
        previous_is_ascii_letter = (
            index > 0 and value[index - 1].isascii() and value[index - 1].isalpha()
        )
        next_is_ascii_letter = (
            index + 1 < len(value) and value[index + 1].isascii() and value[index + 1].isalpha()
        )
        in_word_symbols.append(
            replacement
            if replacement is not None and previous_is_ascii_letter and next_is_ascii_letter
            else char
        )
    symbol_folded = "".join(in_word_symbols)
    variants = {value, symbol_folded}
    variants.update(
        folded
        for source in (value, symbol_folded)
        for folded in in_word_leet_folds(source, mints_governed_term=_mints_governed_term)
    )
    canonical: set[str] = set()
    for variant in variants:
        folded = variant
        for keyword, pattern in _STRUCTURAL_AUDIENCE_KEYWORD_PATTERNS:
            folded = pattern.sub(keyword, folded)
        canonical.add(folded)
    return canonical


def _contains_unreviewed_audience_outcome_claim(value: str) -> bool:
    """Fail closed on unreviewed population descriptions in benefit claims."""

    for match in _AUDIENCE_OUTCOME_CLAIM_RE.finditer(value):
        audience = " ".join(match.group("audience").strip(" ,").split())
        if not _is_reviewed_generic_audience(audience):
            return True
    return False


def _is_reviewed_generic_audience(value: str) -> bool:
    return _REVIEWED_GENERIC_AUDIENCE_RE.fullmatch(value) is not None


def assert_no_protected_class_marketing_text(value: str, *, field_name: str) -> str:
    """Reject protected-class language from targeting or outreach copy.

    This is intentionally narrower than a general prose validator. It is used
    only at campaign/outreach decision boundaries, where protected-class
    language must fail closed instead of being silently scrubbed or persisted.
    """

    if contains_protected_class_marketing_text(value):
        raise ValueError(f"{field_name} cannot contain protected-class targeting language")
    return value


def contains_protected_class_marketing_text(
    value: str,
    *,
    assume_reviewed_read_only_analytics: bool = False,
) -> bool:
    """Return true for protected-class or obvious proxy targeting language.

    ``assume_reviewed_read_only_analytics`` marks the caller's surface as a
    read-only analytics narrative (Ask Genie answers) whose ranking vocabulary
    ("candidates are those with the highest opportunity scores") is the
    product's core language. It bypasses ONLY the fail-closed unknown-criterion
    state machine that exists for campaign audience formation; every direct
    protected-class, health-status, national-origin, and proxy-targeting
    detector still runs.
    """

    return (
        protected_class_marketing_reason(
            value,
            assume_reviewed_read_only_analytics=assume_reviewed_read_only_analytics,
        )
        is not None
    )


def protected_class_marketing_reason(
    value: str,
    *,
    assume_reviewed_read_only_analytics: bool = False,
) -> ProtectedClassRefusalReason | None:
    """Name *why* this module rejects text, or ``None`` when it accepts it.

    Same decision as ``contains_protected_class_marketing_text`` -- this is
    the implementation, and the boolean delegates here -- but it separates a
    real protected-class/proxy match from the fail-closed unknown-criterion
    state. Callers that persist a refusal reason need that split: before it
    existed, "Which zyrplax borrowers are eligible for a HELOC?" was refused
    by the unknown-criterion state machine and audited as a fair-lending
    finding (persona audit, 2026-08-07).

    Direct detectors are consulted before the fail-closed states so a prompt
    that is both never under-reports as ``unreviewed_criterion``.
    """

    normalized = unicodedata.normalize("NFKC", str(value))
    # Campaign copy is an English-language governed surface. Invisible format
    # controls and non-ASCII alphabetic confusables make exact safety matching
    # non-auditable, so reject rather than attempting a lossy transliteration.
    # Unscannable text is an unproven-criteria state, not a fair-lending
    # finding: a Spanish-language campaign label must not be audited as
    # protected-class targeting.
    if any(unicodedata.category(char) == "Cf" for char in normalized):
        return "unreviewed_criterion"
    if any(
        unicodedata.category(char).startswith("L")
        and not ("A" <= char <= "Z" or "a" <= char <= "z")
        for char in normalized
    ):
        return "unreviewed_criterion"
    # NFKC intentionally preserves ordinary Latin diacritics. Strip combining
    # marks from an NFKD scan copy so accents cannot split a protected term
    # into unrelated ASCII fragments (for example ``Wómën`` or ``Müslïm``).
    # Keep ``normalized`` above for script/control validation and never persist
    # this lossy safety-only representation.
    mark_folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.category(char).startswith("M")
    )
    if is_reviewed_campaign_audience_description_text(
        mark_folded
    ) or is_reviewed_campaign_audience_summary_text(mark_folded):
        # The server renders these fields from a closed offer-to-audience map.
        # Handle it before the generic unknown-health relationship detector,
        # whose fail-closed ``population with X`` grammar intentionally cannot
        # infer that each complete product description is governed. Full-match
        # semantics ensure appended or substituted criteria remain scannable.
        return None
    unreviewed_audience_outcome_claim = any(
        _contains_unreviewed_audience_outcome_claim(candidate)
        for candidate in _structural_audience_scan_variants(mark_folded)
    )
    # Scan ordinary prose plus bounded de-obfuscations. The marketing surface
    # has no valid need for leetspeak or split-word spelling; joining up to
    # eight adjacent ASCII tokens catches forms such as ``w0men``, ``wo.men``,
    # ``w o m e n``, and ``mus lim`` without stripping the whole sentence into
    # one unauditable string.
    symbol_variants = {
        mark_folded,
        # Explicit, reviewed in-word symbol substitutions. Keep the original
        # scan alongside this fold so ordinary punctuation remains a boundary
        # while evasions such as ``Wom€n`` and ``Musl!m`` cannot pass.
        mark_folded.translate(_MARKETING_SYMBOL_CONFUSABLES),
    }
    # Folds every digit run except an isolated one whose folded spelling is
    # itself a governed term -- see ``in_word_leet_folds``.
    leet_variants = {
        translated
        for variant in symbol_variants
        for translated in in_word_leet_folds(variant, mints_governed_term=_mints_governed_term)
    }

    # Add only reviewed ASCII lookalike folds. These variants are safety-scan
    # inputs, never rewritten campaign copy: ``vv`` is commonly substituted
    # for ``w`` and a capital ``I`` for lowercase ``l`` in otherwise ordinary
    # words. Applying both orders catches combinations without opening a
    # general edit-distance matcher that would be difficult to audit.
    ascii_confusable_variants = {
        folded for variant in leet_variants for folded in ascii_confusable_folds(variant)
    }
    deobfuscated: set[str] = set()
    separator_folded: set[str] = set()
    joined_tokens: set[str] = set()
    for leet_folded in ascii_confusable_variants:
        separator_folded.add(
            # Safety-scan only: campaign prose has no legitimate reason to
            # make punctuation distinguish a protected multiword term. Fold
            # every non-alphanumeric separator run (including Unicode
            # bullets/dashes) to one space while preserving the submitted
            # copy unchanged for ordinary validation/persistence.
            re.sub(r"[^A-Za-z0-9]+", " ", leet_folded).strip()
        )
        deobfuscated.add(
            re.sub(
                r"(?<=[A-Za-z])[\-\u2010-\u2015](?=[A-Za-z])",
                "",
                leet_folded,
            )
        )
        tokenized = re.findall(r"[A-Za-z]+", leet_folded)
        joined_tokens.update(
            "".join(tokenized[start:stop])
            for start in range(len(tokenized))
            # Only join genuinely split terms. Re-emitting ordinary one-token
            # windows detaches words such as ``age`` and ``English`` from the
            # safe context already reviewed above, creating false positives.
            for stop in range(start + 2, min(len(tokenized), start + 8) + 1)
            if sum(len(token) for token in tokenized[start:stop]) <= 32
        )
    # Composition order must not reopen the boundary: apply the same bounded
    # folds after punctuation/split-token joining as well as before it.
    ascii_confusable_variants.update(
        folded
        for variant in deobfuscated | joined_tokens
        for folded in ascii_confusable_folds(variant)
    )
    scannable_parts = (
        mark_folded,
        *sorted(ascii_confusable_variants),
        *sorted(deobfuscated),
        *sorted(separator_folded),
        *sorted(joined_tokens),
    )
    scannable = " ".join(scannable_parts)
    for pattern in PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS:
        scannable = pattern.sub(" ", scannable)
    # Keep punctuation-preserving representations for criterion state.
    health_semantic_parts = (mark_folded, *sorted(leet_variants), *sorted(deobfuscated))
    health_scannable = " ; ".join(
        mask_protected_health_safe_contexts(part) for part in health_semantic_parts
    )
    # Direct status matching can use lossy separator/token normalization; selection state cannot.
    health_status_scannable = " ; ".join(
        mask_protected_health_safe_contexts(part) for part in scannable_parts
    )
    reviewed_analytics = is_reviewed_read_only_analytics_text(mark_folded)
    has_unreviewed_selection_criterion = (
        False
        if (reviewed_analytics or assume_reviewed_read_only_analytics)
        else any(
            contains_unreviewed_selection_criterion(
                mask_protected_health_safe_contexts(part),
                selection_context_re=PROTECTED_HEALTH_SELECTION_CONTEXT_RE,
            )
            # Separator-folded text is invalid for the clause state machine.
            for part in health_semantic_parts
        )
    )
    # Direct protected-class, health, national-origin, and proxy detectors
    # first; the fail-closed unknown-criterion states last. The set of
    # rejected text is unchanged (this was one ``or`` chain) -- only the
    # reported reason depends on the order.
    if (
        PROTECTED_CLASS_MARKETING_RE.search(scannable)
        or PROTECTED_AGE_CITIZENSHIP_MARKETING_RE.search(scannable)
        or PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE.search(scannable)
        or PROTECTED_RELIGION_FAMILIAL_RELATION_RE.search(scannable)
        or (not reviewed_analytics and PROTECTED_HEALTH_TERM_MARKETING_RE.search(health_scannable))
        or PROTECTED_HEALTH_STATUS_MARKETING_RE.search(health_status_scannable)
        or PROTECTED_HEALTH_GOVERNANCE_INTENT_RE.search(health_scannable)
        or _contains_national_origin_marketing_text(scannable)
        or contains_protected_class_proxy_marketing_text(scannable)
        # Clause-local, on the punctuation-preserving variant: see
        # ``contains_geographic_composition_proxy_text``.
        or contains_geographic_composition_proxy_text(mark_folded)
    ):
        return "protected_class"
    if has_unreviewed_selection_criterion or unreviewed_audience_outcome_claim:
        return "unreviewed_criterion"
    return None


# Governed national-origin vocabulary used only when the term occurs near a
# population or targeting verb. Keeping this explicit makes changes auditable
# and avoids treating arbitrary geography/product prose as protected targeting.
def _contains_national_origin_marketing_text(value: str) -> bool:
    scannable = value
    for safe_pattern in PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
        scannable = safe_pattern.sub(" ", scannable)
    for match in PROTECTED_NATIONAL_ORIGIN_RE.finditer(scannable):
        window = scannable[max(0, match.start() - 120) : match.end() + 120]
        if PROTECTED_CLASS_PROXY_HARD_TARGETING_RE.search(window):
            return True
        if PROTECTED_CLASS_PROXY_POPULATION_RE.search(window):
            return True
    return False


_CLAUSE_SPLIT_RE = re.compile(r"[.!?;:\n\r]+")


def contains_geographic_composition_proxy_text(value: str) -> bool:
    """Detect audience selection by the make-up of a PLACE, clause by clause.

    Separate from ``contains_protected_class_proxy_marketing_text`` for one
    reason: that function is fed the separator-folded scan variant, which joins
    every representation with spaces and drops punctuation so obfuscated
    multiword terms cannot hide. The pre-existing proxies survive that because
    they start with bound tokens (``section 8``, ``limited english``) that do
    not end sentences.

    Composition adjectives are ordinary predicate adjectives and end sentences
    constantly. Against the folded text, "Our product set is diverse.
    Communities we serve are growing quickly." reads as "diverse Communities"
    and files a fair-lending finding on benign prose -- measured, and the
    reason a first attempt at this bank was reverted rather than shipped.

    So this scans a punctuation-preserving representation, split into clauses
    first, and requires the targeting/population context to live in the SAME
    clause as the match.
    """

    for clause in _CLAUSE_SPLIT_RE.split(str(value)):
        if not clause.strip():
            continue
        scannable = clause
        for safe_pattern in PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
            scannable = safe_pattern.sub(" ", scannable)
        for pattern in PROTECTED_CLASS_GEOGRAPHIC_COMPOSITION_PATTERNS:
            if not pattern.search(scannable):
                continue
            if PROTECTED_CLASS_PROXY_HARD_TARGETING_RE.search(scannable):
                return True
            if GEOGRAPHIC_COMPOSITION_AUDIENCE_FORMATION_RE.search(scannable):
                return True
            if PROTECTED_CLASS_PROXY_POPULATION_RE.search(scannable):
                return True
    return False


def contains_protected_class_proxy_marketing_text(value: str) -> bool:
    """Detect protected-class proxies only when used for people or targeting.

    The proxy terms are not intrinsically unsafe. Language-access support,
    branch-service coverage, and document section references remain usable;
    borrower/geography selection and explicit targeting fail closed.
    """

    text = str(value)
    for safe_pattern in PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
        text = safe_pattern.sub(" ", text)
    for pattern in PROTECTED_CLASS_PROXY_PATTERNS:
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 120) : match.end() + 120]
            if PROTECTED_CLASS_PROXY_HARD_TARGETING_RE.search(window):
                return True
            if PROTECTED_CLASS_PROXY_POPULATION_RE.search(window):
                return True
    return False
