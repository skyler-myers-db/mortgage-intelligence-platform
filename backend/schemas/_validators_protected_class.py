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
    _PROTECTED_HEALTH_COORDINATED_CONTINUATION_RE,
    PROTECTED_HEALTH_SAFE_CONTEXT_PATTERNS,
    PROTECTED_NATIONAL_ORIGIN_RE,
)
from backend.schemas.marketing_scan_provenance import (
    SHADOW_LEET_TABLE,
    ScanPair,
    ascii_confusable_fold_pairs,
    join_pairs,
    merge_pairs,
    mirror_sub,
    regex_pair,
    search_backed,
    splice_pair,
    tokenize_pair,
    translate_pair,
)
from backend.schemas.marketing_selection_criteria import (
    contains_unreviewed_selection_criterion,
    is_reviewed_campaign_audience_description_text,
    is_reviewed_campaign_audience_summary_text,
    is_reviewed_read_only_analytics_text,
)
from backend.schemas.protected_relationships import PROTECTED_RELIGION_FAMILIAL_RELATION_RE

# Governed refusal reasons this module can report. Both are fail-closed
# rejections; only ``protected_class`` is a fair-lending finding.
ProtectedClassRefusalReason = Literal["protected_class", "unreviewed_criterion"]

# The two reviewed leetspeak readings (``1`` is ambiguous between ``l`` and
# ``i``); the shadow table in ``marketing_scan_provenance`` mirrors the same
# six digits onto the provenance sentinel.
_LEET_TABLES = (
    str.maketrans("013457", "oleast"),
    str.maketrans("013457", "oieast"),
)
_SEPARATOR_RUN_RE = re.compile(r"[^A-Za-z0-9]+")
_IN_WORD_HYPHEN_RE = re.compile(r"(?<=[A-Za-z])[\-‐-―](?=[A-Za-z])")
_LETTER_RUN_RE = re.compile(r"[A-Za-z]+")

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
    variants = {
        value,
        symbol_folded,
        value.translate(str.maketrans("013457", "oleast")),
        value.translate(str.maketrans("013457", "oieast")),
        symbol_folded.translate(str.maketrans("013457", "oleast")),
        symbol_folded.translate(str.maketrans("013457", "oieast")),
    }
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


# A rendered governed table re-scans every cell, and those repeat heavily: in
# the live 20-row capture of 2026-08-12 (mip.gold.lead_population) 106 of 200
# cells were duplicates, one 150-character rationale appearing 19 times. The
# decision is a pure function of the text and the flag, so memoizing cannot
# change a verdict; maxsize and the length gate keep a long answer from
# pinning itself in the cache.
_CACHEABLE_SCAN_CHARS = 512


@lru_cache(maxsize=1024)
def _protected_class_marketing_reason_cached(
    value: str,
    assume_reviewed_read_only_analytics: bool,
) -> ProtectedClassRefusalReason | None:
    return _protected_class_marketing_reason(
        value, assume_reviewed_read_only_analytics=assume_reviewed_read_only_analytics
    )


def protected_class_marketing_reason(
    value: str,
    *,
    assume_reviewed_read_only_analytics: bool = False,
) -> ProtectedClassRefusalReason | None:
    """Memoizing front door for :func:`_protected_class_marketing_reason`."""

    text = str(value)
    if len(text) <= _CACHEABLE_SCAN_CHARS:
        return _protected_class_marketing_reason_cached(text, assume_reviewed_read_only_analytics)
    return _protected_class_marketing_reason(
        text, assume_reviewed_read_only_analytics=assume_reviewed_read_only_analytics
    )


def _protected_class_marketing_reason(
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
    # Every variant is built twice from here: the REAL string, byte-identical
    # to the historical scan text, and a provenance SHADOW whose leet stage
    # folds digits to a sentinel letter instead of ``o/l/e/a/s/t`` (see
    # ``marketing_scan_provenance``). Matching runs on the real text, and a
    # match stands only if its span holds a source-backed letter -- which is
    # what stops ``4,140`` minting the national-origin term ``lao`` while
    # leaving every split evasion caught: ``muslim`` from ``mus 1 im`` keeps
    # ``mus``/``im``, ``laotian`` from ``140-tian`` keeps ``tian``.
    leet_pairs = [
        translate_pair(ScanPair(variant), table, SHADOW_LEET_TABLE)
        for variant in sorted(symbol_variants)
        for table in _LEET_TABLES
    ]

    # Add only reviewed ASCII lookalike folds. These variants are safety-scan
    # inputs, never rewritten campaign copy: ``vv`` is commonly substituted
    # for ``w`` and a capital ``I`` for lowercase ``l`` in otherwise ordinary
    # words. Applying both orders catches combinations without opening a
    # general edit-distance matcher that would be difficult to audit.
    ascii_confusable_pairs = [
        folded for pair in leet_pairs for folded in ascii_confusable_fold_pairs(pair)
    ]
    deobfuscated_pairs: list[ScanPair] = []
    separator_pairs: list[ScanPair] = []
    joined_pairs: list[ScanPair] = []
    window_origins: dict[str, str] = {}
    # Iterate the DEDUPLICATED variants, as the historical set-based loop
    # did: for unvaried text the raw pair list holds ten copies of one
    # string, and running window construction per copy cost ~10x on long
    # inputs (measured in signoff round two, 6x at 10KB end to end).
    for _real, _shadow in merge_pairs(ascii_confusable_pairs).items():
        leet_folded = ScanPair(_real, _shadow)
        # Safety-scan only: campaign prose has no legitimate reason to
        # make punctuation distinguish a protected multiword term. Fold
        # every non-alphanumeric separator run (including Unicode
        # bullets/dashes) to one space while preserving the submitted
        # copy unchanged for ordinary validation/persistence.
        collapsed = regex_pair(leet_folded, _SEPARATOR_RUN_RE, " ")
        lead = len(collapsed.real) - len(collapsed.real.lstrip(" "))
        trail = len(collapsed.real.rstrip(" "))
        separator_pairs.append(ScanPair(collapsed.real[lead:trail], collapsed.shadow[lead:trail]))
        deobfuscated_pairs.append(regex_pair(leet_folded, _IN_WORD_HYPHEN_RE, ""))
        token_spans = list(_LETTER_RUN_RE.finditer(leet_folded.real))
        tokens = tokenize_pair(leet_folded, _LETTER_RUN_RE)
        for start in range(len(tokens)):
            # Only join genuinely split terms. Re-emitting ordinary one-token
            # windows detaches words such as ``age`` and ``English`` from the
            # safe context already reviewed above, creating false positives.
            # Twelve tokens, because the longest single-word governed terms
            # (``millennials``, ``bangladeshi``, ``trinidadian``) are eleven
            # letters and an evader spells them one letter per token: with an
            # eight-token cap the term never reconstructed, and the only
            # thing refusing ``m i 1 1 3 n n i 4 1 5`` was the incidental
            # ``als`` mint that provenance rightly drops (signoff round two).
            # The 32-character sum bound keeps the window count finite.
            for stop in range(start + 2, min(len(tokens), start + 12) + 1):
                if sum(len(token.real) for token in tokens[start:stop]) > 32:
                    continue
                window = ScanPair(
                    "".join(token.real for token in tokens[start:stop]),
                    "".join(token.shadow for token in tokens[start:stop]),
                )
                joined_pairs.append(window)
                # A joined window is a bare token in the sorted scan blob, so
                # a CONTEXT-GATED bank (national origin, proxies) loses the
                # population noun that sat beside the split term in the
                # sentence -- ``140 tian homeowners`` reconstructs ``laotian``
                # but the noun test used to depend on whichever windows sorted
                # alongside. Remember where each window came from so the
                # gated detectors can ask the ORIGIN for the noun instead.
                #
                # Only a window that LOOKS like a split evasion earns that
                # assist: one carrying a digit-minted character, or one
                # spliced from three or more tokens. A two-token all-source
                # join is the shape ordinary prose makes by accident --
                # ``Bank of America, N.A.`` joins ``America``+``N`` into
                # ``american``, ``the borrower's Audi`` joins ``s``+``Audi``
                # into ``saudi`` -- and origin assistance turned those into
                # deterministic fair-lending findings on servicer and
                # possessive prose (signoff round two). Without the assist
                # they fall back to the blob-window test, which is base
                # behavior.
                window_is_evasion_shaped = stop - start >= 3 or window.shadow != window.real
                if not window_is_evasion_shaped:
                    continue
                origin = leet_folded.real[
                    max(0, token_spans[start].start() - 120) : token_spans[stop - 1].end() + 120
                ]
                existing = window_origins.get(window.real)
                window_origins[window.real] = (
                    origin if existing is None else f"{existing} ; {origin}"
                )
    # Composition order must not reopen the boundary: apply the same bounded
    # folds after punctuation/split-token joining as well as before it.
    ascii_confusable_pairs.extend(
        folded
        for pair in (*deobfuscated_pairs, *joined_pairs)
        for folded in ascii_confusable_fold_pairs(pair)
    )
    # Dedup by scan text, OR-merging provenance across generating paths, so
    # the emitted part lists are exactly the historical sorted sets.
    ascii_confusable_variants = merge_pairs(ascii_confusable_pairs)
    leet_variants = merge_pairs(leet_pairs)
    deobfuscated = merge_pairs(deobfuscated_pairs)
    separator_folded = merge_pairs(separator_pairs)
    joined_tokens = merge_pairs(joined_pairs)
    scannable_parts = [
        ScanPair(mark_folded),
        *(ScanPair(real, shadow) for real, shadow in sorted(ascii_confusable_variants.items())),
        *(ScanPair(real, shadow) for real, shadow in sorted(deobfuscated.items())),
        *(ScanPair(real, shadow) for real, shadow in sorted(separator_folded.items())),
        *(ScanPair(real, shadow) for real, shadow in sorted(joined_tokens.items())),
    ]
    scannable_pair = join_pairs(scannable_parts, " ")
    for pattern in PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS:
        scannable_pair = mirror_sub(scannable_pair, pattern, " ")
    # Keep punctuation-preserving representations for criterion state.
    health_semantic_pairs = [
        ScanPair(mark_folded),
        *(ScanPair(real, shadow) for real, shadow in sorted(leet_variants.items())),
        *(ScanPair(real, shadow) for real, shadow in sorted(deobfuscated.items())),
    ]
    masked_health_pairs = [_mask_health_pair(pair) for pair in health_semantic_pairs]
    health_pair = join_pairs(masked_health_pairs, " ; ")
    # Direct status matching can use lossy separator/token normalization; selection state cannot.
    health_status_pair = join_pairs([_mask_health_pair(pair) for pair in scannable_parts], " ; ")
    reviewed_analytics = is_reviewed_read_only_analytics_text(mark_folded)
    has_unreviewed_selection_criterion = (
        False
        if (reviewed_analytics or assume_reviewed_read_only_analytics)
        else any(
            contains_unreviewed_selection_criterion(
                masked.real,
                selection_context_re=PROTECTED_HEALTH_SELECTION_CONTEXT_RE,
            )
            # Separator-folded text is invalid for the clause state machine.
            for masked in masked_health_pairs
        )
    )
    # Direct protected-class, health, national-origin, and proxy detectors
    # first; the fail-closed unknown-criterion states last. The set of
    # rejected text is unchanged (this was one ``or`` chain) -- only the
    # reported reason depends on the order.
    if (
        search_backed(PROTECTED_CLASS_MARKETING_RE, scannable_pair)
        or search_backed(PROTECTED_AGE_CITIZENSHIP_MARKETING_RE, scannable_pair)
        or search_backed(PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE, scannable_pair)
        or search_backed(PROTECTED_RELIGION_FAMILIAL_RELATION_RE, scannable_pair)
        or (
            not reviewed_analytics
            and search_backed(PROTECTED_HEALTH_TERM_MARKETING_RE, health_pair)
        )
        or search_backed(PROTECTED_HEALTH_STATUS_MARKETING_RE, health_status_pair)
        or search_backed(PROTECTED_HEALTH_GOVERNANCE_INTENT_RE, health_pair)
        or _contains_national_origin_marketing_text(scannable_pair, window_origins)
        or _contains_protected_class_proxy_pair(scannable_pair, window_origins)
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
def _contains_national_origin_marketing_text(
    pair: ScanPair,
    window_origins: dict[str, str] | None = None,
) -> bool:
    for safe_pattern in PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
        pair = mirror_sub(pair, safe_pattern, " ")
    for match in PROTECTED_NATIONAL_ORIGIN_RE.finditer(pair.real):
        if not pair.backed(match.start(), match.end()):
            # Every character of the matched term is a digit-minted letter --
            # a NUMBER the leet fold rewrote, ``4,140`` -> ``lao`` -- not a
            # term anyone wrote. The window nouns are irrelevant to a term
            # that does not exist in the source.
            continue
        window = pair.real[max(0, match.start() - 120) : match.end() + 120]
        if _window_targets_population(window):
            return True
        origin = (window_origins or {}).get(match.group())
        if origin is not None and _window_targets_population(origin):
            return True
    return False


def _window_targets_population(window: str) -> bool:
    return (
        PROTECTED_CLASS_PROXY_HARD_TARGETING_RE.search(window) is not None
        or PROTECTED_CLASS_PROXY_POPULATION_RE.search(window) is not None
    )


def _mask_health_pair(pair: ScanPair) -> ScanPair:
    """``mask_protected_health_safe_contexts``, mirrored onto a scan pair.

    Follows the original loop exactly -- same patterns, same longest-match
    choice, same coordinated-continuation sentinel -- splicing the shadow at
    the spans the REAL text matched, because those word patterns cannot be
    re-run on sentinel text. The inserted ``cancer`` sentinel enters both
    strings and therefore stays matchable and source-backed, exactly as the
    original inserts it into the scan copy.
    ``test_leet_digit_match_provenance`` pins ``.real`` against the original
    function so the two loops cannot drift apart.
    """

    for _ in range(16):
        matches = [
            match
            for pattern in PROTECTED_HEALTH_SAFE_CONTEXT_PATTERNS
            if (match := pattern.search(pair.real)) is not None
        ]
        if not matches:
            break
        match = max(matches, key=lambda item: item.end() - item.start())
        if _PROTECTED_HEALTH_COORDINATED_CONTINUATION_RE.match(pair.real[match.end() :]):
            pair = splice_pair(pair, match.start(), match.end(), " cancer ")
        else:
            pair = splice_pair(pair, match.start(), match.end(), " ")
    return pair


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

    A bare string has no provenance shadow, so every span reads as
    source-backed and this public form behaves exactly as it always has.
    """

    return _contains_protected_class_proxy_pair(ScanPair(str(value)))


def _contains_protected_class_proxy_pair(
    pair: ScanPair,
    window_origins: dict[str, str] | None = None,
) -> bool:
    for safe_pattern in PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
        pair = mirror_sub(pair, safe_pattern, " ")
    for pattern in PROTECTED_CLASS_PROXY_PATTERNS:
        for match in pattern.finditer(pair.real):
            if not pair.backed(match.start(), match.end()):
                continue
            window = pair.real[max(0, match.start() - 120) : match.end() + 120]
            if _window_targets_population(window):
                return True
            origin = (window_origins or {}).get(match.group())
            if origin is not None and _window_targets_population(origin):
                return True
    return False
