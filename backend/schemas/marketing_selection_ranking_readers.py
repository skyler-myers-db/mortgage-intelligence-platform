"""Fail-closed readers for two ranking slots the criterion machine never read.

Both were measured on main (7dc6c29a, 2026-09-08) at ``protected_prompt_match``
while the planner-line false positives were being closed
(``test_planned_deep_analysis_cross_clause_bindings``). Neither was opened by
that change; each is exactly as reachable as it was before it.

* **A hyphen-attached formation participle's LEFT half.** The bound-population
  capture matches ``\\branked`` after the hyphen with an EMPTY criterion, and the
  pre-population reviewer accepts the empty span, so "the zyrplax-ranked
  borrowers" answered while "the top-ranked zyrplax borrowers" refused: only
  the span BETWEEN the participle and the population noun was ever judged.
  Same for ``rosacea-ranked``, ``credit score-ranked`` and
  ``left-handedness-ranked``. The premodifier of the compound IS the ranking
  criterion.

* **A weighing or ranking adverbial with no population noun inside it.**
  "which segments should be prioritized when balancing zyrplax?" and "Which
  segments have the highest opportunity when ranked by rosacea?" answered,
  while the same sentences refused once a population noun appeared later in
  the list -- accidentally, through the bound capture. The candidate loop
  reads a population's suffix for an immediate outcome, an anchored passive
  outcome and a criterion connector; a subordinate ``when ranked by X`` or
  ``when balancing X`` clause is none of those, so ``X`` was read by nothing.

Each reader is a REFUSAL-ADDER, never an allow branch: it returns a refusal
only when it has read one of these two slots and the content is outside the
reviewed vocabulary. Every verdict the machine gives today stands unless one
of the two slots holds an unreviewed term. The allow sides are closed -- a
literal alternation of legitimate left halves, and the union of the governed
ranking vocabularies the analytics shapes already read (weighing measures,
analytic dimensions and signals, product intents, group sizes), judged WHOLE
-- so an unreviewed conjunct cannot ride a reviewed one ("rate spread and
zyrplax" refuses) and an unlisted left half fails closed.

Tree-swap differential against the base tree (pinned guard-family literals
plus a shape x payload battery) is the acceptance evidence; the readers were
tuned only through the allowed->refused losses whose payload is reviewed
vocabulary, never by widening a slot to accept an unreviewed one.
"""

from __future__ import annotations

import re

from backend.schemas.growth_agent_segment_intent import (
    is_closed_reviewed_segment_signal_criterion,
)
from backend.schemas.marketing_selection_reviewed_analytics import (
    _REVIEWED_ANALYTIC_DIMENSION,
    _REVIEWED_ANALYTIC_SIGNAL,
    _REVIEWED_PRODUCT_INTENT,
    _REVIEWED_WEIGHING_MEASURE,
)
from backend.schemas.marketing_selection_reviewed_places import (
    GOVERNED_SCOPE_DEMONSTRATIVE_FRAGMENT,
)
from backend.schemas.marketing_selection_vocabulary import (
    POPULATION_QUANTIFIER_DIGITS,
    REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT,
    matches_reviewed_mortgage_attribute,
    reviewed_attribute_scope_fragment,
    reviewed_fullmatch,
)

# The one open slot both readers judge: what something is ranked, sorted,
# weighed or balanced BY. Every alternative is a governed vocabulary another
# reviewed shape already reads -- the weighing measures (``borrower volume``,
# a reviewed attribute with an optional ``concentration|mix|share|
# distribution``), the analytics dimensions and signal columns, the product
# intents, and the group-size nouns a segment ranking is sized by. A measure
# that is not in any of them ("credit score", "score", "zyrplax") is not
# added here: reviewing vocabulary for a measure the product does not model
# would be inventing a capability, not closing a hole.
_RANKING_MEASURE = (
    rf"(?:{_REVIEWED_WEIGHING_MEASURE}|{_REVIEWED_ANALYTIC_DIMENSION}|"
    rf"{_REVIEWED_ANALYTIC_SIGNAL}|"
    rf"{_REVIEWED_PRODUCT_INTENT}"
    r"(?:\s+(?:opportunity|potential|propensity|intent|signals?|volume|candidates?))?|"
    r"(?:(?:borrower|lead|segment|cohort|population|customer|homeowner)\s+)?"
    r"(?:counts?|volume|size|totals?)|"
    r"(?:the\s+)?(?:number|count|share|percentage|percent)\s+of\s+"
    r"(?:borrowers?|leads?|customers?|homeowners?|candidates?)|"
    r"(?:overall\s+|total\s+|modeled\s+)?(?:opportunity|upside|potential))"
)
_RANKING_MEASURE_ARTICLE = r"(?:(?:the|our|their|its)\s+)?"
# A trailing scope preposition with NOTHING behind it is what the governed
# fair-lending mask leaves when it erases one of its admission-gated place
# values ("when balancing rate spread in Oklahoma" reaches this machine as
# "... rate spread in  "). Tolerating the bare preposition admits nothing a
# prompt can carry: the mask erases only those governed values, and a
# preposition with a real object behind it still has to satisfy the scope
# fragment's membership screen.
_DANGLING_SCOPE_PREPOSITION = r"(?:\s+(?:in|across|within|throughout))?"
_REVIEWED_RANKING_OBJECT_RE = re.compile(
    rf"^{_RANKING_MEASURE_ARTICLE}{_RANKING_MEASURE}"
    rf"(?:\s*,\s*{_RANKING_MEASURE_ARTICLE}{_RANKING_MEASURE})*"
    rf"(?:\s*,?\s+(?:and|or)\s+{_RANKING_MEASURE_ARTICLE}{_RANKING_MEASURE})?"
    rf"{reviewed_attribute_scope_fragment('ranking_object')}"
    rf"{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}"
    rf"{_DANGLING_SCOPE_PREPOSITION}$"
    # The consideration may be the governed scope itself ("when considering the
    # current coverage for the campaign"): a closed demonstrative scope, with
    # the same purpose tail a reviewed attribute takes.
    rf"|^{GOVERNED_SCOPE_DEMONSTRATIVE_FRAGMENT}{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}$",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" ,.-")).strip(" ,.-")


def _is_reviewed_ranking_object(value: str) -> bool:
    """True when a whole ranking object is governed vocabulary, by any reviewer."""

    criterion = _normalize(value)
    return bool(criterion) and (
        reviewed_fullmatch(_REVIEWED_RANKING_OBJECT_RE, criterion)
        or matches_reviewed_mortgage_attribute(criterion)
        or is_closed_reviewed_segment_signal_criterion(criterion)
    )


# ---------------------------------------------------------------------------
# Reader 1: the left half of ``<X>-ranked <population>``.
# ---------------------------------------------------------------------------
#
# Legitimate single-token left halves of a hyphen-attached formation
# participle. Measured across the repo's own prompt copy before choosing
# (``top-ranked`` x13, ``highest-ranked``, ``hand-picked``, ``pre-selected``,
# ``re-selected``, ``auto-picked``, ``well-targeted``, ``last-selected``,
# ``model-``/``operator-``/``supervisor-selected``): a degree or ordinal
# ("top", "highest", "second"), a productive prefix ("pre", "re", "un",
# "non", "de", "self", "auto", "hand", "cherry"), a recency adverb, the agent
# that did the ranking ("model", "operator", "user"), or a bare count. None of
# these names WHAT the population was ranked by. A literal alternation on
# purpose -- never an open adjective slot -- so a de-obfuscated variant that
# mints ``t0p`` -> ``top`` still refuses on the original string, which is the
# direction the variant combiner fails.
_REVIEWED_PARTICIPLE_PREFIX_RE = re.compile(
    r"^(?:top|highest|best|lowest|bottom|worst|first|last|second|third|"
    r"mid|middle|higher|lower|high|low|well|evenly|"
    r"pre|re|un|non|de|auto|self|hand|cherry|"
    r"newly|recently|previously|already|freshly|currently|formerly|manually|"
    r"machine|model|system|genie|operator|user|human|supervisor|"
    rf"{POPULATION_QUANTIFIER_DIGITS})$",
    re.IGNORECASE,
)
# Where a multi-word left half STARTS: the closed function-word run in front of
# a noun phrase (determiners, prepositions, conjunctions, auxiliaries, the
# question words, a count). The walk from the hyphen stops at the first of
# these, and everything between is judged WHOLE against the ranking-object
# vocabulary, so "the opportunity score-ranked borrowers" reviews as
# ``opportunity score`` and "the zyrplax opportunity score-ranked borrowers"
# refuses on the whole span. A function word missing from this list can only
# LENGTHEN the judged span and refuse it; it can never shorten the span past
# an unreviewed token.
_PREMODIFIER_BOUNDARY_RE = re.compile(
    r"^(?:the|an?|this|that|these|those|all|any|our|your|their|its|my|some|each|"
    r"every|which|what|whose|no|of|among|amongst|for|in|to|by|with|from|across|"
    r"within|on|at|into|onto|over|under|between|per|versus|vs|throughout|about|"
    r"regarding|towards?|via|and|or|but|nor|than|then|as|so|is|are|was|were|be|"
    r"been|being|am|have|has|had|do|does|did|should|would|could|can|will|may|"
    r"might|must|shall|me|us|only|just|also|please|"
    rf"{POPULATION_QUANTIFIER_DIGITS})$",
    re.IGNORECASE,
)
_HYPHEN_CHARS = frozenset("-‐‑‒–—―")
_TOKEN_OPENERS = "(\"'“”‘’["
_TOKEN_CLOSER_RE = re.compile(r"[,;:.!?)\]\"'”’]$")


def is_reviewed_hyphenated_participle_prefix(match: re.Match[str], clause: str) -> bool:
    """Judge the left half of a hyphen-attached formation participle.

    ``match`` is a bound-population capture whose participle sits at
    ``match.start()``. A participle that is not hyphen-attached has no left
    half to judge and passes; one that is must carry either a single
    allow-listed token ("top-ranked", "hand-picked") or a whole reviewed
    ranking object ("opportunity score-ranked", "rate-spread-ranked") in front
    of the hyphen. Anything else -- an unreviewed word, a banked term, an
    empty left half -- fails closed.
    """

    hyphen = match.start() - 1
    if hyphen < 0 or clause[hyphen] not in _HYPHEN_CHARS:
        return True
    left_context = clause[:hyphen]
    if not left_context or not left_context[-1].isalnum():
        return False
    tokens = left_context.split()
    last = tokens[-1].lstrip(_TOKEN_OPENERS)
    if _REVIEWED_PARTICIPLE_PREFIX_RE.fullmatch(last) is not None:
        return True
    phrase: list[str] = []
    for token in reversed(tokens):
        if phrase and _TOKEN_CLOSER_RE.search(token) is not None:
            break
        stripped = token.lstrip(_TOKEN_OPENERS)
        if _PREMODIFIER_BOUNDARY_RE.fullmatch(stripped) is not None or len(phrase) >= 6:
            break
        phrase.append(stripped)
        if stripped != token:
            break
    return _is_reviewed_ranking_object(" ".join(reversed(phrase)))


# ---------------------------------------------------------------------------
# Reader 2: ``... when ranked by <X>`` / ``... when balancing <X>``.
# ---------------------------------------------------------------------------
#
# The weighing verbs mirror ``_REVIEWED_WEIGHING_ADVERBIAL_RE`` (the allow
# shape for the population-noun case) and add the closed optimisation idioms.
# The ranking participles are the formation participles a criterion can bind
# to plus the scoring/evaluation family; the connector is the criterion
# connector the machine already reads after a population noun. With a
# subordinator in front, the adverbial is read wherever it sits; without one,
# ``<participle> <connector> <X>`` is read wherever it sits too, because the
# immediate-outcome branch only sees it when it touches the population noun.
_RANKING_PARTICIPLE = (
    r"(?:ranked|ordered|sorted|prioriti[sz]ed|selected|picked|screened|tiered|grouped|"
    r"scored|rated|weighted|measured|evaluated|assessed|judged|graded|filtered|"
    r"stratified|segmented|bucketed|compared)"
)
_RANKING_CONNECTOR = r"(?:by|on|against|using|according\s+to|based\s+on|in\s+terms\s+of)"
_WEIGHING_VERB = (
    r"(?:balancing|weighing|weighting|considering|trading\s+off|optimi[sz]ing\s+for|"
    r"accounting\s+for|factoring\s+in|controlling\s+for|adjusting\s+for|"
    r"maximi[sz]ing|minimi[sz]ing)"
)
_SUBORDINATOR = r"(?:when|whenever|while|if|once|after|as)"
# The object runs to the end of the clause, or to the start of a coordinated
# question or directive: ", and how large is each segment", ", which offers
# ...", "and explain why ...", ", then contact them". Every stop is a closed
# literal that opens a NEW clause the rest of the machine reads as it does
# today; a conjunct that is not one of them ("rate spread, home equity, and
# rosacea") stays inside the object and is judged with it.
_OBJECT_STOP = (
    r"(?=\s*,?\s+(?:and\s+)?(?:then\s+)?"
    r"(?:how|what|which|where|who|whom|whose|why|explain|tell|describe|show|list|"
    r"summari[sz]e|recommend|rank|compare|break\s+down|give|identify|surface|report|"
    r"highlight|flag|contact|call|email|text|message|reply)\b"
    r"|\s*,\s*(?:then|so)\b|\s*$)"
)
_RANKING_ADVERBIAL_RE = re.compile(
    rf"\b(?:{_SUBORDINATOR}\s+(?:{_RANKING_PARTICIPLE}\s+{_RANKING_CONNECTOR}|{_WEIGHING_VERB})"
    rf"|{_RANKING_PARTICIPLE}\s+{_RANKING_CONNECTOR})\s+"
    rf"(?P<object>[^.!?;:]{{1,160}}?){_OBJECT_STOP}",
    re.IGNORECASE,
)


def contains_unreviewed_ranking_adverbial(clause: str) -> bool:
    """True when a ranking or weighing adverbial names an unreviewed object.

    The caller anchors this on a clause that names an audience or population
    reference; the adverbial itself needs no population noun, which is the
    whole point -- the bound capture only ever judged it when one happened to
    sit inside the list.
    """

    return any(
        not _is_reviewed_ranking_object(match.group("object"))
        for match in _RANKING_ADVERBIAL_RE.finditer(clause)
    )
