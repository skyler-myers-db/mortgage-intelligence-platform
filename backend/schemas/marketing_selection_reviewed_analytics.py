"""Reviewed read-only analytics question shapes.

Closed-vocabulary grammars for analytics questions the product itself asks
(offer mix by segment, ranked product-intent cohorts, permit source-gap
probes). Split from the criterion machine when it crossed the size gate
(2026-08-08); the vocabulary and shapes are unchanged. Every fragment stays
closed so an unknown criterion can never ride an approved shape.
"""

from __future__ import annotations

import re

_REVIEWED_ANALYTIC_POPULATION = (
    r"(?:(?:(?:in[- ]the[- ]money|listed|refinance[- ]ready|retention[- ]risk|"
    r"marketing[- ]eligible|investor|equity[- ]rich|highest[- ]scoring)\s+)?"
    r"(?:borrowers?|leads?|applicants?|homeowners?|customers?|cohorts?|populations?))"
)
_REVIEWED_ANALYTIC_DIMENSION = (
    r"(?:(?:current\s+coverage|covered)\s+)?(?:states?|count(?:y|ies)|"
    r"zip(?:\s+codes?)?|postal\s+codes?|markets?|metros?|"
    r"segments?|lead\s+scores?|opportunity\s+scores?|equity|ltv|loan[- ]to[- ]value|"
    r"rate[- ]spreads?|listing\s+time(?:\s+on\s+market)?)"
)
_REVIEWED_ANALYTIC_MEASURE = (
    r"(?:listing\s+time\s+on\s+market|lead\s+score|opportunity\s+score|"
    r"borrower\s+count|equity(?:\s+percentage)?|ltv|loan[- ]to[- ]value|rate[- ]spread)"
)
_REVIEWED_ANALYTIC_LOCATION = (
    r"(?:\s+(?:in|across)\s+(?:the\s+current\s+(?:coverage|portfolio)|"
    r"[A-Za-z]{2}|[A-Z][A-Za-z' -]{2,40}))?"
)
# Governed Module 0 product-intent cohorts. Closed list: these are offer
# codes/segment names the product models, never free-text criteria.
_REVIEWED_PRODUCT_INTENT = (
    r"(?:cash[- ]out|heloc|home[- ]equity|refi(?:nance)?|rate[- ]and[- ]term|"
    r"purchase|listed(?:[- ]for[- ]sale)?|investor|multi[- ]property|"
    r"retention|recapture|in[- ]the[- ]money|high[- ]equity)"
)
# Signal columns a planned sub-analysis may name alongside a ranked cohort.
# Closed: every entry is a governed gold column or Module 0 domain signal
# (CLAUDE.md domain rules), never free text.
_REVIEWED_ANALYTIC_SIGNAL = (
    r"(?:opportunity\s+scores?|lead\s+scores?|rate\s+spreads?|equity(?:\s+percentage)?|"
    r"ltv|loan[- ]to[- ]value|key\s+triggers?|triggers?|recommended\s+offers?|"
    r"next[- ]best\s+offers?|segment\s+memberships?|segments?|listing\s+status|"
    r"competitor\s+liens?|listed\s+for\s+sale|investor\s+status|"
    r"retention\s+risk|heloc\s+propensity)"
)
_REVIEWED_ANALYTIC_SIGNAL_LIST = (
    rf"{_REVIEWED_ANALYTIC_SIGNAL}(?:\s*,?\s*(?:and\s+)?{_REVIEWED_ANALYTIC_SIGNAL})*"
)
# "the top 20 (eligible) borrowers" — the cohort noun a planned deep analysis
# names in nearly every sub-question.
_REVIEWED_TOP_COHORT = (
    r"(?:the\s+)?top\s+(?:[0-9]{1,3}\s+)?"
    r"(?:eligible\s+|marketing[- ]eligible\s+|highest[- ]scoring\s+)?"
    r"(?:borrowers?|leads?|candidates?|opportunities)"
)
_REVIEWED_WHOLE_POPULATION = (
    r"(?:the\s+)?(?:full|entire|whole|overall|broader)\s+"
    r"(?:eligible\s+)?(?:borrower\s+)?(?:population|pool|universe|portfolio|group)"
)

_REVIEWED_READ_ONLY_ANALYTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        # Ranked shortlist plus its governed signal columns ("who are the top
        # 20 eligible borrowers ranked by opportunity score, and what are
        # their rate spread, equity percentage, key triggers, and recommended
        # offer?"). Captured live 2026-08-10 from the governed space's OWN
        # deep-analysis plan: the criterion machine read it as an unreviewed
        # criterion, three of seven planned sub-questions were dropped, the
        # plan fell under the deep floor, and the sweep aborted silently — the
        # user saw a single-screen answer instead of the deep decomposition.
        r"^(?:who|what)\s+(?:are|is)\s+"
        rf"{_REVIEWED_TOP_COHORT}"
        r"(?:\s+ranked\s+by\s+" + _REVIEWED_ANALYTIC_SIGNAL_LIST + r")?"
        r"(?:\s*,?\s*and\s+what\s+(?:are|is)\s+(?:their|its)\s+"
        + _REVIEWED_ANALYTIC_SIGNAL_LIST
        + r")?\s*\??$",
        re.IGNORECASE,
    ),
    re.compile(
        # Percentile placement of a ranked cohort within the population
        # ("what is the percentile rank of the top 20 borrowers for
        # opportunity score, rate spread, and equity percentage within the
        # entire eligible borrower pool?"). Same live capture.
        r"^what\s+(?:is|are)\s+the\s+"
        r"(?:percentile\s+ranks?|percentiles?|rank(?:ings?)?)\s+of\s+"
        rf"{_REVIEWED_TOP_COHORT}\s+"
        r"(?:for|on|by|across)\s+"
        + _REVIEWED_ANALYTIC_SIGNAL_LIST
        + rf"(?:\s+(?:within|in|against|compared\s+to)\s+{_REVIEWED_WHOLE_POPULATION})?"
        r"\s*\??$",
        re.IGNORECASE,
    ),
    re.compile(
        # Offer strategy by segment ("which offer should we lead with for each
        # segment, and why?"). The audience grammar read the affirmative
        # "lead with ... for each segment" as an unreviewed audience decision;
        # it is the product's core read-only offer-mix question. Live persona
        # audit 2026-08-07 (marketing-leader).
        r"^(?:which|what)\s+(?:next[- ]best\s+)?offers?\s+"
        r"(?:should|do|would)\s+(?:we|i|the\s+team)\s+"
        r"(?:lead\s+with|use|present|recommend|make|pitch|prioriti[sz]e)\s+"
        r"(?:for|to|with)\s+(?:each|every|the|our)?\s*"
        rf"(?:{_REVIEWED_ANALYTIC_DIMENSION}|{_REVIEWED_ANALYTIC_POPULATION})"
        r"(?:\s*,?\s*and\s+why)?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Ranked product-intent cohort with a per-row rationale ("rank the top
        # cash-out candidates in Texas and explain why each one qualifies").
        # The intent vocabulary is closed, so an unknown criterion cannot ride
        # this shape. Live persona audit 2026-08-07 (sales-manager).
        r"^(?:rank|show|list|give\s+me|surface|prioriti[sz]e)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:top\s+(?:[0-9]{1,3}\s+)?)?"
        # One or two stacked intent tokens ("in-the-money refi", "purchase
        # mortgage") — still drawn from the same closed vocabulary, so an
        # unknown criterion cannot ride the second slot. Live persona audit
        # 2026-08-07 (co-pilot flagship objective).
        # The intent token is OPTIONAL: "rank the top opportunities" names no
        # criterion at all, so there is no unreviewed criterion to smuggle —
        # it ranks by the governed score. An unknown word in this slot still
        # fails to match (it is neither an intent token nor a cohort noun),
        # so "rank the top zyrplax borrowers" keeps falling through to the
        # strict criterion machine. Live persona probe 2026-08-10
        # (sales-manager): the whole question was refused because of it.
        rf"(?:{_REVIEWED_PRODUCT_INTENT}(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|mortgage|loan))?\s+)?"
        r"(?:candidates?|borrowers?|leads?|opportunities)"
        rf"{_REVIEWED_ANALYTIC_LOCATION}"
        r"(?:\s*,?\s*and\s+(?:explain|tell\s+me|describe|show)\s+"
        r"(?:me\s+)?why\s+(?:each|every)(?:\s+one)?\s+"
        r"(?:qualifies|ranks?|scores?|is\s+(?:a\s+)?(?:strong|good)(?:\s+candidate)?))?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Reviewed-population cohort carrying a closed product-intent
        # with-clause ("show customers with an in-the-money refi", "show
        # investor borrowers with multiple properties" — the Owner Link
        # domain rule). The with-clause alternatives are a closed set; a
        # free-text criterion ("with zyrplax", "with eczema") does not match
        # and falls through to the strict criterion machine. Live persona
        # audit 2026-08-07 (co-pilot).
        r"^(?:show|find|list|rank|surface|give\s+me)\s+(?:me\s+)?(?:the\s+)?"
        rf"(?:{_REVIEWED_PRODUCT_INTENT}\s+)?"
        r"(?:customers?|borrowers?|leads?|candidates?|prospects?|homeowners?|owners?)\s+"
        r"with\s+(?:"
        rf"(?:(?:a|an|the)\s+)?{_REVIEWED_PRODUCT_INTENT}"
        rf"(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|mortgage|loan|refi|refinance|position|offer|opportunity))?"
        r"|multiple\s+properties"
        r")"
        rf"{_REVIEWED_ANALYTIC_LOCATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        # Building-permit data is an explicit product source gap. This closed
        # read-only query must reach the Genie source-gap policy rather than
        # being mislabeled as protected-class targeting merely because it
        # uses the product noun ``candidates``.
        r"^(?:show|list|find|identify)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:heloc|home[- ]equity)\s+candidates?\s+with\s+"
        r"(?:recent|filed)\s+(?:building\s+)?permits?\s+and\s+"
        r"(?:strong|high)\s+equity$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:show|list|count)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:approved|assigned|queued)\s+leads?\s+"
        r"(?:that|who)\s+(?:have|has)\s+not\s+been\s+"
        r"(?:touched|contacted|called)\s+in\s+[0-9]{1,3}\s+days?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:chart|plot|graph|visualize|display|show|list|count|rank|order|group|compare|"
        r"break\s+down)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:(?:top|bottom)\s+(?:[0-9]{1,3}|ten|twenty(?:[- ]five)?)\s+)?"
        rf"{_REVIEWED_ANALYTIC_POPULATION}\s+"
        r"(?:(?:by|grouped\s+by|ordered\s+by|ranked\s+by)\s+)"
        rf"{_REVIEWED_ANALYTIC_DIMENSION}{_REVIEWED_ANALYTIC_LOCATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what\s+is|calculate|show|display|report|compare)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:average|median|mean|total|minimum|maximum|distribution\s+of)\s+"
        rf"{_REVIEWED_ANALYTIC_MEASURE}\s+(?:for|across)\s+(?:the\s+)?"
        rf"{_REVIEWED_ANALYTIC_POPULATION}\s+by\s+"
        rf"{_REVIEWED_ANALYTIC_DIMENSION}{_REVIEWED_ANALYTIC_LOCATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:which|what)\s+{_REVIEWED_ANALYTIC_DIMENSION}\s+"
        r"(?:leads?|ranks?\s+(?:highest|lowest)|has\s+(?:the\s+)?"
        r"(?:highest|lowest|most|fewest)\s+(?:count|score|borrowers?|leads?))$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^how\s+(?:has|have)\s+(?:the\s+)?{_REVIEWED_ANALYTIC_POPULATION}\s+"
        r"(?:moved|changed|shifted|trended)(?:\s+(?:recently|over\s+time|this\s+week))?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Superlative ranked-shortlist directive ("rank the strongest
        # candidates for outreach", "pick the very best refinance
        # candidates", "give me a curated list of the highest-potential
        # borrowers"). Every slot is a closed alternation — superlatives,
        # product intents, population nouns, and purposes — so an unknown
        # criterion cannot ride the shape. Live capture 2026-08-08: the
        # audience-formation grammar failed this closed as an unreviewed
        # audience decision, refusing a fundamental deep-analysis ask.
        r"^(?:and\s+)?(?:rank|pick|surface|identify|find|curate|list|show|give\s+me|"
        r"determine)\s+(?:me\s+)?"
        r"(?:a\s+(?:curated\s+)?list\s+of\s+)?(?:the\s+)?"
        r"(?:very\s+|absolute\s+|single\s+)?"
        r"(?:top|best|strongest|highest[- ]potential|most\s+promising)\s+"
        r"(?:[0-9]{1,3}\s+)?(?:potential\s+)?"
        rf"(?:{_REVIEWED_PRODUCT_INTENT}\s+)?"
        r"(?:candidates?|borrowers?|leads?|opportunities|prospects?)"
        r"(?:\s+(?:for\s+(?:outreach|contact|review|follow[- ]up)|to\s+contact))?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Best-offer-per-borrower directive ("recommend the best offer for
        # each with reasoning", "what the absolute best curated offer for
        # each would be and why"). The population tokens and the
        # answer-format tail are closed; free-text criteria fall through to
        # the strict criterion machine. Same 2026-08-08 live capture.
        r"^(?:and\s+)?(?:recommend|determine|identify|pick|choose|select|suggest|"
        r"tell\s+me|what)\s+"
        r"(?:what\s+)?(?:the\s+)?(?:absolute\s+)?(?:best|ideal|right|optimal)\s+"
        r"(?:curated\s+|fit\s+)?offers?\s+(?:for|to)\s+(?:each|every)"
        r"(?:\s+(?:one|borrower|candidate|lead|prospect|customer))?"
        r"(?:\s+would\s+be)?"
        r"(?:\s+with\s+(?:full\s+|clear\s+|detailed\s+)?"
        r"(?:reasoning|rationale|justification|explanations?))?"
        r"(?:\s*,?\s*and\s+why)?$",
        re.IGNORECASE,
    ),
)


_REVIEWED_ANALYSIS_PREAMBLE_RE = re.compile(
    # Closed analysis preamble ("Analyze the full dataset of eligible
    # borrowers and <directive>"). Compound clauses defeated the
    # ``^…$``-anchored reviewed shapes below, so a reviewed directive
    # prefixed by an analysis framing failed closed (live capture,
    # 2026-08-08). Every slot is a closed alternation — an unknown
    # population or criterion does not match, is not stripped, and the
    # clause still fails closed.
    r"^(?:"
    # Scope framing with no verb ("Across the entire portfolio, …").
    r"across\s+(?:the\s+)?(?:entire|whole|full|current)?\s*"
    r"(?:portfolio|book|footprint|coverage|datasets?|pipelines?)\s*,\s*"
    r"|"
    r"(?:(?:comprehensively|deeply|thoroughly|fully)\s+)?"
    # Direct verb ("Comprehensively analyze …") or light-verb form
    # ("Do a deep analysis of …").
    r"(?:analyze|analyse|review|examine|study|assess|explore|deep[- ]dive(?:\s+into)?|"
    r"(?:do|run|perform|conduct|complete)\s+(?:a|an)\s+"
    r"(?:(?:deep|full|comprehensive|complete|thorough)\s+)?"
    r"(?:analysis|review|assessment|study|deep[- ]dive)\s+of)\s+"
    r"(?:(?:the|our|this|every|all)\s+)?(?:(?:full|entire|complete|whole)\s+)?"
    r"(?:(?:eligible|marketing[- ]eligible|reviewed|contact[- ]eligible)\s+)?"
    r"(?:datasets?|data\s*sets?|data|portfolios?|books?|pipelines?|populations?|"
    r"borrowers?|leads?|customers?|prospects?|homeowners?)?"
    r"(?:\s+of\s+(?:(?:all|our|the)\s+)?(?:(?:eligible|marketing[- ]eligible|reviewed|"
    r"contact[- ]eligible)\s+)?(?:borrowers?|leads?|customers?|prospects?|homeowners?))?"
    r"\s*,?\s*and\s+"
    r")",
    re.IGNORECASE,
)
_REVIEWED_WHY_ASSESSMENT_PREAMBLE_RE = re.compile(
    # Closed why-assessment preamble ("Evaluate why each borrower is an
    # especially good candidate, and <directive>"). Same compound-clause
    # problem and the same posture as the analysis preamble above: the
    # assessment vocabulary is closed, so an unknown criterion inside the
    # segment does not match, is not stripped, and the clause fails closed.
    r"^(?:evaluate|explain|justify|assess|describe|tell\s+me|"
    r"walk\s+(?:me\s+)?through)\s+(?:me\s+)?"
    r"(?:why\s+(?:each|every)(?:\s+(?:one|borrower|candidate|lead|prospect|customer))?\s+"
    r"(?:is|would\s+be|makes)\s+(?:an?\s+)?"
    r"(?:especially\s+|particularly\s+|very\s+)?"
    r"(?:strong|good|great|excellent|prime|ideal|top|promising)\s+"
    r"(?:candidate|prospect|fit|match|choice|opportunity)|"
    r"each\s+selection|"
    r"the\s+rationale\s+for\s+each(?:\s+(?:one|borrower|candidate|selection))?)"
    r"\s*,?\s*and\s+",
    re.IGNORECASE,
)
