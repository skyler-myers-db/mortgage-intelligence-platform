"""The closed lead-in a reviewed audience decision may open with.

``_REVIEWED_AUDIENCE_DECISION_PATTERNS[0..2]`` in
``marketing_selection_criteria`` read "<lead-in> <population> <reviewed
criterion>". Until 2026-09-08 the lead-in was ``(?:[a-z][a-z'-]*|<count>)
{1,10}`` -- any word, up to ten of them -- so an unreviewed premodifier rode
in front of the population noun whenever the criterion BEHIND the noun was
reviewed. Measured on main b1f58bcf: "Who are the top zyrplax borrowers by
opportunity score?" and "Who are the top borrowers in the zyrplax cohort by
opportunity score?" both reached Genie, while "Rank the top zyrplax
borrowers" -- no reviewed tail to ride -- refused. Banked terms (``eczema``,
``hispanic``) still refused through the direct detectors, so the hole was
exactly the unbanked-token class the criterion machine exists to catch, and
``audience_admission_criterion`` returned None for these clauses, so the
admission grammar was never the owner.

What the lead-in tokens are actually FOR, enumerated from every pinned
literal that reached the three patterns (84 guard-family test modules,
4,434 literals, 2026-09-08), each a closed slot below:

1. the directive prefix -- "could you please", "kindly proceed to", "go
   ahead and" (``_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT``);
2. ONE opening frame -- an audience-formation command ("select", "rank",
   "queue", "admit"), a read-only listing verb ("show me", "list",
   "identify", "break down", "review", "build"), an interrogative ("who
   are", "which", "how many"), a priority idiom ("give priority to",
   "reserve the best treatment for"), a declarative campaign subject
   ("this offer gives priority to") or a population subject with its copula
   ("the top borrower candidates overall are");
3. an optional bridge through a first population or destination noun to
   the one the criterion binds -- "admit HOMEOWNERS TO the cohort if ...",
   "build a heloc COHORT OF households with ...", "the top borrowers IN THE
   eligible cohort by ...";
4. determiners ("the", "our", "all of"), the population count ("the top
   50", bare "top" -- a count sizes a population and names nobody, #218),
   and the reviewed premodifiers: the closed adjective set the admission
   grammar shares ("eligible", "marketing-eligible", "highest-scoring",
   "current"), a governed product-intent cohort ("in-the-money",
   "listed-for-sale", "heloc"), a reviewed mortgage attribute ("high
   equity", "high LTV"), a population noun compounding the head
   ("borrower cohort"), a bare USPS state code ("MS borrowers", a closed
   literal list), or a governed place ("Indian Head Park borrowers"),
   captured under the screened ``geoscope_`` prefix so
   :func:`reviewed_fullmatch` judges membership exactly as it does for a
   scope tail -- recognizing a place shape is not authority to admit it.

Everything else -- "zyrplax", "left-handed", "several", "wealthy", an
unbanked health adjective, a title-case pair that is not a governed place
-- breaks the shape: the pattern does not match, the clause falls through
to the bound-population capture and the population-directive tail, and it
refuses as ``unreviewed_criterion``.

Reviewed compounds spell ``[- ]?`` here as everywhere in the allow grammars:
the criterion machine also reads the de-obfuscated variant that deletes
intra-word hyphens, and one tripping variant refuses the whole prompt (#228).
"""

from __future__ import annotations

from backend.schemas.marketing_selection_contextual_criteria import _COREFERENCE_POPULATION
from backend.schemas.marketing_selection_reviewed_places import _LOCATION_TOKEN
from backend.schemas.marketing_selection_vocabulary import (
    _SCOPE_GROUP_PREFIX,
    POPULATION_QUANTIFIER_DIGITS,
    POPULATION_QUANTIFIER_LEAD_WORDS,
    REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT,
)
from backend.schemas.usps import USPS_STATE_CODES

_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT = (
    r"(?:(?:could|would|can|will)\s+you\s+)?"
    r"(?:(?:please|kindly)\s*,?\s+)?"
    r"(?:(?:(?:go|move)\s+ahead\s+and|proceed\s+(?:to|and))\s+)?"
)
# Keep the scanner's leading-capital-I confusable form of ``Insert`` (and, for
# the same reason, ``Include``) aligned with ``marketing_audience_admission``
# across the selection-state grammar: ``ascii_confusable_folds`` rewrites every
# ``I`` as ``l`` and the criterion machine reads that variant too.
_AUDIENCE_FORMATION_COMMAND_FRAGMENT = (
    r"(?:select|choose|pick|target|(?:include|lnclude)|prioritize|favor|rank|order|sort|reserve|"
    r"sequence|group|elevate|tier|screen|queue|advance|shortlist|nominate|enroll|"
    r"move|transfer|admit|place|add|put|assign|route|direct|(?:insert|lnsert)|"
    r"allocate|dispatch)"
)
# Read-only listing verbs. Every one is attested in a pinned literal or in the
# reviewed-analytics openers; none names a criterion.
_LEAD_IN_READ_ONLY_VERB = (
    r"(?:show|list|(?:identify|ldentify)|find|surface|display|pull|return|report|review|"
    r"segment|break\s+down|give|tell|count|compare|analy[sz]e|evaluate|"
    r"recommend|summari[sz]e|export|filter|see|view|describe|build|create|prepare)"
)
_LEAD_IN_ADVERB = r"(?:only|also|just|first|next|now|always)"
_LEAD_IN_FIRST_PERSON = (
    r"(?:let'?s|we\s+(?:should|need\s+to|want\s+to|will|can)|"
    r"(?:i|l)\s+(?:want|need|would\s+like|'?d\s+like)\s+to|help\s+me)"
)
_LEAD_IN_IMPERATIVE_FRAME = (
    rf"(?:(?:{_LEAD_IN_ADVERB}\s+)?(?:{_LEAD_IN_FIRST_PERSON}\s+)?"
    rf"(?:{_AUDIENCE_FORMATION_COMMAND_FRAGMENT}|{_LEAD_IN_READ_ONLY_VERB})"
    r"(?:\s+(?:me|us))?)"
)
_LEAD_IN_INTERROGATIVE_FRAME = (
    r"(?:(?:who|which|what)(?:\s+(?:are|is|ls|were|was))?|how\s+many|"
    r"(?:tell|show)\s+(?:me|us)\s+(?:which|what|who))"
)
_LEAD_IN_PRIORITY_FRAME = (
    r"(?:give\s+(?:(?:top|first|the\s+highest)\s+)?priority\s+to|"
    r"give\s+(?:preferential|priority)\s+(?:consideration|treatment|access)\s+to|"
    r"reserve\s+(?:the\s+)?(?:best|top|preferential)\s+"
    r"(?:treatment|offers?|consideration|access|pricing)\s+for)"
)
_LEAD_IN_DECLARATIVE_FRAME = (
    r"(?:(?:this|that|the|our)\s+(?:campaign|offer|program|list|cohort|audience|segment|queue)\s+"
    r"(?:gives?\s+priority\s+to|is\s+for|goes\s+to|targets|prioriti[sz]es|favou?rs|"
    r"includes|selects|is\s+reserved\s+for))"
)
_LEAD_IN_FRAME = (
    rf"(?:{_LEAD_IN_IMPERATIVE_FRAME}|{_LEAD_IN_INTERROGATIVE_FRAME}|"
    rf"{_LEAD_IN_PRIORITY_FRAME}|{_LEAD_IN_DECLARATIVE_FRAME})"
)
# The determiner list ``_is_reviewed_pre_population_binding`` strips, plus the
# demonstratives the co-reference grammar already treats as population
# references and the partitive ``of`` those quantifiers take ("all of the").
_LEAD_IN_DETERMINER = (
    r"(?:(?:all|any|some|each|which|what)(?:\s+of)?|the|an?|these|those|this|that|every|"
    r"our|your|only)"
)
_LEAD_IN_COUNT = (
    rf"(?:{POPULATION_QUANTIFIER_LEAD_WORDS}(?:\s+{POPULATION_QUANTIFIER_DIGITS})?|"
    rf"{POPULATION_QUANTIFIER_DIGITS})"
)
# The admission grammar's ``_MODIFIERS`` adjectives, plus ``selected``, which
# ``_COREFERENCE_SUBJECT`` already reads as part of a population reference.
_LEAD_IN_REVIEWED_ADJECTIVE = (
    r"(?:reviewed|eligible|qualified|marketing[- ]?eligible|highest[- ]?scoring|"
    r"prospective|current|selected)"
)
# The reviewed-analytics product intents (``_REVIEWED_PRODUCT_INTENT``), spelled
# with their fold images because this grammar, unlike the analytics shapes,
# reads the hyphen-deleted variant. ``(?:i|l)`` heads are the capital-I fold
# image ("In-The-Money", "Investor" in title case), for the same reason.
_LEAD_IN_PRODUCT_INTENT = (
    r"(?:cash[- ]?out|heloc|home[- ]?equity|refi(?:nance)?|rate[- ]?and[- ]?term|purchase|"
    r"listed(?:[- ]?for[- ]?sale)?|(?:i|l)nvestor|multi[- ]?property|retention|recapture|"
    r"(?:i|l)n[- ]?the[- ]?money|high[- ]?equity)"
    r"(?:[\s-]*(?:mortgage|loan|refi|refinance|position|offer))?"
)
_LEAD_IN_BRIDGE_NOUN = (
    rf"(?:{_COREFERENCE_POPULATION}|groups?|cohorts?|audiences?|segments?|populations?|"
    r"campaigns?|lists?|queues?|shortlists?)"
)
# A place capture must never steal a parse: the overall match would still
# succeed with "Inland Empire eligible" or "Texas heloc" in the capture, the
# membership screen would then reject it, and ``re`` does not retry a
# different parse after a successful fullmatch (the optional slot that steals
# a parse). Two closed defenses: the repetition is LAZY, so the shortest place
# that lets the rest of the noun phrase parse wins, and every token excludes
# the closed vocabulary by lookahead, so a determiner, count word, reviewed
# adjective or population noun cannot sit inside the capture at all.
_LEAD_IN_PLACE_TOKEN = (
    rf"(?!(?:{_LEAD_IN_DETERMINER}|{POPULATION_QUANTIFIER_LEAD_WORDS}|"
    rf"{_LEAD_IN_REVIEWED_ADJECTIVE}|{_LEAD_IN_BRIDGE_NOUN}|and|of|with|by|for|to)"
    rf"(?![A-Za-z])){_LOCATION_TOKEN}"
)
# A bare USPS code in front of the population noun -- "MS borrowers", "TX
# borrowers", the product's own per-state rollup wording -- is a closed
# 51-literal list, not a screened capture. The screen deliberately refuses a
# BARE ``ms`` (multiple sclerosis or Mississippi, nothing in the span says
# which), which is right for a scope tail and wrong here: ``MS`` must be
# indistinguishable from ``TX`` in this position
# (``test_marketing_safety_two_letter_ms``), and the health reading is the
# TERM bank's to catch through its carriers ("MS patients" still refuses).
# Codes that start with I also spell their capital-I fold image.
_LEAD_IN_STATE_CODE = (
    "(?:"
    + "|".join(
        sorted({code.lower() for code in USPS_STATE_CODES}
        | {"l" + code[1:].lower() for code in USPS_STATE_CODES if code.startswith("I")})
    )
    + ")"
)


def _lead_in_place(name: str) -> str:
    """A governed-place premodifier, captured for :func:`match_scopes_are_governed`."""

    return (
        rf"(?P<{_SCOPE_GROUP_PREFIX}{name}>"
        rf"{_LEAD_IN_PLACE_TOKEN}(?:\s+{_LEAD_IN_PLACE_TOKEN}){{0,3}}?)"
    )


def _lead_in_population_prefix(name: str) -> str:
    """Determiners, a count and reviewed premodifiers in front of a population noun.

    A factory because the place capture needs a group name unique to its
    embedding site, and the prefix is embedded twice per pattern (once before
    the bridge noun, once before the noun the criterion binds).
    """

    # Product intents and reviewed attributes stack ("in-the-money HELOC
    # borrowers", "high-equity investor borrowers" -- segment intersections
    # the product models), on either side of at most ONE place: a place group
    # inside a repetition would keep only its last iteration, leaving every
    # earlier one unscreened.
    cohort = rf"(?:(?:{_LEAD_IN_PRODUCT_INTENT}|{REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT})\s+){{0,2}}"
    return (
        rf"(?:{_LEAD_IN_DETERMINER}\s+){{0,3}}"
        rf"(?:{_LEAD_IN_COUNT}\s+)?"
        rf"(?:{_LEAD_IN_REVIEWED_ADJECTIVE}\s+){{0,3}}"
        rf"{cohort}"
        rf"(?:(?:{_LEAD_IN_STATE_CODE}|{_lead_in_place(name)})\s+)?"
        rf"{cohort}"
        rf"(?:{_LEAD_IN_REVIEWED_ADJECTIVE}\s+){{0,2}}"
        rf"(?:{_COREFERENCE_POPULATION}\s+)?"
    )


# A definitional statement about a closed population -- "The top borrower
# candidates overall are those with the highest opportunity scores" (a live
# Genie narrative, 2026-08-06) -- opens with the population as SUBJECT and a
# copula; the criterion then binds ``those``. Every slot is the closed prefix
# above plus the closed copula, so no criterion can ride in the subject.
_LEAD_IN_COPULAR_SUBJECT_FRAME = (
    rf"(?:{_lead_in_population_prefix('lead_in_subject')}{_LEAD_IN_BRIDGE_NOUN}\s+"
    r"(?:overall\s+)?(?:are|is|were|was|remain|remains))"
)
AUDIENCE_LEAD_IN_FRAGMENT = (
    rf"{_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT}"
    rf"(?:{_LEAD_IN_FRAME}|{_LEAD_IN_COPULAR_SUBJECT_FRAME})\s+"
    rf"(?:{_lead_in_population_prefix('lead_in_bridge')}{_LEAD_IN_BRIDGE_NOUN}\s+"
    r"(?:(?:i|l)n|of|from|within|among|to|(?:i|l)nto|onto|for)\s+)?"
    rf"{_lead_in_population_prefix('lead_in')}"
)
