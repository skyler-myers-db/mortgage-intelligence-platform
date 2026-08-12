"""Structural extraction for transfer or admission into marketing audiences."""

from __future__ import annotations

import re

_POPULATION = (
    r"(?:people|persons?|individuals?|residents?|households?|borrowers?|homeowners?|"
    r"applicants?|recipients?|customers?|prospects?|clients?|owners?|members?|patients?|"
    r"candidates?|leads?|groups?|cohorts?|audiences?|segments?|populations?)"
)
_DIRECTIVE_PREFIX = (
    r"(?:(?:could|would|can|will)\s+you\s+)?(?:(?:please|kindly)\s*,?\s+)?"
    r"(?:(?:(?:go|move)\s+ahead\s+and|proceed\s+(?:to|and))\s+)?"
)
# Destination + population + criterion provide the governance relationship;
# the admission verb itself is intentionally open. Enumerating verbs allowed
# semantically identical wording (for example, schedule or channel) to evade
# the shared sink. The bounded token cannot cross whitespace or punctuation,
# while every match must still satisfy one of the complete relations below.
_ACTION_WORD = r"(?:[a-z][a-z-]{2,24})"
_ACTION_CONTINUATION_WORD = r"(?:[a-z][a-z-]{1,24})"
_ACTION = rf"(?:{_ACTION_WORD}(?:\s+{_ACTION_CONTINUATION_WORD}){{0,2}})"
_PARTICIPLE = rf"(?:{_ACTION_WORD}(?:\s+{_ACTION_CONTINUATION_WORD}){{0,1}})"
_AUXILIARY = (
    r"(?:(?:is|are|was|were|gets?|got)|(?:has|have|had)\s+(?:been|gotten)|"
    r"(?:will|would|should|can|could|may|might)\s+(?:be|get))"
)
_DESTINATION = r"(?:campaign|cohort|audience|segment|population|list|queue|offer)"
_CONDITION = (
    r"(?:when|if|where|provided(?:\s+that)?|based\s+on|according\s+to|using|"
    r"because\s+of|because|due\s+to|owing\s+to|on\s+account\s+of|"
    r"on\s+(?:the\s+)?(?:basis|grounds?)\s+of|by\s+(?:reason|virtue)\s+of|"
    r"in\s+light\s+of)"
)
_CAUSAL_CONDITION = (
    r"(?:because\s+of|because|due\s+to|owing\s+to|on\s+account\s+of|"
    r"on\s+(?:the\s+)?(?:basis|grounds?)\s+of|by\s+(?:reason|virtue)\s+of|"
    r"in\s+light\s+of)"
)
_MODIFIERS = (
    r"(?:(?:the|these|those|all|reviewed|eligible|qualified|marketing[- ]eligible|"
    r"highest[- ]scoring|prospective|current)\s+){0,3}"
    # A bare cardinal QUANTIFIES the population it precedes -- "the top 50
    # borrowers", "the next 1,000 leads". Without this slot the count broke the
    # admission parse, so "Add the top 50 borrowers with the highest rate spread
    # to the campaign." proved no relation at all and fell through to the
    # fail-closed tail, while the same sentence without the count was answered.
    # The count admits nothing on its own: a token drawn from ``[0-9,]`` cannot
    # spell a criterion, and whatever the criterion group captures must still
    # satisfy ``_is_reviewed_admission_criterion``.
    r"(?:(?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)\s+)?"
)
_DESTINATION_REFERENCE = rf"(?:the\s+|this\s+|that\s+|a\s+|an\s+)?{_DESTINATION}"
_DESTINATION_RELATION = rf"(?:into|in|to|on|onto|for|towards?)\s+{_DESTINATION_REFERENCE}"
# Some admissions encode the relationship through argument order rather than a
# transfer preposition: ``borrowers enter the campaign``, ``campaign receives
# borrowers``, or ``borrowers gain campaign membership``. Keep the predicate a
# bounded word and prove admission from population/destination order plus an
# explicit criterion connector. This avoids an evadable action-synonym list.
_DIRECT_DESTINATION_RELATION = _DESTINATION_REFERENCE
_POPULATION_DESTINATION_RELATION = rf"(?:{_DESTINATION_RELATION}|{_DIRECT_DESTINATION_RELATION})"
_DESTINATION_MEMBERSHIP_RELATION = (
    rf"(?:{_DESTINATION_REFERENCE}\s+"
    r"(?:membership|admission|placement|enrollment|members?|participants?)|"
    rf"(?:members?|participants?|part)\s+(?:of|in)\s+{_DESTINATION_REFERENCE})"
)
_CRITERION_ATTRIBUTE_RELATION = (
    r"(?:with|having|showing|displaying|based\s+on|living\s+with|coping\s+with|"
    r"affected\s+by)"
)
_ACTIVE_ADMISSION_PREDICATE = (
    r"(?:enters?|entered|entering|joins?|joined|joining|lands?|landed|landing|"
    r"(?:ends?|ended|ending|winds?|wound|winding)\s+up|"
    r"finds?\s+(?:itself|themselves)|found\s+(?:itself|themselves)|makes?\s+it|made\s+it)"
)
_CAUSAL_ADMISSION_PREDICATE = (
    r"(?:caus(?:e|es|ed)|driv(?:e|es|en)|determin(?:e|es|ed)(?:\s+(?:whether|if))?|"
    r"leads?|led|brings?|brought|puts?|put|(?:is|are|was|were)\s+putting|"
    r"sends?|sent|routes?|routed|funnels?|funneled|"
    r"moves?|moved|steers?|steered|directs?|directed|explains?\s+why|"
    r"result(?:s|ed)?\s+in|accounts?\s+for\s+why)"
)

_CRITERION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"^{_DIRECTIVE_PREFIX}{_ACTION}\s+{_MODIFIERS}{_POPULATION}\s+"
        rf"{_DESTINATION_RELATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+{_AUXILIARY}\s+{_PARTICIPLE}\s+"
        rf"{_DESTINATION_RELATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+{_ACTION}\s+"
        rf"{_POPULATION_DESTINATION_RELATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+{_ACTION}\s+"
        rf"{_DESTINATION_MEMBERSHIP_RELATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_DIRECT_DESTINATION_RELATION}\s+{_ACTION}\s+"
        rf"{_MODIFIERS}{_POPULATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_DIRECT_DESTINATION_RELATION}\s+{_AUXILIARY}\s+{_PARTICIPLE}\s+"
        rf"(?:with|by|from|of)\s+{_MODIFIERS}{_POPULATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_DIRECTIVE_PREFIX}{_ACTION}\s+{_MODIFIERS}{_POPULATION}\s+"
        rf"{_CRITERION_ATTRIBUTE_RELATION}\s+"
        rf"(?P<criterion>[^.!?;:]{{1,100}}?)\s+{_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+"
        rf"{_CRITERION_ATTRIBUTE_RELATION}\s+"
        rf"(?P<criterion>[^.!?;:]{{1,100}}?)\s+{_AUXILIARY}\s+{_PARTICIPLE}\s+"
        rf"{_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+"
        rf"{_CRITERION_ATTRIBUTE_RELATION}\s+"
        rf"(?P<criterion>[^.!?;:]{{1,100}}?)\s+{_ACTIVE_ADMISSION_PREDICATE}\s+"
        rf"{_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?P<criterion>[^.!?;:]{{1,100}}?)\s+{_POPULATION}\s+"
        rf"{_AUXILIARY}\s+{_PARTICIPLE}\s+{_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:the\s+)?(?:admission|transfer|movement|placement|addition)\s+of\s+"
        rf"{_MODIFIERS}{_POPULATION}\s+{_DESTINATION_RELATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}(?:['’]s|s['’])\s+"
        rf"(?:admission|transfer|movement|placement|addition)\s+"
        rf"{_DESTINATION_RELATION}\s+{_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,120})$",
        re.IGNORECASE,
    ),
    # Causal prose can bind a criterion to admission without a conditional
    # connector. Preserve the same population/destination proof while allowing
    # criterion-first reason, because-of, and causal-verb word orders.
    re.compile(
        rf"^(?P<criterion>[^.!?;:,]{{1,100}}?)\s+"
        r"(?:is|was|remains?)\s+(?:(?:the\s+)?(?:reason|basis|cause|grounds?)\s+"
        r"(?:why\s+|that\s+|for\s+)|why\s+)"
        rf"{_MODIFIERS}{_POPULATION}\s+{_ACTION}\s+"
        rf"{_POPULATION_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_CAUSAL_CONDITION}\s+"
        rf"(?P<criterion>[^.!?;:,]{{1,100}}?)\s*,?\s+"
        rf"{_MODIFIERS}{_POPULATION}\s+{_ACTION}\s+"
        rf"{_POPULATION_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?P<criterion>[^.!?;:,]{{1,100}}?)\s+"
        rf"{_CAUSAL_ADMISSION_PREDICATE}\s+{_MODIFIERS}{_POPULATION}\s+"
        rf"to\s+{_ACTION}\s+{_POPULATION_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?P<criterion>[^.!?;:,]{{1,100}}?)\s+"
        rf"{_CAUSAL_ADMISSION_PREDICATE}\s+{_MODIFIERS}{_POPULATION}\s+"
        rf"{_ACTION}\s+{_POPULATION_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?P<criterion>[^.!?;:,]{{1,100}}?)\s+"
        rf"{_CAUSAL_ADMISSION_PREDICATE}\s+{_MODIFIERS}{_POPULATION}\s+"
        rf"{_DESTINATION_RELATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+{_ACTION}\s+"
        rf"{_POPULATION_DESTINATION_RELATION}\s+"
        rf"{_CAUSAL_CONDITION}\s+"
        r"(?P<criterion>[^.!?;:]{1,100})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:the\s+)?{_DESTINATION}\s+"
        r"(?:entry|membership|admission|placement|enrollment)\s+"
        r"(?:(?:depends?|hinges?|rests?)\s+(?:on|upon)|(?:stems?|results?)\s+from|"
        r"(?:is|was)\s+(?:based\s+on|determined\s+by))\s+"
        r"(?P<criterion>[^.!?;:]{1,100})$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?P<criterion>[^.!?;:,]{{1,100}}?)\s+"
        r"(?:caus(?:e|es|ed)|driv(?:e|es|en)|determines?)\s+"
        rf"{_DESTINATION_REFERENCE}\s+(?:membership|admission|placement|enrollment)\s+"
        rf"(?:for|of)\s+{_MODIFIERS}{_POPULATION}$",
        re.IGNORECASE,
    ),
    # A population can be offered a governed destination directly. Bind the
    # attribute phrase to the destination so health wording cannot evade the
    # audience decision merely by using ``receive`` instead of ``enter``.
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+{_CRITERION_ATTRIBUTE_RELATION}\s+"
        r"(?P<criterion>[^.!?;:]{1,100}?)\s+"
        rf"(?:receives?|received|receiving)\s+{_DESTINATION_REFERENCE}$",
        re.IGNORECASE,
    ),
    # Destination-first eligibility statements are still audience decisions
    # when the destination, population, and bound criterion are all explicit.
    re.compile(
        rf"^{_DIRECT_DESTINATION_RELATION}\s+(?:is|was|will\s+be|would\s+be)\s+for\s+"
        rf"{_MODIFIERS}{_POPULATION}\s+{_CRITERION_ATTRIBUTE_RELATION}\s+"
        r"(?P<criterion>[^.!?;:]{1,100})$",
        re.IGNORECASE,
    ),
)
_UNREVIEWED_RELATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}\s+{_AUXILIARY}\s+{_PARTICIPLE}\s+"
        rf"{_DESTINATION_RELATION}(?:\s+[^.!?;:]{{1,120}})?$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:the\s+)?(?:admission|transfer|movement|placement|addition)\s+of\s+"
        rf"{_MODIFIERS}{_POPULATION}\s+{_DESTINATION_RELATION}"
        r"(?:\s+[^.!?;:]{1,120})?$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_MODIFIERS}{_POPULATION}(?:['’]s|s['’])\s+"
        rf"(?:admission|transfer|movement|placement|addition)\s+"
        rf"{_DESTINATION_RELATION}(?:\s+[^.!?;:]{{1,120}})?$",
        re.IGNORECASE,
    ),
)


def audience_admission_criterion(clause: str) -> str | None:
    """Return the criterion, empty for an unproved relation, or None if unrelated."""

    for pattern in _CRITERION_PATTERNS:
        match = pattern.fullmatch(clause)
        if match is not None:
            return match.group("criterion")
    if any(pattern.fullmatch(clause) is not None for pattern in _UNREVIEWED_RELATION_PATTERNS):
        return ""
    return None


def remove_audience_admission_clauses_for_identity_scan(value: str) -> str:
    """Remove independently proved admission clauses from identity-only scans."""

    parts = re.split(r"([.!?;\n]+)", value)
    return "".join(
        "" if index % 2 == 0 and audience_admission_criterion(part.strip()) is not None else part
        for index, part in enumerate(parts)
    )
